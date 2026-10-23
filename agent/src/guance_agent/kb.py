"""同步检索契约；模拟库仅用于验证运行能力。"""
import json
import time
from functools import lru_cache
from typing import Protocol

from .config import Settings
from .schemas import Hit, Passage

FYP_ALIASES = {f"{n}五": f"第{n}个五年规划" for n in ("十一", "十二", "十三", "十四", "十五")}
META = ("planid", "title", "area", "fyp", "admin_level", "plantype_label")

class KBNotFound(Exception):
    def __init__(self, planid: str, section_idx: int | None = None):
        self.planid, self.section_idx = planid, section_idx

class KBNoText(Exception):
    def __init__(self, planid: str, title: str):
        self.planid, self.title = planid, title

def normalize_fyp(fyp: str | None) -> str | None:
    if fyp is None:
        return None
    key = "".join(c for c in fyp if not c.isspace() and c not in '\"\'“”‘’')
    return FYP_ALIASES.get(key, fyp)

class KnowledgeBase(Protocol):
    name: str

    def search(self, query: str, area: str | None, fyp: str | None, top_k: int) -> list[Hit]: ...
    def read(self, planid: str, section_idx: int | None, max_chars: int) -> Passage: ...

class MockKB:
    name = "mock"

    def __init__(self, path, fail=None, delay=0.0, snippet_chars=300):
        with open(path, encoding="utf-8") as stream:
            self.docs = json.load(stream)
        self.fail, self.delay, self.snippet_chars = fail, delay, snippet_chars

    def _fault(self, operation):
        time.sleep(self.delay)
        if self.fail == operation:
            raise RuntimeError("mock backend fault")

    def search(self, query, area, fyp, top_k):
        self._fault("search")
        grams = {query[i:i + 2] for i in range(len(query) - 1)}
        hits = []
        for doc in self.docs:
            if area and area not in (doc["area"] or ""):
                continue
            if fyp is not None and doc["fyp"] != fyp:
                continue
            for chunk in doc["chunks"]:
                start, end = chunk["char_start"], chunk["char_end"]
                content = doc["body"][start:end]
                section = next(s for s in doc["sections"] if s["idx"] == chunk["section_idx"])
                # ponytail: mock 检索保真度改进（标题+章节路径+正文参与打分），
                # 不代表真实 RAG 的召回策略；接入真实后端时整段替换。
                blob = " ".join([doc["title"], section["path"], content])
                score = sum(gram in blob for gram in grams)
                if not score:
                    continue
                content = content[:self.snippet_chars]
                hits.append(Hit(**{k: doc[k] for k in META}, chunk_id=chunk["chunk_id"],
                                section_idx=section["idx"], section_path=section["path"],
                                content=content, char_start=start, char_end=start + len(content),
                                score=score))
        return sorted(hits, key=lambda h: (-h.score, h.chunk_id))[:top_k]

    def read(self, planid, section_idx, max_chars):
        self._fault("read")
        doc = next((d for d in self.docs if d["planid"] == planid), None)
        if doc is None:
            raise KBNotFound(planid)
        if not doc["body"]:
            raise KBNoText(planid, doc["title"])
        values = {k: doc[k] for k in META}
        if section_idx is None:
            return Passage(**values, section_idx=None, section_path="", content="",
                           char_start=None, char_end=None, is_index=True, sections=[
                               {"idx": s["idx"], "path": s["path"],
                                "chars": s["char_end"] - s["char_start"]} for s in doc["sections"]])
        section = next((s for s in doc["sections"] if s["idx"] == section_idx), None)
        if section is None:
            raise KBNotFound(planid, section_idx)
        start = section["char_start"]
        end = min(section["char_end"], start + max_chars)
        chunks = [{k: c[k] for k in ("chunk_id", "char_start", "char_end")}
                  for c in doc["chunks"] if start <= c["char_start"] < c["char_end"] <= end]
        return Passage(**values, section_idx=section_idx, section_path=section["path"],
                       content=doc["body"][start:end], char_start=start, char_end=end,
                       is_index=False, truncated=end < section["char_end"], chunks=chunks)

# ponytail: 键只保留知识库相关三项，最多 4 个实例；接入多后端/多路径时再改为显式生命周期管理。
@lru_cache(maxsize=4)
def _build_kb(backend: str, path: str, snippet_chars: int) -> KnowledgeBase:
    if backend == "mock":
        return MockKB(path, snippet_chars=snippet_chars)
    raise ValueError("不支持的检索后端")

def get_kb(s: Settings) -> KnowledgeBase:
    return _build_kb(s.kb_backend, str(s.mock_kb_path), s.search_snippet_chars)
