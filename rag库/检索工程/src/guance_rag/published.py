"""将已发布的纯文本批次接入现有检索与原文契约。"""
from dataclasses import replace

from .backend import StoreBackend
from .config import DEFAULT_STORE
from .errors import BackendError
from .expansion import load_release
from .legacy import component
from .models import TitleLookup


class PublishedBackend:
    def __init__(self, options, *, preview=None):
        self.options = options
        self.base = StoreBackend(options)
        self.active = [self.base]
        self.opened = {}
        self.history = {}
        self.release = None
        self.owners = dict.fromkeys(self.base.store.by_planid, self.base)
        try:
            if options.mode == "bm25":
                self.release = preview or load_release(options.store_project, self.base.store.generation,
                                                       options.expansion_root)
            if self.release:
                self.history = self.release.history
                for path in self.release.active:
                    shard = self._open(path)
                    if self.owners.keys() & shard.store.by_planid.keys():
                        raise BackendError("累计发布存在重复政策编号")
                    self.owners.update(dict.fromkeys(shard.store.by_planid, shard))
                    self.active.append(shard)
        except Exception:
            self.close()
            raise

    def _open(self, path):
        if path.name not in self.opened:
            # 这些读取器共享同一个检索线程与分词词表。
            shard = StoreBackend(replace(self.options, store_project=path),
                                 lexical_tokenizer=self.base.lexical.index.tokenizer)
            if not shard.store.text_only or shard.store.generation != path.name:
                shard.close()
                raise BackendError("增量索引身份与发布清单不一致")
            self.opened[path.name] = shard
        return self.opened[path.name]

    def search(self, query, area, fyp, top_k, *, deadline, **filters):
        if len(self.active) == 1:
            return self.base.search(query, area, fyp, top_k, deadline=deadline, **filters)
        selections = []
        for backend in self.active:
            ranked = backend.rank(query, area, fyp, top_k, deadline=deadline, **filters)
            if ranked:
                selections.append((backend, ranked))
        if not selections:
            return []
        if len(selections) == 1:
            backend, ranked = selections[0]
            return backend.render(ranked, top_k, deadline)

        documents, owners = [], {}
        for backend, ranked in selections:
            for source, _, rid in ranked:
                row = backend.store.connection.execute("SELECT text FROM representations WHERE id=?",
                                                       (rid,)).fetchone()
                if row is None or not row[0] or rid in owners:
                    raise BackendError("跨批候选缺少唯一的词法检索正文")
                documents.append((rid, row[0]))
                owners[rid] = (backend, source)
        ranker = component(DEFAULT_STORE, "bm25s_index").BM25SLexical.rerank
        scored = ranker(self.base.lexical.index, query, documents)
        results, contexts = [], set()
        remaining = self.options.max_search_chars
        for rid, score in scored:
            self.base._deadline(deadline)
            backend, source = owners[rid]
            hits = backend.render([(source, score, rid)], 1, deadline,
                                  remaining=remaining, seen_contexts=contexts)
            if not hits:
                continue
            hit = hits[0]
            results.append(hit)
            passages = getattr(hit, "contexts", None) or getattr(hit, "ocr_contexts", ())
            remaining -= len(hit.content) + sum(len(p.content) for p in passages)
            if not remaining or len(results) >= top_k:
                break
        return results

    def find_by_title(self, title, *, deadline, **filters):
        candidates = []
        for backend in self.active:
            candidates.extend(backend.find_by_title(title, deadline=deadline, **filters).candidates)
        return TitleLookup(matched=len(candidates), candidates=candidates)

    def _owner(self, generation_id, *, planid=None, chunk_id=None, document_id=None):
        if generation_id:
            path = self.history.get(generation_id)
            return self._open(path) if path else self.base
        if planid in self.owners:
            return self.owners[planid]
        for backend in self.active:
            if document_id in backend.store.document_records or (
                chunk_id and backend.store.object(chunk_id)
            ):
                return backend
        return self.base

    def read(self, planid, section_idx, max_chars, *, generation_id=None, **kwargs):
        backend = self._owner(generation_id, planid=planid, chunk_id=kwargs.get("chunk_id"),
                              document_id=kwargs.get("document_id"))
        return backend.read(planid, section_idx, max_chars, generation_id=generation_id, **kwargs)

    def get_chunk_source(self, chunk_id, *, document_id=None, generation_id=None, **kwargs):
        backend = self._owner(generation_id, chunk_id=chunk_id, document_id=document_id)
        return backend.get_chunk_source(chunk_id, document_id=document_id,
                                        generation_id=generation_id, **kwargs)

    def verify_quote(self, chunk_id, quote, observed_ranges, *, document_id=None, generation_id=None, **kwargs):
        backend = self._owner(generation_id, chunk_id=chunk_id, document_id=document_id)
        return backend.verify_quote(chunk_id, quote, observed_ranges, document_id=document_id,
                                    generation_id=generation_id, **kwargs)

    def read_image(self, source_id, **kwargs):
        return self.base.read_image(source_id, **kwargs)

    def verify_media_quote(self, source_id, quote, observed_ranges, **kwargs):
        return self.base.verify_media_quote(source_id, quote, observed_ranges, **kwargs)

    def stats(self, *, deadline):
        result = self.base.stats(deadline=deadline)
        if self.release:
            result.update(generation_id=self.release.generation, revision=self.release.revision,
                          documents=len(self.owners), markdown_shards=len(self.active) - 1)
            for key in ("native_representations", "metadata_loads", "bm25_loads",
                        "snapshot_loads", "object_primary_key_lookups"):
                result[key] += sum(b.stats(deadline=deadline)[key] for b in self.active[1:])
        return result

    def close(self):
        try:
            for shard in self.opened.values():
                shard.close()
        finally:
            self.base.close()
