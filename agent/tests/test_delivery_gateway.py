"""Last-mile delivery must project only verifier-authorized facts."""
import asyncio
from types import SimpleNamespace

import guance_agent.delivery_gateway as delivery_gateway
from guance_agent.schemas import Claim, EvidenceRef, RunError, RunResult, TaskRequest
from guance_agent.verify import (
    _authorize_number_verified_claim,
    _authorize_quote_verified_claim,
)


class _Registry:
    def __init__(self, refs):
        self.refs = {ref.chunk_id: ref for ref in refs}

    def get(self, chunk_id):
        return self.refs.get(chunk_id)


def _ref(chunk_id, planid="p1"):
    return EvidenceRef(
        planid=planid, chunk_id=chunk_id, title="政策", area="甲市", fyp="十四五",
        admin_level=None, plantype_label=None, section_idx=1, section_path="措施",
        char_start=0, char_end=20, source="kb_read", observed_ranges=[(0, 20)],
    )


def _result(claims, refs, answer=""):
    result = RunResult(
        run_id="run", session_id="session", status="completed",
        evidence_status="verified", answer=answer, claims=claims,
        evidence_refs=refs, dropped_chunk_ids=[], error=None,
        meta={"research_quality": {"status": "partial"}}, task_type="qa",
    )
    return result


def _task(question="分析政策措施"):
    return TaskRequest(
        run_id="run", session_id="session", question=question,
        created_at="2026-11-24T00:00:00+00:00", task_type="qa",
    )


def _authorize(result, claims):
    for claim in claims:
        _authorize_quote_verified_claim(claim)
        _authorize_number_verified_claim(result, claim)


def _ctx(refs):
    return SimpleNamespace(
        evidence=_Registry(refs), scope=None,
        route=SimpleNamespace(kind="open", planids=()), question="分析政策措施",
    )


def test_rejected_unverified_claim_cannot_be_appended_after_report_delivery():
    approved = Claim(text="政策建设灌溉设施", chunk_ids=["c1"], quotes=["建设灌溉设施"])
    rejected = Claim(text="该政策已全面落实", chunk_ids=["c2"], quotes=[])
    refs = [_ref("c1"), _ref("c2")]
    result = _result([approved, rejected], refs,
                     answer="模型称该政策已全面落实并取得明显成效")
    _authorize(result, [approved])
    result.structured = {
        "schema_version": "qa-report/1", "dimensions": ["措施"],
        "blocks": [
            {"id": "approved", "title_path": ["措施"], "dimension": "措施",
             "kind": "fact", "text": approved.text,
             "supporting_claim_indexes": [0], "supporting_chunk_ids": ["c1"]},
            {"id": "rejected", "title_path": ["成效"], "dimension": "措施",
             "kind": "fact", "text": rejected.text,
             "supporting_claim_indexes": [1], "supporting_chunk_ids": ["c2"]},
        ],
        "coverage": {"covered": ["措施"], "missing": [], "status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes",
                         "blocks": "model_reviewed_evidence_linked_semantics_not_proven"},
    }

    asyncio.run(delivery_gateway.finalize_public_delivery(result, _task(), _ctx(refs), None))

    assert "政策建设灌溉设施" in result.answer
    assert "该政策已全面落实" not in result.answer
    assert [claim.text for claim in result.claims] == [approved.text]
    assert [ref.chunk_id for ref in result.evidence_refs] == ["c1"]
    assert [block["id"] for block in result.structured["blocks"]] == ["approved"]
    assert result.structured["coverage"]["status"] == "partial"


def test_simple_qa_replaces_unsupported_nonnumeric_model_narrative():
    fact = Claim(text="政策提出建设灌溉设施", chunk_ids=["c1"], quotes=["建设灌溉设施"])
    ref = _ref("c1")
    result = _result([fact], [ref], answer="政策全面落实并显著提升了产量")
    _authorize(result, [fact])

    asyncio.run(delivery_gateway.finalize_public_delivery(
        result, _task("政策提出了什么措施？"), _ctx([ref]), None))

    assert result.answer == "- 政策提出建设灌溉设施"
    assert "全面落实" not in result.answer
    assert "显著提升" not in result.answer
    assert result.structured is None
    assert result.claims == [fact]


def test_unreviewed_analysis_is_not_published_even_with_verified_fact_claims():
    fact = Claim(text="政策提出建设灌溉设施", chunk_ids=["c1"], quotes=["建设灌溉设施"])
    ref = _ref("c1")
    result = _result([fact], [ref], answer="未审核分析：政策显著提高产量")
    _authorize(result, [fact])
    result.structured = {
        "schema_version": "qa-report/1", "dimensions": ["政策分析"],
        "blocks": [{
            "id": "analysis", "title_path": ["研究结论"],
            "dimension": "政策分析", "kind": "analysis",
            "text": "政策显著提高产量。", "supporting_claim_indexes": [0, 0],
            "supporting_chunk_ids": ["c1"],
        }],
        "coverage": {"covered": [], "missing": ["政策分析"], "status": "partial"},
        "verification": {"facts": "not_reviewed", "blocks": "not_reviewed"},
    }

    asyncio.run(delivery_gateway.finalize_public_delivery(result, _task(), _ctx([ref]), None))

    assert "政策显著提高产量" not in result.answer
    assert result.answer == "- 政策提出建设灌溉设施"
    assert result.structured is None


