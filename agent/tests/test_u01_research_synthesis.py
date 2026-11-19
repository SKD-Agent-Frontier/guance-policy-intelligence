"""U01 conclusion ordering and deterministic evidence checks."""

import json
import time
from types import SimpleNamespace

import pytest

from guance_agent.research_delivery import (
    _render_structured_report,
    deliver_research,
    draft_verified_report,
    verify_summary,
)
from guance_agent.schemas import Claim, EvidenceRef, RunResult


def _result():
    claims = [
        Claim(text="政策支持数字基础设施建设。", chunk_ids=["chunk-a"],
              quotes=["支持数字基础设施建设"]),
        Claim(text="政策支持数字人才培养。", chunk_ids=["chunk-b"],
              quotes=["支持数字人才培养"]),
    ]
    refs = [
        EvidenceRef(planid="policy", chunk_id=chunk_id, title="政策规划", area="某地",
                    fyp="十四五", admin_level=None, plantype_label=None,
                    section_idx=1, section_path="支持措施", char_start=0, char_end=40,
                    source="kb_read")
        for chunk_id in ("chunk-a", "chunk-b")
    ]
    return RunResult(
        run_id="u01", session_id="u01", status="completed", evidence_status="verified",
        answer="", claims=claims, evidence_refs=refs, dropped_chunk_ids=[], error=None,
        meta={"quote_verification": {"unverified": 0}}, task_type="qa",
    )


def _analysis(result, text="两类措施共同支持数字经济。"):
    return {
        "id": "conclusion", "kind": "analysis", "text": text,
        "support_signatures": [
            (claim.text, tuple(claim.chunk_ids)) for claim in result.claims
        ],
    }


@pytest.mark.asyncio
async def test_draft_prompt_requests_conclusion_before_facts_without_schema_change():
    result = _result()
    captured = {}

    async def complete_text(messages, **kwargs):
        captured["system"] = messages[0]["content"]
        return json.dumps({
            "dimensions": ["研究结论", "支持措施"],
            "blocks": [
                {"id": "fact", "title_path": ["支持措施"],
                 "dimension": "支持措施", "kind": "fact",
                 "supporting_claim_indexes": [0]},
                {"id": "conclusion", "title_path": ["研究结论"],
                 "dimension": "研究结论", "kind": "analysis",
                 "text": "两类措施共同支持数字经济。",
                 "supporting_claim_indexes": [0, 1]},
            ],
        }, ensure_ascii=False)

    runtime = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
        complete_text=complete_text,
    )
    assert await draft_verified_report(
        result, "请梳理数字经济支持措施", runtime, time.monotonic() + 120,
        force=True,
    )
    assert "先输出1–2个“研究结论”analysis块" in captured["system"]
    assert result._qa_report_candidates[1]["support_signatures"]
    rendered = _render_structured_report({"blocks": result._qa_report_candidates})
    assert rendered.splitlines()[0] == "## 研究结论"
    assert rendered.index("## 研究结论") < rendered.index("## 支持措施")


def test_summary_high_coverage_approved():
    result = _result()
    outcome = verify_summary(result, [_analysis(result)])
    assert outcome == {
        "checked": 1, "approved": ["conclusion"], "rejected": [],
        "coverage": {"conclusion": 1.0},
    }
    assert result.meta["summary_verification"] == outcome


def test_summary_low_coverage_rejected_for_dead_signature():
    result = _result()
    block = _analysis(result)
    block["support_signatures"][1] = ("已撤回的事实", ("chunk-b",))
    outcome = verify_summary(result, [block])
    assert outcome["approved"] == []
    assert outcome["rejected"] == [
        {"block_id": "conclusion", "reason": "low_evidence_coverage"},
    ]
    assert outcome["coverage"]["conclusion"] == 0.0


def test_summary_number_must_appear_in_supporting_quote():
    result = _result()
    block = _analysis(result, "两类措施预计带动30个项目。")
    outcome = verify_summary(result, [block])
    assert outcome["rejected"] == [
        {"block_id": "conclusion", "reason": "number_not_in_quotes"},
    ]


