"""只读核查已发布 RAG 的原文定位，并导出真实数据的拟议接口样例。

这不是 KnowledgeBase 生产适配器，也不修改 Agent、切块、向量或发布指针。
SQLite 以 mode=ro 打开；仅使用现有 BM25S 做真实关键词检索，不打开 dense
索引、不调用模型。输出中的新增字段是对接提案，不能称为现有 API 响应。
"""
from __future__ import annotations

import bisect
import hashlib
import json
import runpy
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PROJECT = REPO / "rag库/向量化与存储工程（降级版）"
sys.path.insert(0, str(PROJECT))

from filelock import FileLock  # noqa: E402
from vector_store.bm25s_index import BM25SLexical  # noqa: E402


def write_json(name: str, value: object) -> None:
    """只写本交付目录；JSON 转义换行不改变读回后的原文字串。"""
    path = HERE / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stable_id(kind: str, *parts: object) -> str:
    """逐项复核现有切块器的 ID 算法；不导入会加载 tokenizer 的构建流程。"""
    data = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return kind + "_" + hashlib.sha256(data.encode("utf-8")).hexdigest()[:32]


def inspect(db: sqlite3.Connection) -> dict:
    """核查当前冻结来源；所有 start/end 都是完整 MD 的 Unicode 码点范围。"""
    generation, revision = db.execute("SELECT generation,revision FROM runtime").fetchone()
    manifest = json.loads(db.execute("SELECT manifest FROM releases WHERE id=?", (generation,)).fetchone()[0])
    prepared = Path(manifest["prepared_path"])
    kinds = defaultdict(dict)
    for oid, kind, data in db.execute(
        "SELECT id,kind,data FROM objects WHERE kind IN ('document','structure','parent','chunk')"
    ):
        kinds[kind][oid] = json.loads(data)
    docs, sections, parents, chunks = (kinds[k] for k in ("document", "structure", "parent", "chunk"))
    texts, snapshots, counts = {}, {}, Counter()
    by_document = defaultdict(list)
    for section in sections.values():
        by_document[section["document_id"]].append(section)
    for values in by_document.values():
        # root 在首位，同起点按层级排序。idx 只是本次提案生成的目录索引，
        # 现有 RAG 没有发布此目录，不能当作已存在的持久化章节编号。
        values.sort(key=lambda s: (s["start"], s["level"], s["id"]))
    starts = {did: [s["start"] for s in values] for did, values in by_document.items()}
    idx = {s["id"]: i for values in by_document.values() for i, s in enumerate(values)}
    for doc in docs.values():
        raw = (prepared / doc["snapshot"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == doc["source_hash"]
        # read_text 的通用换行转换会破坏 CRLF 坐标；必须先读字节再严格解码。
        texts[doc["id"]] = raw.decode("utf-8", errors="strict")
        snapshots[doc["id"]] = {"sha256": doc["source_hash"], "snapshot": doc["snapshot"]}
        counts["documents_with_crlf"] += int(b"\r\n" in raw)
        assert doc["id"] == stable_id("doc", doc["planid"], doc["source_hash"])
    large = []
    for chunk in chunks.values():
        parent = parents[chunk["parent_id"]]
        section = sections[parent["structure_id"]]
        doc = docs[chunk["document_id"]]
        spans = chunk["source_segments"]
        counts["multi_segment_chunks"] += int(len(spans) != 1)
        ranges = [(s["start"], s["end"]) for s in spans]
        assert chunk["id"] == stable_id("chunk", doc["id"], parent["id"], ranges, doc["config_hash"])
        for span in spans:
            a, b = span["start"], span["end"]
            assert span["text"] == texts[doc["id"]][a:b]
            assert parent["document_id"] == doc["id"] == section["document_id"]
            assert parent["start"] <= a < b <= parent["end"]
            assert section["start"] <= a < b <= section["end"]
            # 同时检查内部是否另起标题，避免“落在上级章节范围内”掩盖跨小节。
            positions = starts[doc["id"]]
            inner = bisect.bisect_left(positions, b) - bisect.bisect_right(positions, a)
            counts["chunks_with_internal_section_boundary"] += int(inner > 0)
            if b - a > 6000:
                large.append((b - a, chunk["id"]))
        counts["chunk_ids_recomputed"] += 1
        counts["chunk_spans_verified"] += len(spans)

    member_rows = db.execute(
        "SELECT r.id,r.source_id,r.record,m.payload FROM members m "
        "JOIN representations r ON r.id=m.representation_id WHERE m.generation=?", (generation,)
    ).fetchall()
    members = {rid: {"source_id": sid, "record": json.loads(record), "payload": json.loads(payload)}
               for rid, sid, record, payload in member_rows}
    versions = {r["record"]["document_id"]: r["record"]["document_version_id"] for r in members.values()}
    native_ids = {rid for rid, row in members.items() if row["record"]["source_type"] == "text_chunk"}
    source_counts = Counter(row["record"]["source_type"] for row in members.values())
    counts.update(documents=len(docs), native_chunks=len(chunks), structures=len(sections),
                  parents=len(parents), chunks_over_6000=len(large), active_representations=len(members))

    def metadata(chunk: dict) -> dict:
        doc = docs[chunk["document_id"]]
        parent = parents[chunk["parent_id"]]
        section = sections[parent["structure_id"]]
        m = doc["metadata"]
        return {"planid": doc["planid"], "title": m.get("title"), "area": m.get("area_name") or None,
                "fyp": m.get("fyp_name") or None, "admin_level": m.get("admin_level") or None,
                "plantype_label": m.get("plantype_label") or None,
                "document_id": doc["id"], "document_version_id": versions[doc["id"]],
                "source_hash": doc["source_hash"],
                "generation_id": generation, "section_id": section["id"],
                "section_idx": idx[section["id"]], "section_path": " / ".join(chunk["section_path"]),
                "offset_basis": "full_markdown_with_front_matter",
                "offset_unit": "unicode_codepoint", "body_start": doc["body_start"],
                "source_path": doc["source_path"], "snapshot": doc["snapshot"]}

    def passage(chunk: dict, offset: int = 0, max_chars: int = 6000) -> dict:
        """样例只投影连续文本块。offset 相对 chunk 起点，输出坐标仍是全文件坐标。"""
        span = chunk["source_segments"][0]
        a = span["start"] + offset
        b = min(span["end"], a + max_chars)
        assert span["start"] <= a < b <= span["end"]
        section = sections[parents[chunk["parent_id"]]["structure_id"]]
        return {**metadata(chunk), "contract_version": "proposal-v0.2", "read_scope": "chunk",
                "chunk_id": chunk["id"], "parent_id": chunk["parent_id"],
                "chunk_char_start": span["start"], "chunk_char_end": span["end"],
                "section_char_start": section["start"], "section_char_end": section["end"],
                "content": texts[chunk["document_id"]][a:b], "char_start": a, "char_end": b,
                "is_index": False, "truncated": b < span["end"],
                "next_offset": b - span["start"] if b < span["end"] else None,
                "chunks": [{"chunk_id": chunk["id"], "chunk_char_start": span["start"],
                            "chunk_char_end": span["end"], "char_start": a, "char_end": b,
                            "complete": a == span["start"] and b == span["end"]}],
                "contexts": chunk["context_segments"], "required_relation_ids": chunk["required_relation_ids"],
                "quality_warnings": chunk.get("reasons", []), "sections": None}

    # 调用现有 BM25S 实现产生真实候选；无需模型密钥，也不占用完整 dense 库内存。
    lexical = BM25SLexical(PROJECT / ".runtime/local/bm25")
    try:
        lexical.load(generation)
        query = "养老服务"
        found = lexical.search(query, 20, native_ids)
    finally:
        lexical.close()
    chosen = next((rank, hit) for rank, hit in enumerate(found, 1)
                  if 300 < len(chunks[members[hit["id"]]["source_id"]]["source_segments"][0]["text"]) <= 6000)
    rank, hit = chosen
    row = members[hit["id"]]
    chunk = chunks[row["source_id"]]
    span = chunk["source_segments"][0]
    snippet_end = min(span["end"], span["start"] + 300)
    sample = {**metadata(chunk), "contract_version": "proposal-v0.2", "chunk_id": chunk["id"],
              "chunk_char_start": span["start"], "chunk_char_end": span["end"],
              "content": texts[chunk["document_id"]][span["start"]:snippet_end],
              "char_start": span["start"], "char_end": snippet_end,
              "truncated": snippet_end < span["end"], "score": 1 / (60 + rank),
              "source_type": "text_chunk", "parent_id": chunk["parent_id"],
              "representation_id": hit["id"], "document_version_id": row["record"]["document_version_id"],
              "contexts": chunk["context_segments"], "required_relation_ids": chunk["required_relation_ids"],
              "quality_warnings": chunk.get("reasons", [])}
    raw = {"representation_id": hit["id"], "rrf_score": sample["score"],
           "channels": {"bm25": {"rank": rank, "score": hit["score"]}},
           "record": row["record"], "payload": row["payload"], "source": chunk,
           "parent": parents[chunk["parent_id"]], "document": docs[chunk["document_id"]]}
    # 保留与 operations.query 相同的 related 回取语义，作为当前嵌套结构的样例。
    related_ids = [r[0] for r in db.execute(
        "SELECT target_id FROM links WHERE status='resolved' AND source_id IN (?,?)",
        (chunk["id"], chunk["parent_id"]))]
    raw["related"] = [json.loads(db.execute("SELECT data FROM objects WHERE id=?", (oid,)).fetchone()[0])
                      for oid in related_ids]
    write_json("samples/真实RAG结果.json", {"generation": generation, "mode": "bm25",
              "filters": {"source_type": "text_chunk"}, "results": [raw]})
    write_json("samples/Hit.json", sample)
    write_json("samples/Passage.json", passage(chunk))
    # 同时展示章节读取：同一章从章首读 6000 码点，与按块读 451 字的范围不同。
    section = sections[parents[chunk["parent_id"]]["structure_id"]]
    section_end = min(section["end"], section["start"] + 6000)
    page_chunks = []
    for candidate in chunks.values():
        if candidate["document_id"] != chunk["document_id"]:
            continue
        if parents[candidate["parent_id"]]["structure_id"] != section["id"]:
            continue
        original = candidate["source_segments"][0]
        a, b = max(original["start"], section["start"]), min(original["end"], section_end)
        if a < b:
            page_chunks.append({"chunk_id": candidate["id"],
                                "chunk_char_start": original["start"], "chunk_char_end": original["end"],
                                "char_start": a, "char_end": b,
                                "complete": a == original["start"] and b == original["end"]})
    page_chunks.sort(key=lambda c: (c["char_start"], c["chunk_id"]))
    chapter_page = {**metadata(chunk), "contract_version": "proposal-v0.2", "read_scope": "section",
                    "content": texts[chunk["document_id"]][section["start"]:section_end],
                    "char_start": section["start"], "char_end": section_end,
                    "section_char_start": section["start"], "section_char_end": section["end"],
                    "is_index": False, "truncated": section_end < section["end"],
                    "next_offset": section_end - section["start"] if section_end < section["end"] else None,
                    "chunks": page_chunks, "sections": None}
    assert chapter_page["content"][sample["char_start"]-section["start"]:
                                   sample["char_end"]-section["start"]] == sample["content"]
    write_json("samples/章节Passage.json", chapter_page)
    # 选最短的超限块验证续读，避免把最大的 9 万字块整份复制到交付目录。
    long_size, long_id = min(large)
    long_chunk = chunks[long_id]
    page1, page2 = passage(long_chunk), passage(long_chunk, 6000)
    assert page1["char_end"] == page2["char_start"]
    assert page1["content"] + page2["content"] == long_chunk["source_segments"][0]["text"]
    write_json("samples/长块第1页.json", page1)
    write_json("samples/长块第2页.json", page2)
    # 同名句子不使用 str.find 反查。直接按主键取块，再使用冻结位置重建。
    stored = json.loads(db.execute("SELECT data FROM objects WHERE id=?", (chunk["id"],)).fetchone()[0])
    assert stored == chunk
    assert sample["content"] == texts[chunk["document_id"]][sample["char_start"]:sample["char_end"]]
    assert passage(chunk)["content"] == span["text"]
    # 使用现有真实 Pydantic 类证明：旧类型能接受基础字段，但会丢弃拟议新字段。
    # 仅给字典增加键并不能完成接入，Hit/Passage/EvidenceRef 都必须一起修订。
    schemas = runpy.run_path(str(REPO / "agent/src/guance_agent/schemas.py"))
    old_hit = schemas["Hit"].model_validate(sample).model_dump()
    old_passage = schemas["Passage"].model_validate(passage(chunk)).model_dump()
    dropped = {"Hit": sorted(sample.keys() - old_hit.keys()),
               "Passage": sorted(passage(chunk).keys() - old_passage.keys())}
    assert "chunk_char_start" in dropped["Hit"] and "next_offset" in dropped["Passage"]
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
            "repository_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
            "generation_id": generation, "revision": revision, "counts": dict(counts),
            "active_source_types": dict(source_counts), "max_chunk_chars": max(large)[0],
            "max_chunk_id": max(large)[1], "sample_query": query, "sample_mode": "bm25",
            "sample_rank": rank, "sample_bm25_score": hit["score"], "sample_chunk_id": chunk["id"],
            "sample_planid": docs[chunk["document_id"]]["planid"],
            "sample_chunk_chars": span["end"] - span["start"],
            "sample_snippet_chars": len(sample["content"]),
            "chapter_sample": {"chars": len(chapter_page["content"]), "chunks": len(page_chunks),
                               "truncated": chapter_page["truncated"],
                               "char_start": section["start"], "char_end": section_end},
            "sample_section_idx_is_proposed": True,
            "agent_v01_drops_proposed_fields": dropped,
            "long_sample": {"chunk_id": long_id, "chars": long_size,
                            "page_chars": [len(page1["content"]), len(page2["content"])],
                            "reconstruction_passed": True},
            "lookup_plan": list(db.execute("EXPLAIN QUERY PLAN SELECT data FROM objects WHERE id=?", (chunk["id"],))),
            "checks": {"snapshot_hashes": True, "all_native_spans": True, "chunk_id_recomputation": True,
                       "sample_primary_key_lookup": True, "snippet_and_full_chunk": True, "long_chunk_pagination": True},
            "models_called": 0, "production_adapter_implemented": False,
            "startup_and_concurrency_tested": False,
            "limitations": ["拟议字段由样例脚本投影，不是已接入的 Agent 后端响应",
                            "只核查原生文本块；OCR 和图片字段需要单独的坐标类型",
                            "未运行 dense、混合检索或回答准确率评测"]}


def main() -> None:
    """复用项目锁，防止核查途中切换发布；不删除锁或绕过 Qdrant 路径锁。"""
    with FileLock(str(PROJECT / ".runtime/project.lock"), timeout=0):
        db_path = (PROJECT / ".runtime/local/metadata.sqlite3").resolve()
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
            report = inspect(db)
    write_json("核查结果.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
