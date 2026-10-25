"""同步检索契约；模拟库仅用于验证运行能力。"""
import json
import time
from functools import lru_cache
from typing import Protocol

from .config import Settings
from .schemas import ChunkSpan, Hit, Passage

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
    def read(self, planid: str, section_idx: int | None, max_chars: int, *,
             chunk_id: str | None = None, offset: int = 0,
             document_id: str | None = None, generation_id: str | None = None) -> Passage: ...

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

    def read(self, planid, section_idx, max_chars, *, chunk_id=None, offset=0,
             document_id=None, generation_id=None):
        """按目录 / 章节 / 片段三种范围读取；offset 是对象内偏移，读完 next_offset 为 None。"""
        self._fault("read")
        doc = next((d for d in self.docs if d["planid"] == planid), None)
        if doc is None:
            raise KBNotFound(planid)
        if not doc["body"]:
            raise KBNoText(planid, doc["title"])
        values = {k: doc[k] for k in META}
        if chunk_id is not None:
            return self._page(doc, values, self._locate(doc, chunk_id), offset, max_chars, "chunk")
        if section_idx is None:
            return Passage(**values, section_idx=None, section_path="", content="",
                           char_start=None, char_end=None, is_index=True, read_scope="index",
                           sections=[{"idx": s["idx"], "path": s["path"],
                                      "chars": s["char_end"] - s["char_start"]}
                                     for s in doc["sections"]])
        section = next((s for s in doc["sections"] if s["idx"] == section_idx), None)
        if section is None:
            raise KBNotFound(planid, section_idx)
        return self._page(doc, values, dict(section, chunk=None), offset, max_chars, "section")

    @staticmethod
    def _locate(doc, chunk_id):
        """按 chunk_id 直接定位完整块范围与所属章节。"""
        chunk = next((c for c in doc["chunks"] if c["chunk_id"] == chunk_id), None)
        if chunk is None:
            raise KBNotFound(doc["planid"])
        section = next(s for s in doc["sections"] if s["idx"] == chunk["section_idx"])
        return {"idx": section["idx"], "path": section["path"],
                "char_start": chunk["char_start"], "char_end": chunk["char_end"], "chunk": chunk}

    def _page(self, doc, values, target, offset, max_chars, scope):
        start, end = target["char_start"], target["char_end"]
        cut, stop = min(start + offset, end), 0
        stop = min(end, cut + max_chars)
        spans = [ChunkSpan(chunk_id=c["chunk_id"],
                           char_start=max(c["char_start"], cut), char_end=min(c["char_end"], stop),
                           chunk_char_start=c["char_start"], chunk_char_end=c["char_end"],
                           complete=c["char_start"] >= cut and c["char_end"] <= stop)
                 for c in doc["chunks"] if c["char_start"] < stop and c["char_end"] > cut]
        chunk = target.get("chunk")
        return Passage(**values, section_idx=target["idx"], section_path=target["path"],
                       content=doc["body"][cut:stop], char_start=cut, char_end=stop,
                       is_index=False, read_scope=scope, truncated=stop < end,
                       next_offset=None if stop >= end else stop - start, chunks=spans,
                       chunk_id=chunk["chunk_id"] if chunk else None,
                       chunk_char_start=start if chunk else None,
                       chunk_char_end=end if chunk else None,
                       parent_id=chunk.get("parent_id") if chunk else None)

# ponytail: 键只保留知识库相关三项，最多 4 个实例；接入多后端/多路径时再改为显式生命周期管理。
@lru_cache(maxsize=4)
def _build_kb(backend: str, path: str, snippet_chars: int) -> KnowledgeBase:
    if backend == "mock":
        return MockKB(path, snippet_chars=snippet_chars)
    if backend == "sample":
        from .kb_sample import SampleKB  # 延迟导入打破导入环
        return SampleKB()
    raise ValueError("不支持的检索后端: " + backend)

_RAG_KB = None


def warm_up(s: Settings) -> None:
    """进程启动时调用一次：真实 RAG 后端在这里加载，避免首个请求被启动阻塞。

    RagKnowledgeBase 构造会阻塞到 ready（BM25 约 11 秒，Hybrid 约 83 秒），
    绝不能放进请求路径里。
    """
    global _RAG_KB
    if s.kb_backend == "rag" and _RAG_KB is None:
        from .kb_rag import RagKB
        _RAG_KB = RagKB(s.rag_store_project, s.rag_mode)


def close_kb() -> None:
    """进程退出时调用：释放 RAG 的库线程与索引。"""
    global _RAG_KB
    if _RAG_KB is not None:
        try:
            _RAG_KB.close()
        finally:
            _RAG_KB = None


def get_kb(s: Settings) -> KnowledgeBase:
    if s.kb_backend == "rag":
        if _RAG_KB is None:
            warm_up(s)          # 冻加载兜底：没预热也能跑，但会阻塞当前请求
        return _RAG_KB
    return _build_kb(s.kb_backend, str(s.mock_kb_path), s.search_snippet_chars)
