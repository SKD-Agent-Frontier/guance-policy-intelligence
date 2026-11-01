"""Unit boundaries between legacy QA analysis and structured QA reports.

These tests cover orchestration state only; they are not semantic-review
acceptance tests or real-model validation.
"""

from types import SimpleNamespace

from guance_agent.literal_keywords import enforce_literal_result
from guance_agent.qa_analysis import finalize_qa_analysis, prepare_candidates
from guance_agent.qa_report_verification import prepare_report_candidates, prune_report
from guance_agent.schemas import Claim, EvidenceRef, QABlock, QAReportDraft, RunResult


def _result_with_report(answer, structured):
    claims = [
        Claim(text="政策甲提出产业集聚措施。", chunk_ids=["chunk-a"],
              quotes=["产业集聚措施"]),
        Claim(text="政策乙提出企业数字化支持措施。", chunk_ids=["chunk-b"],
              quotes=["企业数字化支持措施"]),
    ]
    result = RunResult(
        run_id="run-1",
        session_id="session-1",
        status="completed",
        evidence_status="verified",
        answer=answer,
        claims=claims,
        evidence_refs=[],
        dropped_chunk_ids=[],
        error=None,
        meta={"analysis_drafts_received": 1},
        task_type="qa",
        structured=structured,
    )
    result._qa_report_supplied = True
    result._qa_analysis_candidates = prepare_candidates(
        [{
            "kind": "comparison",
            "text": "两项政策的政策工具作用对象不同。",
            "scope_note": "仅基于本轮取得的两条政策事实。",
            "supporting_claim_indexes": [0, 1],
        }],
        claims,
        claims,
    )
    assert result._qa_analysis_candidates[0]["support"] == [
        (claims[0].text, ("chunk-a",)),
        (claims[1].text, ("chunk-b",)),
    ]
    return result


def test_supplied_report_blocks_legacy_analysis_overwriting_or_publishing():
    answer = "# 政策工具\n\n经审核保留的报告段落。"
    structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["政策工具"],
        "blocks": [{
            "id": "fact-1",
            "title_path": ["政策工具"],
            "dimension": "政策工具",
            "kind": "fact",
            "text": "经审核保留的报告段落。",
            "supporting_claim_indexes": [0],
            "supporting_chunk_ids": ["chunk-a"],
            "verification": "model_reviewed_evidence_linked",
        }],
        "coverage": {"covered": ["政策工具"], "missing": [], "status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes",
                         "blocks": "model_reviewed_evidence_linked_semantics_not_proven"},
    }
    result = _result_with_report(answer, structured)

    finalized = finalize_qa_analysis(result)

    assert finalized is result
    assert result.answer == answer
    assert result.structured == structured
    assert result.structured["schema_version"] == "qa-report/1"
    assert result._qa_analysis_candidates == []
    assert "analyses" not in result.structured


def test_empty_withdrawn_report_cannot_be_revived_by_legacy_analysis():
    answer = "本轮未形成可核验的报告内容；请查看检索与核验提示。"
    structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["政策工具", "适用对象"],
        "blocks": [],
        "coverage": {
            "covered": [],
            "missing": ["政策工具", "适用对象"],
            "status": "partial",
        },
        "verification": {"facts": "not_reviewed", "blocks": "not_reviewed"},
    }
    result = _result_with_report(answer, structured)

    finalize_qa_analysis(result)

    assert result.answer == answer
    assert result.structured == structured
    assert result.structured["blocks"] == []
    assert result._qa_analysis_candidates == []
    assert "analyses" not in result.structured


def test_literal_keyword_filter_prunes_only_report_blocks_for_removed_claims():
    claims = [
        Claim(text="某地推动数字化与经济发展协同。", chunk_ids=["chunk-a"],
              quotes=["数字化与经济发展"]),
        Claim(text="某地支持数字经济发展。", chunk_ids=["chunk-b"],
              quotes=["支持数字经济发展"]),
    ]
    report = QAReportDraft(
        dimensions=["非连续关键词", "完整关键词"],
        blocks=[
            QABlock(id="block-a", title_path=["数字化与经济"],
                    dimension="非连续关键词", kind="fact",
                    text="这段应随未命中的事实撤回。", supporting_claim_indexes=[0]),
            QABlock(id="block-b", title_path=["数字经济"],
                    dimension="完整关键词", kind="fact",
                    text="这段依赖完整关键词命中。", supporting_claim_indexes=[1]),
        ],
    )
    refs = [
        EvidenceRef(planid="plan-a", chunk_id="chunk-a", title="普通政策标题",
                    area=None, fyp=None, admin_level=None, plantype_label=None,
                    section_idx=None, section_path="第一章", char_start=0,
                    char_end=20, source="kb_search"),
        EvidenceRef(planid="plan-b", chunk_id="chunk-b", title="普通政策标题",
                    area=None, fyp=None, admin_level=None, plantype_label=None,
                    section_idx=None, section_path="第一章", char_start=0,
                    char_end=20, source="kb_search"),
    ]
    result = RunResult(
        run_id="run-2",
        session_id="session-2",
        status="completed",
        evidence_status="verified",
        answer="原始回答。",
        claims=claims,
        evidence_refs=refs,
        dropped_chunk_ids=[],
        error=None,
        meta={"qa_report": {"received": 2}},
        task_type="qa",
    )
    result._qa_report_supplied = True
    result._qa_report_dimensions = list(report.dimensions)
    result._qa_report_candidates = prepare_report_candidates(report, claims)

    enforce_literal_result(
        result,
        SimpleNamespace(literal_keyword="数字经济", literal_hit_ids={"chunk-b"}),
    )
    prune_report(result, None, "literal_keyword")

    assert [claim.chunk_ids for claim in result.claims] == [["chunk-b"]]
    assert [block["id"] for block in result._qa_report_candidates] == ["block-b"]
    assert result._qa_report_candidates[0]["support_signatures"] == [
        (claims[1].text, ("chunk-b",)),
    ]
    assert result.meta["qa_report"]["missing_dimensions"] == ["非连续关键词"]
    assert "这段应随未命中的事实撤回。" not in result.answer
    assert "这段依赖完整关键词命中。" in result.answer
