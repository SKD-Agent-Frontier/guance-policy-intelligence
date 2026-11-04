"""QA model-failure fallback cannot fabricate research conclusions."""
import time
from types import SimpleNamespace

import pytest

from guance_agent.research_fallback import (
    complete_excerpt,
    recover_empty_search_failure,
    recover_read_excerpts,
)
from guance_agent.research_quality import evaluate, requirements
from guance_agent.schemas import EvidenceRef, RunError, RunResult

TEXT = "推动工业结构调整，发展特色产业，支持产业集群建设。"


class FakeEvidence:
    def __init__(self, refs=()):
        self.refs = {ref.chunk_id: ref for ref in refs}

    def ids(self):
        return list(self.refs)

    def get(self, cid):
        return self.refs.get(cid)


class FakeKB:
    def get_chunk_source(self, chunk_id, *, document_id, generation_id):
        return SimpleNamespace(
            chunk_char_start=0, content=TEXT,
            planid="2561", document_id="doc", generation_id="gen",
        )

    def verify_quote(self, chunk_id, quote, observed_ranges, *,
                     document_id, generation_id):
        return (chunk_id == "chunk-1" and quote in TEXT
                and observed_ranges == [(0, len(TEXT))])


def _result(code="OUTPUT_PARSE"):
    return RunResult(
        run_id="r", session_id="s", status="failed", evidence_status="none",
        answer="模型输出格式异常", claims=[], evidence_refs=[],
        dropped_chunk_ids=[], error=RunError(code=code, message="模型运行失败"),
        meta={}, task_type="qa",
    )


def _source(source="kb_read"):
    return EvidenceRef(
        planid="2561", chunk_id="chunk-1", title="沧州市第十个五年计划纲要",
        area="沧州市", fyp="第十个五年计划", admin_level=None, plantype_label=None,
        section_idx=1, section_path="产业结构调整", char_start=0, char_end=len(TEXT),
        source=source, observed_ranges=[(0, len(TEXT))], document_id="doc",
        generation_id="gen",
    )


@pytest.mark.asyncio
async def test_verified_read_can_be_delivered_only_as_source_excerpt():
    result = _result()
    ctx = SimpleNamespace(
        kb=FakeKB(), evidence=FakeEvidence([_source()]),
        route=SimpleNamespace(kind="objects", planids=("2561",)),
    )
    ok = await recover_read_excerpts(result, ctx, time.monotonic() + 20)
    assert ok
    assert result.error is None and result.status == "completed"
    assert result.evidence_status == "insufficient"
    assert result.claims[0].text == TEXT
    assert "不是研究分析或政策结论" in result.answer
    assert result.meta["original_execution_error"] == "OUTPUT_PARSE"
    assert result.meta["execution_recovery"]["kind"] == "verified_verbatim_excerpts"
    quality = evaluate(result, "分析产业扶持政策优势")
    assert quality["status"] != "complete"
    assert "source_excerpts_only" in quality["gaps"]


@pytest.mark.asyncio
async def test_unread_or_unverified_source_can_never_be_recovered():
    for source in ["kb_search", "kb_read"]:
        result = _result()
        kb = FakeKB()
        if source == "kb_read":
            kb.verify_quote = lambda *a, **kw: False
        ctx = SimpleNamespace(kb=kb, evidence=FakeEvidence([_source(source)]),
                              route=SimpleNamespace(kind="open"))
        assert not await recover_read_excerpts(result, ctx, time.monotonic() + 10)
        assert result.status == "failed" and not result.claims


@pytest.mark.asyncio
async def test_scope_mismatch_cannot_escape_into_recovery():
    result = _result("MODEL_TIMEOUT")
    ctx = SimpleNamespace(kb=FakeKB(), evidence=FakeEvidence([_source()]),
                          route=SimpleNamespace(kind="objects", planids=("other",)))
    assert not await recover_read_excerpts(result, ctx, time.monotonic() + 10)
    assert result.error.code == "MODEL_TIMEOUT"


def test_empty_search_failure_is_not_fabricated_as_nonexistent_policy():
    result = _result()
    ctx = SimpleNamespace(evidence=FakeEvidence(), tool_calls=[
        {"name": "kb_search", "status": "empty"}])
    assert recover_empty_search_failure(result, ctx)
    assert result.status == "completed" and result.evidence_status == "insufficient"
    assert not result.claims and not result.evidence_refs
    assert "不代表相关政策不存在" in result.answer
    assert result.meta["original_execution_error"] == "OUTPUT_PARSE"


def test_backend_fault_and_absent_search_never_become_no_results():
    for calls in ([], [{"status": "backend_error"}], [{"status": "timeout"}]):
        result = _result()
        ctx = SimpleNamespace(evidence=FakeEvidence(), tool_calls=calls)
        assert not recover_empty_search_failure(result, ctx)
        assert result.status == "failed"


def test_user_requirements_cover_missing_acceptance_dimensions():
    question = ("分析数字经济政策在产业扶持、基础设施建设、人才培养方面的异同，"
                "说明其积极作用及局限")
    got = requirements(question)["dimensions"]
    assert "地区差异" not in got  # Topic/facet comparison does not name different regions.
    assert {"产业扶持", "基础设施建设", "人才培养",
            "政策作用", "政策局限"} <= set(got)


def test_verbatim_excerpt_never_cuts_mid_sentence():
    original = ("推动工业结构调整并培育特色优势产业。" + "支持" * 130 + "。" + "重点发展高新技术。")
    text = complete_excerpt(original)
    assert text is not None and text.endswith("。")
    assert text == "推动工业结构调整并培育特色优势产业。"
    assert complete_excerpt("连续文本" * 80) is None
