from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

from guance_rag import RagKnowledgeBase
from guance_rag.cli import respond
from guance_rag.errors import BackendError, InvalidArgument, KBNoText, KBNotFound
from guance_rag.models import HitV03, PassageV03
from guance_rag.media_models import MediaHitV04, MediaPassageV04
from guance_rag.retrieval import DenseReader, fuse_sources
from guance_rag.storage import body_origin


def test_origin_preserves_interior_unicode_crlf():
    text = "\ufeff \r\n#😀 A\r\n  B\n"
    origin = body_origin(text, 0)
    assert text[origin:] == "#😀 A\r\n  B\n"
    assert len(text[origin:origin + 2]) == 2
    assert body_origin(" \n", 0) == 2


def test_delivered_samples_validate():
    samples = Path(__file__).resolve().parents[2] / "设计方案2.0/Agent对接说明/samples"
    for name in ("Hit.json", "Passage.json", "章节Passage.json", "长块第1页.json", "长块第2页.json"):
        value = json.loads((samples / name).read_text(encoding="utf-8"))
        (HitV03 if name == "Hit.json" else PassageV03).model_validate(value)


def test_delivered_media_samples_validate():
    samples = Path(__file__).resolve().parents[2] / "设计方案2.0/Agent对接说明/samples"
    for name, cls in (("MediaHit.json", MediaHitV04), ("MediaPassage.json", MediaPassageV04)):
        cls.model_validate_json((samples / name).read_text(encoding="utf-8"))


def test_read_paging_exact_reconstruction_and_versions(options):
    with RagKnowledgeBase(options) as kb:
        full = kb.get_chunk_source("chunk_a_1")
        first = kb.read("1001", chunk_id="chunk_a_1")
        second = kb.read("1001", chunk_id="chunk_a_1", offset=first.next_offset)
        assert len(first.content) == 6000 and first.truncated
        assert first.content + second.content == full.content
        assert first.char_end == second.char_start and second.next_offset is None
        assert not first.chunks[0].complete and not second.chunks[0].complete
        assert full.chunks[0].complete
        assert full.body_origin > full.body_start
        assert first.section_id == "section_a" and first.section_idx == 1
        directory = kb.read("1001")
        assert directory.is_index and directory.sections[0]["char_start"] == 0
        root_page = kb.read("1001", section_idx=0, max_chars=10)
        assert root_page.chunks[0].section_id == "section_a"
        old = kb.read("1001", chunk_id="chunk_old_0", generation_id="g0")
        assert old.document_id == "doc_old" and old.generation_id == "g0"
        with pytest.raises(InvalidArgument):
            kb.read("1001", chunk_id="chunk_old_0", generation_id="g1")
        with pytest.raises(InvalidArgument):
            kb.read("1001", chunk_id="chunk_a_1", section_idx=0)
        with pytest.raises(InvalidArgument):
            kb.read("1002", chunk_id="chunk_a_1")
        with pytest.raises(InvalidArgument):
            kb.read("1001", chunk_id="chunk_a_1", offset=len(full.content))
        with pytest.raises(KBNotFound):
            kb.read("1001", chunk_id="chunk_unknown")
        with pytest.raises(KBNoText):
            kb.read("1003")
        assert respond(kb, {"operation": "read", "arguments": {"planid": "1003"}})["status"] == "no_text"
        missing = respond(kb, {"operation": "read", "arguments": {"planid": "1001", "chunk_id": "chunk_unknown"}})
        assert missing["status"] == "not_found" and "chunk_unknown" in missing["message"]
        assert respond(kb, {"operation": "drop"})["status"] == "invalid_argument"


