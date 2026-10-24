from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

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
