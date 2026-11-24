import time
from types import SimpleNamespace

import pytest

from guance_agent.orchestrator import (
    _freeze_research_requirements,
    _needs_research_report,
    _report_review_reserve,
    _retain_verified_facts_after_report_timeout,
)
from guance_agent.schemas import Claim, EvidenceRef, RunResult
from guance_agent.verify import verify_quotes


def _result():
    result = RunResult(
        run_id="budget-test", session_id="budget-test", status="completed",
        evidence_status="verified", answer="draft text must not survive",
        claims=[
            Claim(text="政策支持数字基础设施建设。", chunk_ids=["chunk-a"],
                  quotes=["支持数字基础设施建设"]),
            Claim(text="缺少来源的陈述。", chunk_ids=["missing"],
                  quotes=["缺少来源"]),
        ],
        evidence_refs=[EvidenceRef(
            planid="policy", chunk_id="chunk-a", title="政策规划", area="某地",
            fyp="十四五", admin_level=None, plantype_label=None, section_idx=1,
            section_path="支持措施", char_start=0, char_end=40, source="kb_read",
        )],
        dropped_chunk_ids=[], error=None,
        meta={"quote_verification": {"approved": 1, "unverified": 0}},
    )
    result._qa_report_supplied = True
    result._qa_report_dimensions = ["研究结论"]
    result._qa_report_candidates = [{"text": "unreviewed draft"}]
    return result


async def _verify_claim_quotes(result):
    evidence = {
        "chunk-a": SimpleNamespace(
            observed_ranges=[(0, 40)], document_id=None, generation_id=None),
    }
    ctx = SimpleNamespace(
        kb=SimpleNamespace(verify_quote=lambda _chunk, quote, *_args, **_kwargs:
                           quote == "支持数字基础设施建设"),
        evidence=evidence,
        settings=SimpleNamespace(tool_timeout=1),
        tool_calls=[], scope=[], route=None, question="",
    )
    result._qa_report_supplied = False
    await verify_quotes(result, ctx)
    result._qa_report_supplied = True


def test_reserve_mirrors_existing_review_token_timeout_cap():
    runtime = SimpleNamespace(settings=SimpleNamespace(
        llm_request_timeout=120, llm_max_output_tokens=3000))
    assert _report_review_reserve(runtime) == 58


def test_default_180_second_run_can_still_draft_and_review_once():
    runtime = SimpleNamespace(settings=SimpleNamespace(
        llm_request_timeout=120, llm_max_output_tokens=3000))
    reserve = _report_review_reserve(runtime)
    assert 180 - 35 - reserve >= 75


def test_simple_question_does_not_enter_optional_report_path():
    result = _result()
    result.meta["research_intent"] = {"primary": "fact", "need_report": False}
    assert not _needs_research_report(result)
    result.meta["research_intent"]["need_report"] = True
    assert _needs_research_report(result)


@pytest.mark.asyncio
async def test_planner_skips_when_its_call_would_consume_report_review_reserve():
    calls = []

    async def complete_text(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("planner must preserve the final report review reserve")

    runtime = SimpleNamespace(
        complete_text=complete_text,
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=20,
                                 llm_max_output_tokens=3000),
        meta={},
    )
    await _freeze_research_requirements(
        SimpleNamespace(question="梳理数字经济重点部署"), SimpleNamespace(), runtime,
        time.monotonic() + 50, None)
    assert not calls
    assert runtime.meta["research_requirement_planner"]["status"] == "skipped_budget"


@pytest.mark.asyncio
async def test_review_timeout_retains_only_source_linked_verified_facts_in_existing_schema():
    result = _result()
    result.claims[1].chunk_ids = ["chunk-a"]
    result.claims[1].quotes = ["未核验引文"]
    await _verify_claim_quotes(result)
    _retain_verified_facts_after_report_timeout(result)

    assert result.structured["schema_version"] == "qa-report/1"
    assert result.structured["verification"] == {
        "facts": "not_reviewed", "blocks": "not_reviewed"}
    assert len(result.structured["blocks"]) == 1
    block = result.structured["blocks"][0]
    assert block["kind"] == "fact"
    assert block["supporting_chunk_ids"] == ["chunk-a"]
    assert "政策支持数字基础设施建设" in result.answer
    assert "unreviewed draft" not in result.answer
    assert "缺少来源的陈述" not in result.answer
    assert result.meta["qa_report"]["retained"] == 1


def test_fabricated_quote_metadata_does_not_authorize_timeout_facts():
    result = _result()
    result.meta["quote_verification"]["unverified"] = 1
    result.meta["quote_verification"]["approved"] = 100
    _retain_verified_facts_after_report_timeout(result)

    assert result.structured["schema_version"] == "qa-report/1"
    assert result.structured["blocks"] == []
    assert result.answer
    assert "政策支持数字基础设施建设" not in result.answer


@pytest.mark.asyncio
async def test_timeout_receipt_is_invalidated_when_claim_content_changes():
    result = _result()
    result.claims = result.claims[:1]
    await _verify_claim_quotes(result)
    result.claims[0].text = "改写后的未核验结论。"

    _retain_verified_facts_after_report_timeout(result)

    assert result.structured["blocks"] == []
    assert "改写后的未核验结论" not in result.answer


@pytest.mark.asyncio
async def test_verified_claim_remains_authorized_when_merged_into_another_result():
    source = _result()
    source.claims = source.claims[:1]
    await _verify_claim_quotes(source)
    merged = _result()
    merged.claims = [source.claims[0]]

    _retain_verified_facts_after_report_timeout(merged)

    assert len(merged.structured["blocks"]) == 1
    assert merged.structured["blocks"][0]["supporting_claim_indexes"] == [0]


@pytest.mark.asyncio
async def test_timeout_fact_does_not_claim_analytical_dimension_coverage():
    result = _result()
    result.claims = result.claims[:1]
    result._qa_report_dimensions = ["地区差异", "实施效果"]
    await _verify_claim_quotes(result)

    _retain_verified_facts_after_report_timeout(result)

    assert result.structured["dimensions"] == ["已核验事实"]
    assert result.structured["blocks"][0]["dimension"] == "已核验事实"
    assert result.structured["coverage"]["covered"] == []
    assert result.structured["coverage"]["missing"] == ["地区差异", "实施效果"]