def test_prepare_reasons_propagate_to_hits_chunk_spans_and_passages(options):
    reasons = {
        "chunk_a_0": ["prepare_warning_a", "prepare_warning_a"],
        "chunk_a_1": ["prepare_warning_b", "prepare_warning_b"],
    }
    with sqlite3.connect(options.store_project / ".runtime/local/metadata.sqlite3") as db:
        for chunk_id, chunk_reasons in reasons.items():
            raw = db.execute("SELECT data FROM objects WHERE id=?", (chunk_id,)).fetchone()[0]
            chunk = json.loads(raw)
            chunk["reasons"] = chunk_reasons
            data = json.dumps(chunk, ensure_ascii=False)
            db.execute("UPDATE objects SET data=?,data_hash=? WHERE id=?",
                       (data, hashlib.sha256(data.encode()).hexdigest(), chunk_id))

    with RagKnowledgeBase(options) as kb:
        hit = next(hit for hit in kb.search("养老服务", area="苏州", top_k=4) if hit.chunk_id == "chunk_a_0")
        assert hit.quality_warnings.count("prepare_warning_a") == 1

        chunk_passage = kb.get_chunk_source("chunk_a_0")
        assert chunk_passage.quality_warnings.count("prepare_warning_a") == 1
        assert chunk_passage.chunks[0].quality_warnings.count("prepare_warning_a") == 1

        section_passage = kb.read("1001", section_idx=0, max_chars=6000)
        chunk_warnings = {span.chunk_id: span.quality_warnings for span in section_passage.chunks}
        assert chunk_warnings["chunk_a_0"].count("prepare_warning_a") == 1
        assert chunk_warnings["chunk_a_1"].count("prepare_warning_b") == 1
        assert section_passage.quality_warnings.count("prepare_warning_a") == 1
        assert section_passage.quality_warnings.count("prepare_warning_b") == 1


def test_real_bm25_prefilter_contexts_and_shared_thread(options):
    with RagKnowledgeBase(options) as kb:
        hits = kb.search("养老服务", area="苏州", fyp="十四五", top_k=2)
        assert hits and {h.planid for h in hits} == {"1001"}
        assert len({h.chunk_id for h in hits}) == len(hits)
        assert all(h.source_type == "text_chunk" and h.score > 0 for h in hits)
        hit = next(h for h in hits if h.chunk_id == "chunk_a_0")
        assert any(c.role == "parent_context" for c in hit.contexts)
        assert "relation_ok" in hit.required_relation_ids
        assert "missing_relation:relation_not_registered" in hit.quality_warnings
        assert any(w.startswith("unresolved_relation:") for w in hit.quality_warnings)
        assert any(w.startswith("context_budget_exceeded:parent_") for w in hit.quality_warnings)
        assert not kb.search("养老服务", area="不存在地区")
        assert not kb.search("zzzzNoSuchToken987654")
        assert {h.planid for h in kb.search("养老服务", area="湖南", top_k=1)} == {"1002"}
        before = kb.stats()
        with ThreadPoolExecutor(max_workers=5) as pool:
            stats = list(pool.map(lambda _: kb.stats(), range(10)))
        assert {s["worker_thread_id"] for s in stats} == {before["worker_thread_id"]}
        assert {s["runtime_instance_id"] for s in stats} == {before["runtime_instance_id"]}
        assert all(s["bm25_loads"] == s["metadata_loads"] == 1 for s in stats)
        assert kb.stats()["snapshot_loads"] == before["snapshot_loads"]
        hits[0].quality_warnings.append("caller_mutation")
        assert "caller_mutation" not in kb.search("养老服务", area="苏州")[0].quality_warnings