def test_scope_mismatch_removes_otherwise_authorized_claim():
    fact = Claim(text="政策提出建设灌溉设施", chunk_ids=["c1"], quotes=["建设灌溉设施"])
    ref = _ref("c1", planid="out-of-scope")
    result = _result([fact], [ref], answer="政策提出建设灌溉设施")
    _authorize(result, [fact])
    ctx = _ctx([ref])
    ctx.scope = ("in-scope",)

    asyncio.run(delivery_gateway.finalize_public_delivery(result, _task(), ctx, None))

    assert result.answer == "本轮没有取得足够可信的政策事实，无法形成研究结论。"
    assert result.claims == []
    assert result.evidence_refs == []


def test_numeric_claim_without_number_receipt_is_withheld():
    fact = Claim(text="补助达到30亿元", chunk_ids=["c1"], quotes=["补助支持企业"])
    ref = _ref("c1")
    result = _result([fact], [ref], answer="补助达到30亿元")
    _authorize_quote_verified_claim(fact)

    asyncio.run(delivery_gateway.finalize_public_delivery(result, _task(), _ctx([ref]), None))

    assert result.claims == []
    assert "30亿元" not in result.answer


def test_partial_result_keeps_verified_facts():
    fact = Claim(text="政策提出建设灌溉设施", chunk_ids=["c1"], quotes=["建设灌溉设施"])
    ref = _ref("c1")
    result = _result([fact], [ref], answer="未完成的自由文本")
    result.status = "limit_reached"
    result.error = RunError(code="RUN_TIMEOUT", message="任务超时")
    _authorize(result, [fact])

    asyncio.run(delivery_gateway.finalize_public_delivery(result, _task(), _ctx([ref]), None))

    assert result.status == "limit_reached"
    assert result.error.message == "任务超时"
    assert result.claims == [fact]
    assert result.answer == "- 政策提出建设灌溉设施"
    assert result.evidence_status == "insufficient"


def test_recovery_without_trusted_context_preserves_error_message():
    result = _result([], [], answer="模型自由文本")
    result.status = "failed"
    result.error = RunError(code="KB_UNAVAILABLE", message="知识库不可用")
    result.meta["execution_recovery"] = {"kind": "empty_search_only"}

    asyncio.run(delivery_gateway.finalize_public_delivery(result, _task(), None, None))

    assert result.answer == "知识库不可用"
    assert result.structured is None
    assert result.claims == []


def test_boundary_response_is_not_replaced_by_no_evidence_fallback():
    result = _result([], [], answer="我不能代替审批机关作出正式认定。")
    result.meta["qa_execution_subtype"] = "boundary_response"

    asyncio.run(delivery_gateway.finalize_public_delivery(result, _task(), None, None))

    assert result.answer == "我不能代替审批机关作出正式认定。"


def test_ambiguous_context_keeps_deterministic_clarification():
    clarification = "请明确要继续研究的政策文献。"
    result = _result([], [], answer="自由生成的答案")
    result.status = "insufficient"
    result.meta["qa_context_resolution"] = {
        "status": "ambiguous", "clarification": clarification,
    }

    asyncio.run(delivery_gateway.finalize_public_delivery(result, _task(), None, None))

    assert result.answer == clarification


def test_extract_structured_is_rebuilt_from_surviving_claims():
    fact = Claim(text="支持农业主体", chunk_ids=["c1"], quotes=["支持农业主体"],
                 dimension="适用对象")
    ref = _ref("c1")
    result = _result([fact], [ref], answer="模型私自生成的对象结论")
    result.task_type = "extract"
    result.structured = {"dimensions": ["适用对象"], "elements": [
        {"text": "未经核验的其他结论", "chunk_ids": ["c1"]}],
        "untrusted": "不要透传"}
    _authorize(result, [fact])

    task = _task("抽取政策要素")
    task.task_type = "extract"
    task.dimensions = ["适用对象"]
    asyncio.run(delivery_gateway.finalize_public_delivery(result, task, _ctx([ref]), None))

    assert result.structured["elements"] == [fact.model_dump(mode="json")]
    assert set(result.structured) == {"dimensions", "documents", "elements", "missing"}
    assert "untrusted" not in result.structured
    assert "未经核验" not in result.answer
    assert result.evidence_status == "insufficient"


def test_compare_cells_use_schema_ids_and_rebuild_only_verified_facts():
    fact = Claim(text="政策提出建设灌溉设施", chunk_ids=["c1"],
                 quotes=["建设灌溉设施"], dimension="措施")
    ref = _ref("c1", planid="p1")
    result = _result([fact], [ref], answer="未经核验的比较结论")
    result.task_type = "compare"
    _authorize(result, [fact])
    result.structured = {"matrix": [{"difference": "未经核验"}],
                         "analysis": "不要透传"}
    task = _task("比较政策措施")
    task.task_type = "compare"
    task.targets = ["p1", "p2"]
    task.dimensions = ["措施"]

    asyncio.run(delivery_gateway.finalize_public_delivery(
        result, task, _ctx([ref]), None))

    cells = result.structured["matrix"][0]["cells"]
    assert cells[0]["cell_id"] == "d0:p1"
    assert cells[0]["text"] == fact.text
    assert cells[1] == {"cell_id": "d0:p2", "planid": "p2",
                        "status": "insufficient"}
    assert "未经核验" not in result.answer
    assert result.structured["matrix"][0]["difference"]["status"] == "insufficient"