def test_summary_ignores_empty_and_non_analysis_blocks():
    result = _result()
    outcome = verify_summary(result, [
        _analysis(result, ""),
        {"id": "fact", "kind": "fact", "text": "政策支持数字经济。"},
    ])
    assert outcome == {"checked": 0, "approved": [], "rejected": [], "coverage": {}}


def test_summary_ignores_markup_lines_in_coverage():
    result = _result()
    block = _analysis(result, "# 研究结论\n> 原文引句\n| 数值 |\n```\n10\n```\n两类措施形成支持。")
    outcome = verify_summary(result, [block])
    assert outcome["coverage"]["conclusion"] == 1.0


def _fact_candidate(result, index, *, block_id=None, text=None):
    claim = result.claims[index]
    return {
        "id": block_id or f"fact-{index}", "title_path": [f"政策事实{index + 1}"],
        "dimension": "支持措施", "kind": "fact",
        "text": claim.text if text is None else text,
        "support_signatures": [(claim.text, tuple(claim.chunk_ids))],
    }


def test_zero_verified_facts_cannot_form_conclusion():
    result = _result()
    result.claims = []
    result.evidence_refs = []
    result.answer = "未经核验的模型结论：政策已全面落实。"
    deliver_research(result, "分析数字经济支持措施")
    assert "无法形成研究结论" in result.answer
    assert "未经核验的模型结论" not in result.answer
    assert result.meta["research_delivery"]["format"] != "partial_report"


def test_one_verified_fact_stays_a_bounded_answer():
    result = _result()
    result.claims = result.claims[:1]
    result.evidence_refs = result.evidence_refs[:1]
    deliver_research(result, "分析数字经济支持措施")
    assert result.meta["research_delivery"]["format"] == "fact_answer"
    assert result.answer.count("政策支持数字基础设施建设") == 1
    assert "##" not in result.answer


def test_one_fact_with_candidate_still_uses_bounded_fact_answer():
    result = _result()
    result.claims = result.claims[:1]
    result.evidence_refs = result.evidence_refs[:1]
    result._qa_report_candidates = [_fact_candidate(result, 0)]
    deliver_research(result, "分析数字经济支持措施")
    assert result.meta["research_delivery"]["format"] == "fact_answer"
    assert result.answer.count("政策支持数字基础设施建设") == 1
    assert "##" not in result.answer


def test_audited_multi_fact_report_starts_with_conclusion():
    result = _result()
    result.structured = {
        "schema_version": "qa-report/1",
        "verification": {"facts": "semantic_reviewed_against_quotes"},
        "blocks": [
            {"id": "fact", "title_path": ["支持措施"], "kind": "fact",
             "text": result.claims[0].text, "supporting_claim_indexes": [0],
             "supporting_chunk_ids": ["chunk-a"]},
            {"id": "conclusion", "title_path": ["研究结论"], "kind": "analysis",
             "text": "两类措施共同支持数字经济。", "supporting_claim_indexes": [0, 1],
             "supporting_chunk_ids": ["chunk-a", "chunk-b"]},
        ],
    }
    deliver_research(result, "分析数字经济支持措施")
    assert result.meta["research_delivery"]["format"] == "evidence_linked_report"
    assert result.answer.startswith("## 研究结论")
    assert result.meta["pipeline_funnel"]["blocks_shown"] == 2


def test_audited_analysis_with_unquoted_number_is_removed_before_delivery():
    result = _result()
    result.structured = {
        "schema_version": "qa-report/1",
        "verification": {"facts": "semantic_reviewed_against_quotes"},
        "blocks": [
            {"id": "fact", "title_path": ["支持措施"], "kind": "fact",
             "text": result.claims[0].text, "supporting_claim_indexes": [0],
             "supporting_chunk_ids": ["chunk-a"]},
            {"id": "rejected", "title_path": ["研究结论"], "kind": "analysis",
             "text": "预计带动30个项目。", "supporting_claim_indexes": [0, 1],
             "supporting_chunk_ids": ["chunk-a", "chunk-b"]},
        ],
    }
    deliver_research(result, "分析数字经济支持措施")
    assert "预计带动30个项目" not in result.answer
    assert result.meta["summary_verification"]["rejected"] == [
        {"block_id": "rejected", "reason": "number_not_in_quotes"},
    ]
    assert result.meta["pipeline_funnel"]["blocks_shown"] == 1