@pytest.mark.parametrize(("section_start", "section_end", "outside"), [
    (0, 20, False),  # 块恰好覆盖章节，属于包含关系。
    (0, 10, True),  # 预览/首页仍在章内，但完整块已越过章尾。
    (1, 20, True),  # 块起点早于章首。
    (20, 30, True),  # 完全不重叠也属于章节归属范围异常。
])
def test_chunk_section_containment_uses_full_range(options, section_start, section_end, outside):
    # 仅修改临时库的章节元数据；块仍逐字对应快照且在原父块内。
    with sqlite3.connect(options.store_project / ".runtime/local/metadata.sqlite3") as db:
        section = json.loads(db.execute("SELECT data FROM objects WHERE id='section_a'").fetchone()[0])
        origin = section["start"]
        section.update(start=origin + section_start, end=origin + section_end)
        data = json.dumps(section, ensure_ascii=False)
        db.execute("UPDATE objects SET data=?,data_hash=? WHERE id='section_a'",
                   (data, hashlib.sha256(data.encode()).hexdigest()))
    original = (options.store_project / "g1/doc_a.md").read_bytes().decode("utf-8")[origin:origin + 20]
    with RagKnowledgeBase(replace(options, snippet_chars=4)) as kb:
        hit = next(h for h in kb.search("养老服务", area="苏州", top_k=2) if h.chunk_id == "chunk_a_0")
        page = kb.read("1001", chunk_id="chunk_a_0", max_chars=4)
        full = kb.get_chunk_source("chunk_a_0")
        for result in (hit, page, full):
            assert (result.chunk_char_start, result.chunk_char_end) == (0, 20)
            assert (result.section_char_start, result.section_char_end) == (section_start, section_end)
            assert ("chunk_outside_section" in result.quality_warnings) == outside
            assert result.section_id == "section_a"
        assert hit.content == page.content == original[:4]
        assert full.content == original  # 章节越界不裁剪原块。
        root_page = kb.read("1001", section_idx=0, max_chars=4)
        item = root_page.chunks[0]
        assert item.chunk_id == "chunk_a_0" and item.section_id == "section_a"
        assert (item.section_char_start, item.section_char_end) == (section_start, section_end)
        assert ("chunk_outside_section" in item.quality_warnings) == outside
        assert ("chunk_outside_section:chunk_a_0" in root_page.quality_warnings) == outside


def test_quote_only_uses_observed_ranges(options):
    with RagKnowledgeBase(options) as kb:
        source = kb.get_chunk_source("chunk_a_0")
        a = source.chunk_char_start
        assert kb.verify_quote(source.chunk_id, source.content[:8], [[a, a + 4], [a + 4, a + 8]])
        assert not kb.verify_quote(source.chunk_id, source.content[:8], [[a, a + 4]])
        assert not kb.verify_quote(source.chunk_id, source.content[:8], [])
        with pytest.raises(InvalidArgument):
            kb.verify_quote(source.chunk_id, "养老", [[a - 1, a + 4]])
        with pytest.raises(InvalidArgument):
            kb.verify_quote(source.chunk_id, "养老", None)


def test_concurrent_searches_keep_filters_results_and_contexts_independent(options):
    cases = [dict(query="养老服务", area="苏州", fyp="十四五", top_k=2),
             dict(query="湖南财政补贴与就业支持", area="湖南", top_k=1),
             dict(query="养老服务", area="湖南", top_k=2),
             dict(query="养老服务", area="不存在地区"),
             dict(query="养老服务", source_types=["image_occurrence"])]
    with RagKnowledgeBase(options) as kb:
        expected = [[hit.model_dump() for hit in kb.search(**case)] for case in cases]
        assert {hit["planid"] for hit in expected[0]} == {"1001"}
        assert any(hit["contexts"] for hit in expected[0])
        assert {hit["planid"] for hit in expected[1]} == {"1002"}
        assert expected[3] == expected[4] == []
        before = kb.stats()
        barrier = threading.Barrier(5)
        def search(index):
            barrier.wait(timeout=5)
            hits = kb.search(**cases[index])
            assert [hit.model_dump() for hit in hits] == expected[index]
            for hit in hits:
                hit.contexts.clear()
                hit.required_relation_ids.clear()
                hit.quality_warnings.append("caller_mutation")
        with ThreadPoolExecutor(5) as pool:
            list(pool.map(search, list(range(5)) * 4))
        assert [hit.model_dump() for hit in kb.search(**cases[0])] == expected[0]
        after = kb.stats()
        for key in ("runtime_instance_id", "worker_thread_id", "metadata_loads", "bm25_loads", "snapshot_loads"):
            assert after[key] == before[key]


