"""S3 mock evidence-prefix tests; the shown text is not a rewritten quotation."""

from guance_agent.business.organize.prompt import build_messages
from guance_agent.business.organize.reading import EvidenceWindow, Reading
from guance_agent.business.organize.scope import parse_scope

from .organize_fakes import MultiDocKB, mock_context


def test_mock_exact_prefixes_and_combined_character_budget():
    kb = MultiDocKB(count=6, chunks=2)
    task, ctx = mock_context(kb=kb)
    windows = []
    for hit in kb.hits:
        passage = kb.get_chunk_source(hit.chunk_id)
        passage.content = "原文内容包含  空格\n和标点。" * 150
        windows.append(EvidenceWindow(hit.planid, hit.chunk_id, passage))
    reading = Reading(windows=windows)
    messages = build_messages(parse_scope(task, ctx), reading)
    assert sum(len(w.shown) for w in windows) <= 6000
    for window in windows:
        assert window.shown == window.passage.content[:500]
        assert window.chunk_id in messages[1]["content"]
        assert window.shown in messages[1]["content"]


def test_mock_categories_dimensions_and_local_excerpt_warning():
    task, ctx = mock_context("梳理科技创新，按平台建设和人才培养分类", dimensions=["资金规模"])
    messages = build_messages(parse_scope(task, ctx), Reading())
    assert "资金规模" in messages[0]["content"]
    assert "平台建设、人才培养" in messages[0]["content"]
    assert "不代表全文" in messages[0]["content"]
