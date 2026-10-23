"""MockKB 目录标志、标题检索保真度与 get_kb 缓存键的回归测试。"""
import copy
from dataclasses import replace

import pytest

from guance_agent.config import Settings
from guance_agent.kb import MockKB, _build_kb, get_kb
from guance_agent.schemas import Passage


@pytest.fixture(autouse=True)
def _isolated_kb_cache():
    """用例前后清空缓存，避免污染其他测试。"""
    _build_kb.cache_clear()
    yield
    _build_kb.cache_clear()


@pytest.fixture
def kb():
    return MockKB(Settings().mock_kb_path)


def test_directory_read_is_flagged_as_index(kb):
    passage = kb.read("TEST-001", None, 6000)
    assert passage.is_index is True
    assert passage.content == "" and passage.sections and not passage.chunks
    assert passage.char_start is None and passage.char_end is None


@pytest.mark.parametrize("planid,section_idx", [("TEST-001", 0), ("TEST-005", 0)])
def test_section_read_is_not_index(kb, planid, section_idx):
    passage = kb.read(planid, section_idx, 6000)
    assert passage.is_index is False
    assert passage.content and passage.char_start is not None


def test_passage_is_index_defaults_to_false():
    passage = Passage(planid="P", title="T", area=None, fyp=None, admin_level=None,
                      plantype_label=None, section_idx=0, section_path="p", content="c",
                      char_start=0, char_end=1)
    assert passage.is_index is False


@pytest.mark.parametrize("query,planid", [
    ("乙市十四五农业农村规划", "TEST-002"),
    ("甲市十四五农业农村规划", "TEST-001"),
])
def test_title_terms_rank_own_document_first(kb, query, planid):
    hits = kb.search(query, None, None, 8)
    assert hits, query
    assert hits[0].planid == planid
    assert hits == sorted(hits, key=lambda h: (-h.score, h.chunk_id))
    doc = next(d for d in kb.docs if d["planid"] == planid)
    # 标题词不出现在正文里：命中只可能来自 title / section_path 参与打分
    assert "十四五" not in doc["body"]


def test_metadata_scoring_does_not_create_false_hits(kb):
    assert kb.search("量子通信", None, None, 8) == []


def test_cache_key_ignores_unrelated_settings():
    s = Settings()
    kb = get_kb(s)
    assert get_kb(replace(s, log_content=not s.log_content)) is kb
    assert get_kb(replace(s, max_turns=s.max_turns + 1)) is kb
    assert _build_kb.cache_info().misses == 1


def test_cache_key_includes_snippet_chars_without_eviction():
    s = Settings()
    kb = get_kb(s)
    assert get_kb(replace(s, search_snippet_chars=s.search_snippet_chars + 1)) is not kb
    assert get_kb(s) is kb  # maxsize>1：切走再切回仍复用原实例


def test_cache_key_includes_backend():
    s = Settings()
    kb = get_kb(s)
    unsupported = copy.copy(s)
    object.__setattr__(unsupported, "kb_backend", "other")
    with pytest.raises(ValueError):
        get_kb(unsupported)   # 未复用 mock 实例，说明 backend 在缓存键里
    assert get_kb(s) is kb
