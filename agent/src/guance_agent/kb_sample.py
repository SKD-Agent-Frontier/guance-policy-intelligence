"""用 RAG 交付的真实样例当数据源的离线后端。

存在的意义：在 RAG 适配器交付之前，让 Agent 侧能吃下 proposal-v0.3 的真实结构
（完整块坐标、章节范围、分页、read_scope），并把契约差异变成测试失败。
样例来自《苏州市"十四五"民政事业发展规划》《杭州市商贸服务第十二个五年规划》的
真实检索结果投影，不是自编数据。
"""
import json
from pathlib import Path

from .kb import KBNoText, KBNotFound
from .schemas import ChunkSpan, Hit, Passage

META = ("planid", "title", "area", "fyp", "admin_level", "plantype_label")
SPAN = ("chunk_char_start", "chunk_char_end", "char_start", "char_end")
CONTRACT = ("document_id", "document_version_id", "generation_id", "source_hash",
            "section_id", "section_char_start", "section_char_end",
            "offset_basis", "offset_unit", "body_origin", "contract_version")


class SampleMissingPage(Exception):
    """样例没有覆盖这一页。如实报错，绝不把第一页当成整章。"""


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class SampleKB:
    """按 chunk_id / section_idx 从样例重建原文，并按 max_chars 分页。"""

    name = "sample"

    def __init__(self, root: Path | str = None):
        self.root = Path(root) if root else Path(__file__).resolve().parents[2] / "contracts/rag_samples"
        self.chunks: dict[str, dict] = {}
        self.sections: dict[tuple[str, int], dict] = {}
        self._load()

    # ---------- 装载 ----------
    def _entry(self, passage: dict, text: str) -> dict:
        return {"meta": {k: passage.get(k) for k in META + CONTRACT},
                "section_path": passage.get("section_path") or "",
                "section_idx": passage.get("section_idx"),
                "section_start": passage.get("section_char_start"),
                "section_end": passage.get("section_char_end"),
                "start": passage["chunk_char_start"] if passage.get("chunk_id") else passage["char_start"],
                "end": passage["chunk_char_end"] if passage.get("chunk_id") else passage["char_end"],
                "text": text}

    def _load(self):
        def path(name):
            file = self.root / name
            if not file.exists():
                raise FileNotFoundError(f"缺少 RAG 样例：{file}")
            return file

        chunk = _load(path("passage_chunk.json"))
        self.chunks[chunk["chunk_id"]] = self._entry(chunk, chunk["content"])

        # 超长块由两页拼接而来，验证分页边界连续
        p1, p2 = _load(path("passage_long_page1.json")), _load(path("passage_long_page2.json"))
        assert p1["content"] + p2["content"], "长块两页为空"
        self.chunks[p1["chunk_id"]] = self._entry(p1, p1["content"] + p2["content"])

        section = _load(path("passage_section.json"))
        key = (section["planid"], section["section_idx"])
        self.sections[key] = {
            "meta": {k: section.get(k) for k in META + CONTRACT},
            "section_path": section.get("section_path") or "",
            "section_idx": section["section_idx"],
            "start": section["section_char_start"],      # 章节的完整范围
            "end": section["section_char_end"],
            "page_start": section["char_start"],         # 样例实际给出的那一页
            "page_end": section["char_end"],
            "next_offset": section.get("next_offset"),
            "truncated": bool(section.get("truncated")),
            "chunks": [ChunkSpan.model_validate(c) for c in section.get("chunks") or []],
            "text": section["content"],
        }

    # ---------- 检索 ----------
    def search(self, query, area, fyp, top_k, *, planids=None):
        grams = {query[i:i + 2] for i in range(max(len(query) - 1, 0))} or {query}
        hits = []
        for chunk_id, item in self.chunks.items():
            meta = item["meta"]
            if area and area not in (meta["area"] or ""):
                continue
            if fyp is not None and meta["fyp"] != fyp:
                continue
            blob = " ".join([meta["title"], item["section_path"], item["text"]])
            score = sum(gram in blob for gram in grams)
            if not score:
                continue
            preview = item["text"][:300]
            hits.append(Hit(**meta, chunk_id=chunk_id, section_idx=item["section_idx"],
                            section_path=item["section_path"], content=preview,
                            char_start=item["start"], char_end=item["start"] + len(preview),
                            chunk_char_start=item["start"], chunk_char_end=item["end"],
                            truncated=len(preview) < len(item["text"]), score=float(score)))
        return sorted(hits, key=lambda h: (-h.score, h.chunk_id))[:top_k]

    # ---------- 读取 ----------
    def read(self, planid, section_idx, max_chars, *, chunk_id=None, offset=0,
             document_id=None, generation_id=None):
        if chunk_id is not None:
            item = self.chunks.get(chunk_id)
            if item is None or item["meta"]["planid"] != planid:
                raise KBNotFound(planid)
            return self._page(planid, item, offset, max_chars, "chunk", chunk_id)
        if section_idx is not None:
            item = self.sections.get((planid, section_idx))
            if item is None:
                raise KBNotFound(planid, section_idx)
            if offset != 0:
                raise SampleMissingPage(
                    f"样例只覆盖该章节第 1 页（offset=0..{item['next_offset']}），offset={offset} 无数据")
            return Passage(**item["meta"], section_idx=item["section_idx"],
                           section_path=item["section_path"], content=item["text"],
                           char_start=item["page_start"], char_end=item["page_end"],
                           is_index=False, read_scope="section", truncated=item["truncated"],
                           next_offset=item["next_offset"], chunks=item["chunks"])
        if planid not in {m["planid"] for m in self._metas()}:
            raise KBNotFound(planid)
        return Passage(**self._values(planid), section_idx=None, section_path="", content="",
                       char_start=None, char_end=None, is_index=True, read_scope="index",
                       sections=[{"idx": idx, "path": item["section_path"]}
                                 for (pid, idx), item in self.sections.items() if pid == planid])

    def _metas(self):
        return [item["meta"] for item in self.chunks.values()]

    def _values(self, planid):
        for item in self.chunks.values():
            if item["meta"]["planid"] == planid:
                return dict(item["meta"])
        raise KBNotFound(planid)

    def _page(self, planid, item, offset, max_chars, scope, chunk_id):
        start, end = item["start"], item["end"]
        cut = min(start + offset, end)
        stop = min(end, cut + max_chars)
        if not item["text"] and cut == start:
            raise KBNoText(planid, item["meta"]["title"])
        body = item["text"]
        spans = [ChunkSpan(chunk_id=chunk_id or "", char_start=cut, char_end=stop,
                           chunk_char_start=start, chunk_char_end=end,
                           complete=cut == start and stop == end)] if chunk_id else []
        return Passage(**item["meta"], section_idx=item["section_idx"],
                       section_path=item["section_path"], content=body[cut - start:stop - start],
                       char_start=cut, char_end=stop, is_index=False, read_scope=scope,
                       truncated=stop < end, next_offset=None if stop >= end else stop - start,
                       chunks=spans, chunk_id=chunk_id,
                       chunk_char_start=start if chunk_id else None,
                       chunk_char_end=end if chunk_id else None)
