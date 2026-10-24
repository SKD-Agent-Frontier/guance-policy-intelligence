"""仅在专用库线程中使用的检索和原文业务逻辑。"""
import hashlib
import re
import time
from filelock import FileLock, Timeout

from .errors import BackendError, InvalidArgument, KBNoText, KBNotFound, RetrievalTimeout
from .models import ChunkSpan, ContextSpan, HitV03, PassageV03
from .media import MediaLibrary, SOURCE_TYPES
from .retrieval import DenseReader, LexicalReader, QueryEncoder, fuse_sources
from .storage import ReadStore

FYP = {f"{n}五": f"第{n}个五年规划" for n in ("十一", "十二", "十三", "十四", "十五")}


def normalize_fyp(value):
    if value is None:
        return None
    key = "".join(c for c in value if not c.isspace() and c not in '\"\'“”‘’')
    return FYP.get(key, value)


class StoreBackend:
    def __init__(self, options):
        self.options = options
        self.store = self.lexical = self.dense = self.encoder = None
        self.media = None
        self.lock = FileLock(str(options.store_project / ".runtime/project.lock"))
        try:
            self.lock.acquire(timeout=0)
        except Timeout:
            raise BackendError("本地库正在被其他运行时或建库命令使用") from None
        try:
            # 保留半初始化实例，启动核查失败时也能在所属线程关闭SQLite。
            self.store = ReadStore.__new__(ReadStore)
            self.store.__init__(options.store_project)
            self.media = MediaLibrary(self.store, options)
            if options.mode in {"bm25", "hybrid"}:
                self.lexical = LexicalReader(options.store_project, self.store.root / "bm25",
                                             self.store.generation, options.cache_directory / "jieba")
                self.lexical_ids = set(self.lexical.index.ids)
                required = {rid for rid, row in self.store.all_members.items() if row["record"].get("representation_type", "text") == "text"}
                if not required <= self.lexical_ids:
                    raise BackendError("BM25缺少当前发布的文本或OCR表示")
            if options.mode in {"dense", "hybrid"}:
                self.dense = DenseReader(self.store.root / "qdrant", self.store.manifest["collection"])
                if self.dense.count != self.store.all_member_count:
                    raise BackendError("向量集合与冻结发布成员数量不一致")
                self.encoder = QueryEncoder.__new__(QueryEncoder)
                self.encoder.__init__(options, self.store.manifest)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _deadline(deadline):
        if time.monotonic() > deadline:
            raise RetrievalTimeout("检索执行超过时限")

    @staticmethod
    def _identifier(value, name, optional=False):
        if optional and value is None:
            return
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise InvalidArgument(name + "格式不正确")

    def search(self, query, area, fyp, top_k, *, source_types=None, deadline):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise InvalidArgument("query须为1–200字符")
        for key, value in (("area", area), ("fyp", fyp)):
            if value is not None and (not isinstance(value, str) or len(value) > 50):
                raise InvalidArgument(key + "须为不超过50字符的字符串")
        if type(top_k) is not int or not 1 <= top_k <= self.options.top_k_max:
            raise InvalidArgument("top_k超出配置范围")
        if source_types is None:
            source_types = SOURCE_TYPES
        elif (not isinstance(source_types, (list, tuple)) or not source_types
              or any(not isinstance(s, str) or s not in SOURCE_TYPES for s in source_types)):
            raise InvalidArgument("source_types须为非空来源类型列表")
        source_types = set(source_types)
        query = query.strip()
        area = (area.strip() or None) if area is not None else None
        fyp = normalize_fyp(fyp.strip() or None) if fyp is not None else None
        members = self.store.all_members
        allowed = {rid for rid, row in members.items()
                   if row["record"]["source_type"] in source_types
                   and (area is None or (row["payload"].get("area_name") and area in row["payload"]["area_name"]))
                   and (fyp is None or row["payload"].get("period_key") == fyp)}
        self._deadline(deadline)
        if not allowed:
            return []
        limit, channels = max(20, top_k * 4), {}
        if self.lexical:
            channels["bm25"] = self.lexical.search(query, limit, allowed & self.lexical_ids)
        if self.dense:
            vector = self.encoder.vector(query)
            self._deadline(deadline)
            versions = {members[r]["record"]["document_version_id"] for r in allowed}
            channels["dense"] = self.dense.search(vector, limit, versions, source_types)
        for hits in channels.values():
            for hit in hits:
                if hit["id"] not in allowed:
                    raise BackendError("索引返回未发布或不满足条件的来源")
                if "point_id" in hit and hit["point_id"] != members[hit["id"]]["record"]["point_id"]:
                    raise BackendError("向量身份与来源映射不一致")
        ranked = fuse_sources(channels, members)
        results, seen_contexts, remaining = [], set(), self.options.max_search_chars
        for source, score, rid in ranked:
            self._deadline(deadline)
            row = members[rid]
            if remaining <= 0:
                if results:
                    results[-1].quality_warnings.append("search_evidence_budget_reached")
                break
            if row["record"]["source_type"] != "text_chunk":
                result = self.media.hit(row, score, remaining)
                if result is not None:
                    results.append(result)
                    remaining -= len(result.content) + sum(len(c.content) for c in result.ocr_contexts)
                    if len(results) >= top_k:
                        break
                continue
            doc, chunk = self.store.resolve(row["payload"]["planid"], source, row["record"]["document_id"], self.store.generation)
            section = self.store.section(doc, chunk=chunk)
            span = chunk["source_segments"][0]
            a, b = self._preview_range(chunk, row["record"])
            if b - a > remaining:
                if results:
                    results[-1].quality_warnings.append("search_evidence_budget_reached")
                break
            warnings = list(chunk.get("reasons", []))
            if chunk.get("status") != "ready":
                warnings.append("upstream_status:" + chunk.get("status", "unknown"))
            contexts, relations, context_warnings = self._contexts(doc, chunk, min(self.options.max_context_chars, remaining - (b - a)), seen_contexts)
            warnings.extend(context_warnings)
            result = HitV03(**self.store.metadata(doc, section), chunk_id=source, parent_id=chunk["parent_id"],
                            representation_id=rid, chunk_char_start=span["start"] - doc.origin,
                            chunk_char_end=span["end"] - doc.origin, char_start=a - doc.origin,
                            char_end=b - doc.origin, content=doc.text[a:b], score=score,
                            truncated=a != span["start"] or b != span["end"], contexts=contexts,
                            media_sources=self.media.references(doc, a, b),
                            required_relation_ids=relations, quality_warnings=list(dict.fromkeys(warnings)))
            results.append(result)
            remaining -= len(result.content) + sum(len(c.content) for c in contexts)
            if len(results) >= top_k:
                break
        return results

    def _preview_range(self, chunk, record):
        span = chunk["source_segments"][0]
        a, end = span["start"], span["end"]
        fragment = record.get("embedding_fragment")
        view = chunk.get("retrieval_text", "")
        if fragment and view.endswith(span["text"]):
            # 输入分片坐标属于检索视图；只有经哈希核对的连续原文后缀才可映射。
            if hashlib.sha256(view.encode()).hexdigest() != fragment["retrieval_text_hash"]:
                raise BackendError("检索分片视图哈希不一致")
            piece = view[fragment["start"]:fragment["end"]]
            if hashlib.sha256(piece.encode()).hexdigest() != fragment["text_hash"]:
                raise BackendError("检索输入分片哈希不一致")
            prefix = len(view) - len(span["text"])
            left, right = max(0, fragment["start"] - prefix), min(len(span["text"]), fragment["end"] - prefix)
            if left < right:
                a, end = span["start"] + left, span["start"] + right
        return a, min(end, a + self.options.snippet_chars)

    def _contexts(self, doc, chunk, budget, seen):
        contexts, warnings = [], []
        required = list(chunk.get("required_relation_ids", []))
        found_relations = set()
        candidates = [(chunk["id"] + ":context:" + str(i), chunk["document_id"], [span], span.get("role", "condition"))
                      for i, span in enumerate(chunk.get("context_segments", []))]
        rows = self.store.connection.execute("SELECT id,target_id,status FROM links WHERE source_id IN (?,?)", (chunk["id"], chunk["parent_id"]))
        for row in rows:
            relation = self.store.object(row["id"])
            if not relation or not relation.get("required", False):
                continue
            found_relations.add(row["id"])
            required.append(row["id"])
            if row["status"] != "resolved" or not row["target_id"]:
                warnings.append("unresolved_relation:" + row["id"])
                continue
            target = self.store.object(row["target_id"])
            if not target:
                warnings.append("missing_context:" + row["target_id"])
                continue
            spans = target.get("source_segments")
            if spans is None and {"start", "end", "text"} <= set(target):
                spans = [target]
            if spans:
                candidates.append((target["id"], target["document_id"], spans, relation.get("kind", "condition")))
            else:
                warnings.append("missing_context_span:" + row["target_id"])
        warnings.extend("missing_relation:" + oid for oid in required if oid not in found_relations)
        for oid in self.store.context_parents.get(chunk["id"], []):
            context = self.store.object(oid)
            candidates.append((oid, context["document_id"], context["source_segments"], "parent_context"))
        parent = self.store.object(chunk["parent_id"])
        candidates.append((parent["id"], parent["document_id"], parent["source_segments"], "parent"))
        for oid, did, spans, role in candidates:
            target_doc = self.store.document(did)
            for span in spans:
                a, b = span["start"], span["end"]
                key = (did, a, b)
                if key in seen:
                    continue
                if not target_doc.origin <= a < b <= len(target_doc.text) or target_doc.text[a:b] != span["text"]:
                    raise BackendError("上下文范围不能逐字还原原文")
                if b - a > budget:
                    warnings.append("context_budget_exceeded:" + oid)
                    continue
                contexts.append(ContextSpan(source_id=oid, document_id=did, source_hash=target_doc.data["source_hash"],
                                            generation_id=target_doc.generation, body_origin=target_doc.origin,
                                            char_start=a - target_doc.origin, char_end=b - target_doc.origin,
                                            content=target_doc.text[a:b], role=role))
                seen.add(key)
                budget -= b - a
        return contexts, list(dict.fromkeys(required)), list(dict.fromkeys(warnings))

    def read(self, planid, section_idx, max_chars, *, chunk_id=None, source_id=None, offset=0,
             document_id=None, generation_id=None, deadline, _complete=False):
        self._identifier(planid, "planid")
        for name, value in (("chunk_id", chunk_id), ("source_id", source_id), ("document_id", document_id), ("generation_id", generation_id)):
            self._identifier(value, name, optional=True)
        if section_idx is not None and (type(section_idx) is not int or section_idx < 0):
            raise InvalidArgument("section_idx须为非负整数")
        if type(offset) is not int or offset < 0:
            raise InvalidArgument("offset须为非负整数")
        if type(max_chars) is not int or max_chars <= 0 or (not _complete and max_chars > self.options.max_read_chars):
            raise InvalidArgument("max_chars超出服务预算")
        if source_id is not None:
            if chunk_id is not None and source_id != chunk_id:
                raise InvalidArgument("chunk_id与source_id不能选择两个不同来源")
            if source_id.startswith("chunk_"):
                chunk_id = source_id
            else:
                self._deadline(deadline)
                result = self.media.evidence(source_id, planid=planid, section_idx=section_idx,
                                             document_id=document_id, generation_id=generation_id,
                                             offset=offset, max_chars=max_chars)
                self._deadline(deadline)
                return result
        doc, chunk = self.store.resolve(planid, chunk_id, document_id, generation_id)
        self._deadline(deadline)
        if doc.origin == len(doc.text):
            raise KBNoText(planid, doc.data["metadata"].get("title") or "")
        section = self.store.section(doc, section_idx, chunk)
        if chunk is None and section_idx is None:
            if offset:
                raise InvalidArgument("目录请求offset必须为0")
            directory = []
            for idx, s in enumerate(self.store.sections[doc.data["id"]]):
                a = self.store.section_start(doc, s)
                metadata = self.store.metadata(doc, s)
                directory.append(dict(idx=idx, section_id=s["id"], path=metadata["section_path"],
                                      char_start=a - doc.origin, char_end=s["end"] - doc.origin, chars=s["end"] - a))
            return PassageV03(**self.store.metadata(doc), read_scope="index", is_index=True,
                              content="", char_start=None, char_end=None, sections=directory,
                              media_sources=self.media.references(doc, doc.origin, len(doc.text), include_unanchored=True))
        whole = chunk["source_segments"][0] if chunk else {"start": self.store.section_start(doc, section), "end": section["end"]}
        if offset >= whole["end"] - whole["start"]:
            raise InvalidArgument("offset超出所选非空块或章节范围")
        a, end = whole["start"] + offset, whole["end"]
        b = min(end, a + max_chars)
        intersections = []
        candidates = [(whole["start"], whole["end"], chunk["id"])] if chunk else [x for s in self.store.sections[doc.data["id"]] for x in self.store.section_chunks[s["id"]]]
        for start, stop, cid in sorted(candidates):
            left, right = max(start, a), min(stop, b)
            if left >= right:
                continue
            source = self.store.object(cid)
            own_section = self.store.section(doc, chunk=source)
            own_meta = self.store.metadata(doc, own_section)
            intersections.append(ChunkSpan(chunk_id=cid, chunk_char_start=start - doc.origin, chunk_char_end=stop - doc.origin,
                                           char_start=left - doc.origin, char_end=right - doc.origin,
                                           complete=left == start and right == stop,
                                           section_id=own_section["id"], section_path=own_meta["section_path"]))
        contexts, required, warnings = self._contexts(doc, chunk, self.options.max_context_chars, set()) if chunk else ([], [], [])
        if chunk and chunk.get("status") != "ready":
            warnings.append("upstream_status:" + chunk.get("status", "unknown"))
        self._deadline(deadline)
        return PassageV03(**self.store.metadata(doc, section), read_scope="chunk" if chunk else "section",
                          chunk_id=chunk_id, parent_id=chunk["parent_id"] if chunk else None,
                          chunk_char_start=whole["start"] - doc.origin if chunk else None,
                          chunk_char_end=whole["end"] - doc.origin if chunk else None,
                          section_char_start=self.store.section_start(doc, section) - doc.origin,
                          section_char_end=section["end"] - doc.origin, content=doc.text[a:b],
                          char_start=a - doc.origin, char_end=b - doc.origin, truncated=b < end,
                          next_offset=b - whole["start"] if b < end else None, chunks=intersections,
                          media_sources=self.media.references(doc, a, b),
                          contexts=contexts, required_relation_ids=required,
                          quality_warnings=list(dict.fromkeys((chunk.get("reasons", []) if chunk else []) + warnings)))

    def get_chunk_source(self, chunk_id, *, document_id=None, generation_id=None, deadline):
        self._identifier(chunk_id, "chunk_id")
        chunk = self.store.object(chunk_id)
        if not chunk or not chunk_id.startswith("chunk_"):
            raise KBNotFound(chunk_id=chunk_id)
        doc = self.store.document(chunk["document_id"])
        span = chunk["source_segments"][0]
        return self.read(doc.data["planid"], None, span["end"] - span["start"],
                         chunk_id=chunk_id, document_id=document_id, generation_id=generation_id,
                         deadline=deadline, _complete=True)

    def verify_quote(self, chunk_id, quote, observed_ranges, *, document_id=None, generation_id=None, deadline):
        if not isinstance(quote, str) or not quote:
            raise InvalidArgument("quote须为非空原句")
        if not isinstance(observed_ranges, (list, tuple)):
            raise InvalidArgument("observed_ranges须为码点起止对列表")
        source = self.get_chunk_source(chunk_id, document_id=document_id, generation_id=generation_id, deadline=deadline)
        merged = []
        for item in observed_ranges:
            if not isinstance(item, (list, tuple)) or len(item) != 2 or any(type(x) is not int for x in item):
                raise InvalidArgument("observed_ranges须为码点起止对")
            a, b = item
            if not source.chunk_char_start <= a < b <= source.chunk_char_end:
                raise InvalidArgument("已见范围不属于该完整块")
        for a, b in sorted(observed_ranges):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(b, merged[-1][1])
            else:
                merged.append([a, b])
        return any(quote in source.content[a - source.chunk_char_start:b - source.chunk_char_start] for a, b in merged)

    def stats(self, *, deadline):
        return dict(generation_id=self.store.generation, revision=self.store.revision, mode=self.options.mode,
                    api_version="proposal-v0.4", supported_source_types=sorted(SOURCE_TYPES),
                    media_coverage_partial=bool(self.store.manifest.get("partial", False)),
                    documents=len(self.store.current_documents), native_representations=len(self.store.members),
                    media_representations=len(self.store.all_members) - len(self.store.members),
                    media_index_loads=len(self.media.generations), image_assets_verified=self.media.image_checks,
                    metadata_loads=1, bm25_loads=self.lexical.loads if self.lexical else 0,
                    dense_loads=self.dense.loads if self.dense else 0, snapshot_loads=self.store.snapshot_loads,
                    object_primary_key_lookups=self.store.object_lookups,
                    model_calls=self.encoder.calls if self.encoder else 0,
                    query_cache_hits=self.encoder.cache_hits if self.encoder else 0)

    def read_image(self, source_id, *, variant="original", document_id=None, generation_id=None, deadline):
        for name, value in (("source_id", source_id), ("document_id", document_id), ("generation_id", generation_id)):
            self._identifier(value, name, optional=name != "source_id")
        self._deadline(deadline)
        result = self.media.image_data(source_id, variant=variant, document_id=document_id, generation_id=generation_id)
        self._deadline(deadline)
        return result

    def verify_media_quote(self, source_id, quote, observed_ranges, *, document_id=None, generation_id=None, deadline):
        for name, value in (("source_id", source_id), ("document_id", document_id), ("generation_id", generation_id)):
            self._identifier(value, name, optional=name != "source_id")
        if not isinstance(quote, str) or not quote or not isinstance(observed_ranges, (list, tuple)):
            raise InvalidArgument("需要非空引句及OCR已见范围列表")
        source = self.media.evidence(source_id, document_id=document_id, generation_id=generation_id, max_chars=2**63 - 1)
        if source.ocr_range is None:
            return False
        span, merged = source.ocr_range, []
        for item in observed_ranges:
            if (not isinstance(item, (list, tuple)) or len(item) != 2 or any(type(x) is not int for x in item)
                    or not span.full_char_start <= item[0] < item[1] <= span.full_char_end):
                raise InvalidArgument("已见范围必须属于同版OCR转录，不能使用Markdown正文坐标")
        for a, b in sorted(observed_ranges):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(b, merged[-1][1])
            else:
                merged.append([a, b])
        self._deadline(deadline)
        return any(quote in source.content[a - span.full_char_start:b - span.full_char_start] for a, b in merged)

    def close(self):
        errors = []
        for resource in (self.encoder, self.dense, self.lexical, self.store):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    errors.append(type(exc).__name__)
        self.encoder = self.dense = self.lexical = self.store = None
        self.lock.release()
        if errors:
            raise BackendError("关闭本地库失败：" + ",".join(errors))