def test_semantically_reviewed_analysis_survives_gateway():
    facts = [
        Claim(text="政策支持农业生产", chunk_ids=["c1"], quotes=["支持农业生产"]),
        Claim(text="政策完善灌溉设施", chunk_ids=["c2"], quotes=["完善灌溉设施"]),
    ]
    refs = [_ref("c1"), _ref("c2")]
    result = _result(facts, refs, answer="未经审核的原模型答案")
    _authorize(result, facts)
    result.structured = {
        "schema_version": "qa-report/1", "dimensions": ["研究结论"],
        "blocks": [{
            "id": "analysis", "title_path": ["研究结论"],
            "dimension": "研究结论", "kind": "analysis",
            "text": "两项措施共同支持农业发展。",
            "supporting_claim_indexes": [0, 1],
            "supporting_chunk_ids": ["c1", "c2"],
            "verification": "model_reviewed_evidence_linked",
        }],
        "coverage": {"covered": ["研究结论"], "missing": [], "status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes",
                         "blocks": "model_reviewed_evidence_linked_semantics_not_proven"},
    }

    asyncio.run(delivery_gateway.finalize_public_delivery(
        result, _task(), _ctx(refs), None))

    assert "两项措施共同支持农业发展" in result.answer
    assert result.meta["research_delivery"]["audited_analysis"] is True


def test_partial_result_keeps_semantically_reviewed_analysis_and_marks_partial():
    facts = [
        Claim(text="政策支持农业生产", chunk_ids=["c1"], quotes=["支持农业生产"]),
        Claim(text="政策完善灌溉设施", chunk_ids=["c2"], quotes=["完善灌溉设施"]),
    ]
    refs = [_ref("c1"), _ref("c2")]
    result = _result(facts, refs, answer="未审核答案")
    result.status = "limit_reached"
    result.error = RunError(code="RUN_TIMEOUT", message="预算耗尽")
    _authorize(result, facts)
    result.structured = {
        "schema_version": "qa-report/1", "dimensions": ["研究结论"],
        "blocks": [{
            "id": "analysis", "title_path": ["研究结论"],
            "dimension": "研究结论", "kind": "analysis",
            "text": "两项措施共同支持农业发展。",
            "supporting_claim_indexes": [0, 1],
            "supporting_chunk_ids": ["c1", "c2"],
            "verification": "model_reviewed_evidence_linked",
        }],
        "coverage": {"covered": ["研究结论"], "missing": [], "status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes",
                         "blocks": "model_reviewed_evidence_linked_semantics_not_proven"},
    }

    asyncio.run(delivery_gateway.finalize_public_delivery(
        result, _task(), _ctx(refs), None))

    assert result.status == "limit_reached"
    assert result.error.message == "预算耗尽"
    assert "两项措施共同支持农业发展" in result.answer
    assert result.structured["coverage"]["status"] == "partial"
    assert result.meta["research_delivery"]["status"] == "partial"


def test_non_qa_keeps_task_schema_while_audited_analysis_stays_in_answer():
    facts = [
        Claim(text="政策支持农业生产", chunk_ids=["c1"], quotes=["支持农业生产"],
              dimension="措施"),
        Claim(text="政策完善灌溉设施", chunk_ids=["c2"], quotes=["完善灌溉设施"],
              dimension="措施"),
    ]
    refs = [_ref("c1"), _ref("c2")]
    result = _result(facts, refs, answer="未审核答案")
    result.task_type = "extract"
    _authorize(result, facts)
    result.structured = {
        "schema_version": "qa-report/1", "dimensions": ["措施"],
        "blocks": [{
            "id": "analysis", "title_path": ["措施分析"],
            "dimension": "措施", "kind": "analysis",
            "text": "两项措施共同支持农业发展。",
            "supporting_claim_indexes": [0, 1],
            "supporting_chunk_ids": ["c1", "c2"],
            "verification": "model_reviewed_evidence_linked",
        }],
        "coverage": {"covered": ["措施"], "missing": [], "status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes",
                         "blocks": "model_reviewed_evidence_linked_semantics_not_proven"},
    }
    task = _task("提取措施")
    task.task_type = "extract"
    task.dimensions = ["措施"]

    asyncio.run(delivery_gateway.finalize_public_delivery(
        result, task, _ctx(refs), None))

    assert "两项措施共同支持农业发展" in result.answer
    assert result.structured is not None
    assert "schema_version" not in result.structured
    assert len(result.structured["elements"]) == 2
    assert result.evidence_status == "insufficient"
    assert result.meta["research_delivery"]["status"] == "partial"