def test_second_process_cannot_reopen_shared_local_store(options):
    script = """
import sys
from guance_rag import RagKnowledgeBase, RuntimeOptions
from guance_rag.errors import BackendError
try:
    kb = RagKnowledgeBase(RuntimeOptions(store_project=sys.argv[1], cache_directory=sys.argv[2], mode="bm25"))
except BackendError as error:
    assert "本地库正在被其他" in str(error), str(error)
    print("locked")
else:
    kb.close()
    raise SystemExit("another process reopened the local store")
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), PYTHONIOENCODING="utf-8")
    with RagKnowledgeBase(options):
        child = subprocess.run([sys.executable, "-c", script, str(options.store_project), str(options.cache_directory)],
                               env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "locked"


def test_rrf_does_not_reward_duplicate_representations():
    members = {rid: {"record": {"source_id": source}} for rid, source in [("a1", "a"), ("a2", "a"), ("b", "b")]}
    result = fuse_sources({"bm25": [{"id": "a1"}, {"id": "a2"}, {"id": "b"}], "dense": [{"id": "b"}]}, members)
    assert result[0][0] == "b"
    assert result[0][1] == pytest.approx(1 / 63 + 1 / 61)
    assert result[1][1] == pytest.approx(1 / 61)


def test_quote_binds_requested_generation_when_chunk_is_reused(options):
    with sqlite3.connect(options.store_project / ".runtime/local/metadata.sqlite3") as db:
        db.execute("INSERT INTO releases SELECT 'g2',status,manifest,validation,created_at FROM releases WHERE id='g1'")
        db.execute("INSERT INTO members SELECT 'g2',representation_id,payload FROM members WHERE generation='g1'")
    with RagKnowledgeBase(options) as kb:
        source = kb.get_chunk_source("chunk_a_0", generation_id="g2")
        assert source.generation_id == "g2"
        assert kb.verify_quote(source.chunk_id, source.content[:4], [[source.char_start, source.char_end]],
                               document_id=source.document_id, generation_id="g2")


def test_readonly_snapshot_and_exclusive_lifecycle(options):
    with RagKnowledgeBase(options):
        with pytest.raises(BackendError, match="正在被其他"):
            RagKnowledgeBase(options)
    with RagKnowledgeBase(options) as kb:
        assert kb.stats()["metadata_loads"] == 1
    path = options.store_project / "g1/doc_a.md"
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(BackendError, match="哈希"):
        RagKnowledgeBase(options)
    # 启动失败也释放SQLite和项目锁，恢复快照后可重开。
    path.write_bytes(path.read_bytes()[:-8])
    with RagKnowledgeBase(options):
        pass


@pytest.mark.parametrize("arguments", [dict(query=""), dict(query="a", top_k=True), dict(query="a", top_k=21), dict(query="a", fyp=123)])
def test_invalid_search_arguments(options, arguments):
    with RagKnowledgeBase(options) as kb:
        with pytest.raises(InvalidArgument):
            kb.search(**arguments)


def test_actual_qdrant_filters_before_top_k(tmp_path):
    import numpy as np
    from qdrant_client import QdrantClient, models as m
    root = tmp_path / "qdrant"
    vector = np.zeros(4096, dtype=np.float32)
    vector[0] = 1
    client = QdrantClient(path=str(root))
    try:
        client.create_collection("fixture", vectors_config={"dense": m.VectorParams(size=4096, distance=m.Distance.COSINE)})
        client.upsert("fixture", points=[m.PointStruct(id=i, vector={"dense": vector.tolist()}, payload={
            "representation_id": str(i), "source_type": source_type, "document_version_id": version})
            for i, source_type, version in [(1, "text_chunk", "wrong"), (2, "visual_chunk", "allowed"), (3, "text_chunk", "allowed")]])
    finally:
        client.close()
    reader = DenseReader(root, "fixture")
    try:
        assert [x["id"] for x in reader.search(vector, 1, {"allowed"})] == ["3"]
        assert reader.loads == 1
    finally:
        reader.close()


# ---------------- 文档定位与范围过滤（检索路由所需的最小能力） ----------------

def test_find_by_title_matches_exactly_and_never_substitutes(options):
    """标题定位只做保守规范化后的精确匹配；零匹配不能退回相似政策。"""
    with RagKnowledgeBase(options) as kb:
        found = kb.find_by_title("测试规划a")
        assert found.matched == 1
        assert [c.planid for c in found.candidates] == ["1001"]
        # 书名号只剥一层
        assert kb.find_by_title("《测试规划a》").matched == 1
        assert kb.find_by_title("《测试规划b》").matched == 1
        # 不做前缀、包含、模糊匹配
        assert kb.find_by_title("测试规划").matched == 0
        assert kb.find_by_title("测试规划a副本").matched == 0
        # 不存在的标题就是 0，不能拿别的政策顶上
        assert kb.find_by_title("完全不存在的规划").matched == 0
        assert kb.find_by_title("完全不存在的规划").candidates == []


def test_find_by_title_applies_area_and_period_filters(options):
    with RagKnowledgeBase(options) as kb:
        assert kb.find_by_title("测试规划a", area="苏州").matched == 1
        assert kb.find_by_title("测试规划a", area="湖南").matched == 0
        assert kb.find_by_title("测试规划a", fyp="十四五").matched == 1
        assert kb.find_by_title("测试规划a", fyp="十三五").matched == 0


def test_find_by_title_returns_identity_only(options):
    """定位结果只描述对象身份，不携带正文证据，避免把候选当证据。"""
    with RagKnowledgeBase(options) as kb:
        candidate = kb.find_by_title("测试规划a").candidates[0]
        payload = candidate.model_dump()
        assert set(payload) == {"planid", "title", "area", "fyp", "document_id",
                                "document_version_id", "generation_id"}
        assert not any(key in payload for key in ("content", "quotes", "chunk_ids"))


@pytest.mark.parametrize("arguments", [dict(title=""), dict(title="   "), dict(title="a", area=123),
                                       dict(title="a", fyp=123)])
def test_find_by_title_rejects_invalid_arguments(options, arguments):
    with RagKnowledgeBase(options) as kb:
        with pytest.raises(InvalidArgument):
            kb.find_by_title(**arguments)


def test_search_planids_scopes_before_ranking(options):
    """指定 planid 时只在该文档内检索；不能先做全库 Top-K 再事后筛选。"""
    with RagKnowledgeBase(options) as kb:
        scoped = kb.search("养老服务", top_k=8, planids=["1002"])
        assert scoped and {hit.planid for hit in scoped} == {"1002"}
        # 另一篇同样含关键词，但被范围排除
        other = kb.search("养老服务", top_k=8, planids=["1001"])
        assert other and {hit.planid for hit in other} == {"1001"}
        # 限定到不存在的编号时必须返回空，不能退回全库结果
        assert kb.search("养老服务", top_k=8, planids=["9999"]) == []


def test_search_planids_can_narrow_to_a_cold_document(options):
    """范围过滤在候选排名之前生效：即使某文档在全库排名靠后，限定后仍能正常检索。"""
    with RagKnowledgeBase(options) as kb:
        wide = {hit.planid for hit in kb.search("养老服务", top_k=8)}
        assert wide == {"1001", "1002"}
        cold = kb.search("养老服务", top_k=8, planids=["1002"])
        assert cold and all(hit.planid == "1002" for hit in cold)
        assert all(hit.score > 0 for hit in cold)


@pytest.mark.parametrize("planids", [[], [" "], "1001", [1001], ["1001", ""]])
def test_search_rejects_invalid_planids(options, planids):
    with RagKnowledgeBase(options) as kb:
        with pytest.raises(InvalidArgument):
            kb.search("养老服务", planids=planids)
