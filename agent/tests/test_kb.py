import json

import pytest

from guance_agent.config import Settings
from guance_agent.kb import KBNoText, KBNotFound, MockKB, get_kb, normalize_fyp


@pytest.fixture
def kb():
    return get_kb(Settings())


def test_search_filters_sort_and_offsets(kb):
    hits = kb.search("农业农村", "甲", normalize_fyp(' “十四五” '), 20)
    assert hits and {h.planid for h in hits} == {"TEST-001"}
    assert hits == sorted(hits, key=lambda h: (-h.score, h.chunk_id))
    docs = {d["planid"]: d for d in kb.docs}
    for hit in hits:
        assert docs[hit.planid]["body"][hit.char_start:hit.char_end] == hit.content
    assert not kb.search("量子通信", None, None, 8)
    assert not kb.search("农业", "丙", "第十四个五年规划", 8)
    assert any(h.planid == "TEST-005" for h in kb.search("农业", "丙", None, 8))
    assert not kb.search("农业", "甲", "十四五", 8)  # 后端只接受标准值
    assert normalize_fyp("未知规划") == "未知规划"


def test_read_directory_errors_and_truncation(kb):
    directory = kb.read("TEST-001", None, 6000)
    assert len(directory.sections) >= 3 and directory.content == "" and not directory.chunks
    for planid, section, exception in [("missing", None, KBNotFound),
                                      ("TEST-001", 99, KBNotFound), ("TEST-003", None, KBNoText)]:
        with pytest.raises(exception):
            kb.read(planid, section, 6000)
    long = kb.read("TEST-005", 0, 6000)
    assert len(long.content) == 6000 and long.truncated and long.fyp is None
    assert all(c.char_end <= long.char_end for c in long.chunks)


def test_all_fixture_spans(kb):
    for doc in kb.docs:
        for c in doc["chunks"]:
            p = kb.read(doc["planid"], c["section_idx"], len(doc["body"]))
            assert doc["body"][c["char_start"]:c["char_end"]] == p.content[
                c["char_start"] - p.char_start:c["char_end"] - p.char_start]
            assert c["chunk_id"].startswith(doc["planid"] + "__s")
    short = MockKB(Settings().mock_kb_path, snippet_chars=5).search("农业", None, None, 1)[0]
    assert len(short.content) == short.char_end - short.char_start == 5


@pytest.mark.parametrize("operation", ["search", "read"])
def test_fault_injection(operation):
    kb = MockKB(Settings().mock_kb_path, fail=operation)
    with pytest.raises(RuntimeError):
        if operation == "search":
            kb.search("农业", None, None, 8)
        else:
            kb.read("TEST-001", 0, 6000)


def test_contract_examples():
    from guance_agent.config import ROOT
    from guance_agent.schemas import ToolResult
    samples = [ToolResult.model_validate(json.loads(p.read_text(encoding="utf-8")))
               for p in (ROOT / "contracts/samples").glob("*.json")]
    assert {s.status for s in samples} == {
        "ok", "empty", "no_text", "not_found", "invalid_argument", "timeout", "backend_error"}
