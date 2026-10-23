"""只读元数据和冻结快照；内部始终使用原文件码点，输出时才换原点。"""
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from .errors import BackendError, InvalidArgument, KBNotFound


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def body_origin(text, start):
    if type(start) is not int or not 0 <= start <= len(text):
        raise BackendError("元数据结束位置无效")
    if start == 0 and text.startswith("\ufeff"):
        start = 1
    while start < len(text) and text[start].isspace():
        start += 1
    return start


@dataclass
class Document:
    data: dict
    text: str
    origin: int
    generation: str


class ReadStore:
    def __init__(self, project):
        self.project = project
        config = read_json(project / "config.json")
        if config["storage"]["metadata"] != "sqlite":
            raise BackendError("检索仅支持既有本地发布")
        self.root = (project / config["storage"]["directory"]).resolve()
        path = self.root / "metadata.sqlite3"
        self.connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")
        self.connection.execute("BEGIN")
        self.cache = OrderedDict()
        self.documents = {}
        self.release_docs = {}
        self.sections = {}
        self.section_indices = {}
        self.parents = {}
        self.context_parents = defaultdict(list)
        self.section_chunks = defaultdict(list)
        self.indexed_documents = set()
        self.object_lookups = 0
        self.snapshot_loads = 0
        active = self.connection.execute("SELECT generation,revision FROM runtime").fetchone()
        if active is None:
            raise BackendError("没有可用的冻结发布")
        self.generation, self.revision = active
        self.manifests = {}
        for row in self.connection.execute("SELECT id,manifest FROM releases WHERE status='ready'"):
            self.manifests[row["id"]] = json.loads(row["manifest"])
        if self.generation not in self.manifests:
            raise BackendError("当前发布尚未就绪")
        self.manifest = self.manifests[self.generation]
        self.members = {}
        self.versions = {}
        self.all_member_count = 0
        rows = self.connection.execute(
            "SELECT r.record,m.payload FROM members m JOIN representations r "
            "ON r.id=m.representation_id WHERE m.generation=?", (self.generation,))
        for row in rows:
            self.all_member_count += 1
            record, payload = json.loads(row[0]), json.loads(row[1])
            self.versions[record["document_id"]] = record["document_version_id"]
            if record["source_type"] == "text_chunk":
                if record["id"] in self.members:
                    raise BackendError("发布包含重复表示身份")
                self.members[record["id"]] = {"record": record, "payload": payload}
        self.current_documents = self._release_document_ids(self.generation)
        self.by_planid = {}
        for did in sorted(self.current_documents):
            doc = self.document(did, self.generation)
            if doc.data["planid"] in self.by_planid:
                raise BackendError("同一发布出现多个同planid版本")
            self.by_planid[doc.data["planid"]] = did
        for did in sorted(self.current_documents):
            self._index_document(did, self.generation)
        sources = {r["record"]["source_id"] for r in self.members.values()}
        known = {c[2] for values in self.section_chunks.values() for c in values}
        if not sources <= known:
            raise BackendError("发布成员缺少原生块或章节定位")
        signatures = {}
        for row in self.members.values():
            record, payload = row["record"], row["payload"]
            signature = (record["document_id"], payload.get("planid"), payload.get("area_name"), payload.get("period_key"))
            version = record["document_version_id"]
            if payload.get("document_version_id") != version or signatures.setdefault(version, signature) != signature:
                raise BackendError("同一文档版本的过滤元数据不一致")

    def _checked_data(self, row):
        if row is None:
            return None
        if hashlib.sha256(row["data"].encode("utf-8")).hexdigest() != row["data_hash"]:
            raise BackendError("来源对象内容哈希不一致")
        return json.loads(row["data"])

    def object(self, oid):
        if oid in self.cache:
            self.cache.move_to_end(oid)
            return self.cache[oid]
        self.object_lookups += 1
        row = self.connection.execute("SELECT data,data_hash FROM objects WHERE id=?", (oid,)).fetchone()
        value = self._checked_data(row)
        if value is not None:
            self.cache[oid] = value
            if len(self.cache) > 128:
                self.cache.popitem(last=False)
        return value

    def _release_document_ids(self, generation):
        if generation not in self.manifests:
            raise KBNotFound()
        if generation not in self.release_docs:
            path = Path(self.manifests[generation]["prepared_path"]) / "documents.jsonl"
            with path.open(encoding="utf-8") as stream:
                self.release_docs[generation] = {json.loads(line)["id"] for line in stream if line.strip()}
        return self.release_docs[generation]

    def locate_generation(self, did, requested=None):
        candidates = [requested] if requested else [self.generation] + sorted(set(self.manifests) - {self.generation})
        for generation in candidates:
            if generation in self.manifests and did in self._release_document_ids(generation):
                return generation
        if requested and requested in self.manifests:
            raise InvalidArgument("文档不属于指定发布")
        raise KBNotFound()

    def document(self, did, generation=None):
        generation = self.locate_generation(did, generation)
        key = (generation, did)
        if key not in self.documents:
            row = self.connection.execute("SELECT data,data_hash FROM documents WHERE id=?", (did,)).fetchone()
            data = self._checked_data(row)
            if data is None:
                raise KBNotFound()
            prepared = Path(self.manifests[generation]["prepared_path"]).resolve()
            snapshot = (prepared / data["snapshot"]).resolve()
            if not snapshot.is_relative_to(prepared):
                raise BackendError("快照路径超出冻结目录")
            raw = snapshot.read_bytes()
            if hashlib.sha256(raw).hexdigest() != data["source_hash"]:
                raise BackendError("冻结原文哈希不一致")
            text = raw.decode("utf-8", errors="strict")
            self.documents[key] = Document(data, text, body_origin(text, data["body_start"]), generation)
            self.snapshot_loads += 1
        return self.documents[key]

    def _objects(self, did, kind):
        for row in self.connection.execute("SELECT data,data_hash FROM objects WHERE document_id=? AND kind=?", (did, kind)):
            yield self._checked_data(row)

    def _index_document(self, did, generation):
        if did in self.indexed_documents:
            return
        doc = self.document(did, generation)
        structures = sorted(self._objects(did, "structure"), key=lambda x: (x["start"], x["level"], x["id"]))
        self.sections[did] = structures
        self.section_indices.update({s["id"]: i for i, s in enumerate(structures)})
        for parent in self._objects(did, "parent"):
            self.parents[parent["id"]] = {k: parent[k] for k in ("id", "document_id", "structure_id", "start", "end", "section_path")}
        structure_ids = {s["id"] for s in structures}
        for chunk in self._objects(did, "chunk"):
            spans = chunk["source_segments"]
            if len(spans) != 1:
                raise BackendError("当前纯文本契约不接受不连续主块")
            span = spans[0]
            parent = self.parents.get(chunk["parent_id"])
            if parent is None or parent["structure_id"] not in structure_ids or parent["document_id"] != did:
                raise BackendError("原生块的章节归属无效")
            a, b = span["start"], span["end"]
            if not doc.origin <= a < b <= len(doc.text) or doc.text[a:b] != span["text"]:
                raise BackendError("原生块不能逐字还原冻结原文")
            if not parent["start"] <= a < b <= parent["end"]:
                raise BackendError("原生块超出原父块范围")
            self.section_chunks[parent["structure_id"]].append((a, b, chunk["id"]))
        for s in structures:
            self.section_chunks[s["id"]].sort()
        for context in self._objects(did, "parent_context"):
            for cid in context["member_chunk_ids"]:
                self.context_parents[cid].append(context["id"])
        self.indexed_documents.add(did)

    def resolve(self, planid, chunk_id=None, document_id=None, generation_id=None):
        chunk = self.object(chunk_id) if chunk_id else None
        if chunk_id and (chunk is None or not chunk_id.startswith("chunk_") or "source_segments" not in chunk):
            raise KBNotFound(planid, chunk_id=chunk_id)
        did = chunk["document_id"] if chunk else document_id
        if did is None:
            generation = generation_id or self.generation
            if generation == self.generation:
                did = self.by_planid.get(planid)
            else:
                for candidate in sorted(self._release_document_ids(generation)):
                    if self.document(candidate, generation).data["planid"] == planid:
                        did = candidate
                        break
        if did is None:
            raise KBNotFound(planid)
        if document_id is not None and did != document_id:
            raise InvalidArgument("chunk_id与document_id不一致")
        doc = self.document(did, generation_id)
        if doc.data["planid"] != planid:
            raise InvalidArgument("来源不属于指定planid")
        self._index_document(did, doc.generation)
        return doc, chunk

    def section(self, doc, section_idx=None, chunk=None):
        values = self.sections[doc.data["id"]]
        parent = self.parents[chunk["parent_id"]] if chunk else None
        own = next((s for s in values if parent and s["id"] == parent["structure_id"]), None)
        if section_idx is not None:
            if section_idx >= len(values):
                raise KBNotFound(doc.data["planid"], section_idx)
            chosen = values[section_idx]
            if own is not None and chosen["id"] != own["id"]:
                raise InvalidArgument("chunk_id不属于指定章节")
            return chosen
        return own

    def section_start(self, doc, section):
        start = section["start"]
        if start < doc.origin:
            if section["level"] != 0:
                raise BackendError("章节范围早于正文原点")
            start = doc.origin
        return start

    def metadata(self, doc, section=None):
        data, m = doc.data, doc.data["metadata"]
        path = section.get("path", section.get("section_path", [])) if section else []
        if isinstance(path, list):
            path = " / ".join(path)
        version = self.versions.get(data["id"])
        if version is None:
            row = self.connection.execute("SELECT record FROM representations WHERE document_id=? LIMIT 1", (data["id"],)).fetchone()
            version = json.loads(row[0])["document_version_id"] if row else None
            self.versions[data["id"]] = version
        return dict(planid=data["planid"], title=m.get("title") or "", area=m.get("area_name") or None,
                    fyp=m.get("fyp_name") or None, admin_level=m.get("admin_level") or None,
                    plantype_label=m.get("plantype_label") or None, document_id=data["id"],
                    document_version_id=version, source_hash=data["source_hash"], generation_id=doc.generation,
                    source_path=data["source_path"], snapshot=data["snapshot"], body_start=data["body_start"],
                    body_origin=doc.origin, section_id=section["id"] if section else None,
                    section_idx=self.section_indices[section["id"]] if section else None, section_path=path or "")

    def close(self):
        if getattr(self, "connection", None) is not None:
            self.connection.rollback()
            self.connection.close()
            self.connection = None