def test_partial_multidimensional_report_keeps_supported_fact_and_warns_gap():
    result = _result()
    result._qa_report_candidates = [
        _fact_candidate(result, 0),
        _fact_candidate(result, 1, block_id="rejected", text="无事实依据的主题"),
    ]
    deliver_research(result, "分析数字经济支持措施的优势和挑战")
    assert result.meta["research_delivery"]["format"] == "partial_report"
    assert "政策支持数字基础设施建设" in result.answer
    assert "无事实依据的主题" not in result.answer
    assert any("rejected" in warning for warning in result.warnings)


def test_partial_report_never_renders_unreviewed_candidate_title():
    result = _result()
    candidate = _fact_candidate(result, 0)
    candidate["title_path"] = ["已投入30亿元"]
    result._qa_report_candidates = [candidate, _fact_candidate(result, 1)]
    deliver_research(result, "分析数字经济支持措施")
    assert result.meta["research_delivery"]["format"] == "partial_report"
    assert result.answer.startswith("## 已核验事实")
    assert result.answer.count("## 已核验事实") == 1
    assert "已投入30亿元" not in result.answer
    assert "政策支持数字人才培养" in result.answer


def test_partial_review_failure_never_shows_rejected_analysis_or_fact():
    result = _result()
    result._qa_report_candidates = [
        _fact_candidate(result, 0), _fact_candidate(result, 1),
        _fact_candidate(result, 1, block_id="rejected", text="被拒事实正文"),
        _analysis(result, "未经语义审核的分析正文。"),
    ]
    deliver_research(result, "分析数字经济支持措施")
    assert result.meta["research_delivery"]["format"] == "partial_report"
    assert "政策支持数字基础设施建设" in result.answer
    assert "政策支持数字人才培养" in result.answer
    assert "被拒事实正文" not in result.answer
    assert "未经语义审核的分析正文" not in result.answer
    assert any("rejected" in warning for warning in result.warnings)
    assert any("semantic_review_missing" in warning for warning in result.warnings)


def test_all_candidate_blocks_rejected_falls_back_to_fact_answer():
    result = _result()
    result._qa_report_candidates = [
        _fact_candidate(result, 0, text="错误的事实正文"),
        _analysis(result, "未经语义审核的分析正文。"),
    ]
    deliver_research(result, "分析数字经济支持措施")
    assert result.meta["research_delivery"]["format"] == "fact_answer"
    assert "错误的事实正文" not in result.answer
    assert "未经语义审核的分析正文" not in result.answer
    assert "政策支持数字基础设施建设" in result.answer


@pytest.mark.asyncio
async def test_draft_budget_skip_retains_verified_facts():
    result = _result()
    calls = []

    async def complete_text(*args, **kwargs):
        calls.append(1)
        raise AssertionError("draft must not run")

    runtime = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
        complete_text=complete_text,
    )
    assert not await draft_verified_report(
        result, "分析数字经济支持措施", runtime, time.monotonic() + 40,
    )
    deliver_research(result, "分析数字经济支持措施")
    assert not calls
    assert result.meta["research_delivery"]["format"] == "fact_answer"
    assert result.meta["research_quality"]["status"] == "partial"
    assert "政策支持数字基础设施建设" in result.answer


def test_funnel_counts_never_exceed_generated_blocks():
    result = _result()
    result._qa_report_candidates = [_fact_candidate(result, 0)]
    deliver_research(result, "分析数字经济支持措施")
    funnel = result.meta["pipeline_funnel"]
    assert funnel["blocks_shown"] <= funnel["blocks_approved"] <= funnel["blocks_generated"]
    assert (funnel["blocks_generated"], funnel["blocks_approved"],
            funnel["blocks_shown"]) == (1, 1, 1)
