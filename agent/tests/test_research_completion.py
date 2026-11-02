"""Round 4: unified research completion and delivery-state consistency."""
from types import SimpleNamespace

from guance_agent.evidence_coverage import build_coverage
from guance_agent.output import enforce_qa_scope
from guance_agent.research_delivery import deliver_research
from guance_agent.research_quality import evaluate
from guance_agent.schemas import Claim, EvidenceRef, RunResult


class Registry:
    def __init__(self, refs):
        self.refs = {ref.chunk_id: ref for ref in refs}

    def get(self, chunk_id):
        return self.refs.get(chunk_id)

    def ids(self):
        return list(self.refs)


def _ref(chunk_id, planid="2561", title="某政策"):
    return EvidenceRef(planid=planid, chunk_id=chunk_id, title=title,
                       area="某市", fyp="十四五", admin_level=None,
                       plantype_label=None, section_idx=1, section_path="措施",
                       char_start=0, char_end=20, source="kb_read")


def _claim(chunk_id, text="政策提出产业扶持措施"):
    return Claim(text=text, chunk_ids=[chunk_id], quotes=[text])


def _result(claims, refs, *, evidence_status="verified"):
    return RunResult(
        run_id="r", session_id="s", status="completed",
        evidence_status=evidence_status, answer="候选答案",
        claims=list(claims), evidence_refs=list(refs), dropped_chunk_ids=[],
        error=None, meta={"quote_verification": {"unverified": 0}},
        task_type="qa",
    )


def _ctx(refs, scope=None, kind="open", planids=(), question="某政策"):
    return SimpleNamespace(
        evidence=Registry(refs), scope=tuple(scope) if scope else None,
        route=SimpleNamespace(kind=kind, planids=tuple(planids)),
        question=question, tool_calls=[],
    )


def test_evidence_insufficient_cannot_be_quality_complete():
    ref = _ref("c1")
    result = _result([_claim("c1")], [ref], evidence_status="insufficient")
    quality = evaluate(result, "某政策的产业扶持是什么")
    assert quality["status"] != "complete"
    assert "evidence_insufficient" in quality["gaps"]


def test_removed_claims_force_partial_and_delivery_explains_gap():
    ref = _ref("c1")
    result = _result([_claim("c1")], [ref])
    result.meta["quote_verification"] = {"unverified": 0, "removed_claims": 1}
    result.meta["qa_execution_mode"] = "bounded_retrieval"
    quality = evaluate(result, "某政策的产业扶持是什么")
    assert quality["status"] == "partial"
    assert "claims_removed_or_answer_rebuilt" in quality["gaps"]
    deliver_research(result, "某政策的产业扶持是什么")
    assert result.meta["research_completion"]["status"] == "partial"
    assert "研究完成情况" not in result.answer
    assert "claims_removed_or_answer_rebuilt" not in result.answer


def test_scope_mismatch_removes_wrong_policy_claims():
    refs = [_ref("c1", "2561"), _ref("c2", "9999", "错误政策")]
    result = _result([_claim("c2", "错误政策提出产业扶持")], refs)
    ctx = _ctx(refs, scope=("2561",), kind="objects", planids=("2561",))
    assert enforce_qa_scope(result, ctx) == 1
    assert not result.claims
    assert result.meta["object_scope_filter"]["dropped_claims"] == 1
    quality = evaluate(result, "沧州政策提出产业扶持")
    assert quality["status"] != "complete"
    assert "object_scope_mismatch" in quality["gaps"]


def test_multi_document_coverage_gap_blocks_complete_and_is_delivered():
    refs = [_ref("c1", "A", "A规划"), _ref("c2", "B", "B规划")]
    result = _result([_claim("c1", "A政策提出产业扶持措施")], refs)
    ctx = _ctx(refs, kind="open")
    result.meta["evidence_coverage"] = build_coverage(
        result, "请整理两篇政策的主要内容", ctx)
    quality = evaluate(result, "请整理两篇政策的主要内容")
    assert quality["status"] == "partial"
    assert quality["missing_planids"] == []
    assert any(gap.startswith("coverage:multiple_documents") for gap in quality["gaps"])
    deliver_research(result, "请整理两篇政策的主要内容")
    assert result.meta["research_completion"]["status"] == "partial"
    assert "多文献要求" not in result.answer
    assert "coverage:multiple_documents" not in result.answer


def test_correct_single_document_completion_stays_complete():
    ref = _ref("c1")
    result = _result([_claim("c1", "产业扶持包括资金支持")], [ref])
    ctx = _ctx([ref], scope=("2561",), kind="objects", planids=("2561",),
               question="某政策的产业扶持是什么？")
    result.meta["evidence_coverage"] = build_coverage(
        result, "某政策的产业扶持是什么？", ctx)
    quality = evaluate(result, "某政策的产业扶持是什么？")
    assert quality["status"] == "complete"
    assert "研究完成情况" not in (deliver_research(result, "某政策的产业扶持是什么？") or result.answer)


def test_no_claims_is_insufficient_not_complete():
    result = _result([], [], evidence_status="insufficient")
    quality = evaluate(result, "某政策的具体补贴金额是多少？")
    assert quality["status"] == "insufficient"
    deliver_research(result, "某政策的具体补贴金额是多少？")
    assert result.meta["research_completion"]["status"] == "insufficient"


def test_semantic_review_pruning_requires_fresh_final_source_coverage():
    """Reviewed report may retain only one of two originally cited documents."""
    refs = [_ref("c1", "23132", "海西州"), _ref("c2", "45100", "双流区")]
    question = "请归纳两篇政策的共同点与差异"
    result = _result([_claim("c1"), _claim("c2")], refs)
    ctx = _ctx(refs, question=question)
    result.meta["evidence_coverage"] = build_coverage(result, question, ctx)
    assert result.meta["evidence_coverage"]["covered_planids"] == ["23132", "45100"]
    assert result.meta["evidence_coverage"]["status"] == "complete"

    # Model semantic review rejects one fact and its dependent report blocks.
    result.claims = result.claims[:1]
    result.evidence_refs = refs[:1]
    result.evidence_status = "insufficient"
    result.meta["answer_rebuilt"] = "semantic_review"
    result.meta["qa_execution_mode"] = "bounded_retrieval"
    # This is the final recomputation now performed in orchestrator after review.
    result.meta["evidence_coverage"] = build_coverage(result, question, ctx)
    coverage = result.meta["evidence_coverage"]
    assert coverage["covered_planids"] == ["23132"]
    assert coverage["status"] == "partial"
    assert "multiple_documents_required" in coverage["gaps"]
    deliver_research(result, question)
    completion = result.meta["research_completion"]
    assert completion["status"] == "partial"
    assert completion["covered_planids"] == ["23132"]
    assert "45100" not in completion["covered_planids"]


def test_audited_report_cannot_override_missing_final_document():
    """An audited block from A cannot declare A+B research complete."""
    question = "请比较两篇政策的共同点与差异"
    ref_a = _ref("c1", "A", "政策A")
    ref_b = _ref("c2", "B", "政策B")
    result = _result([_claim("c1", "两篇共同点涉及产业措施")], [ref_a])
    ctx = _ctx([ref_a, ref_b], kind="open")
    result.meta["evidence_coverage"] = build_coverage(result, question, ctx)
    assert result.meta["evidence_coverage"]["status"] == "partial"
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["地区差异"],
        "blocks": [{
            "id": "a-only", "kind": "analysis",
            "dimension": "地区差异", "title_path": ["共同点与差异"],
            "text": "两篇共同点涉及产业措施",
            "supporting_claim_indexes": [0], "supporting_chunk_ids": ["c1"],
            "verification": "model_reviewed_evidence_linked",
        }],
        "coverage": {"status": "complete", "covered": ["地区差异"], "missing": []},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    quality = evaluate(result, question)
    assert quality["status"] == "partial"
    assert quality["covered_planids"] == ["A"]
    assert "cross_document_analysis_missing" in quality["gaps"]
    assert any("multiple_documents" in item for item in quality["gaps"])


def test_cross_document_analysis_must_cite_distinct_surviving_policies():
    question = "请比较两篇政策的共同点与差异"
    refs = [_ref("c1", "A", "政策A"), _ref("c2", "B", "政策B")]
    result = _result([
        _claim("c1", "政策A涉及共同点"),
        _claim("c2", "政策B涉及共同点"),
    ], refs)
    ctx = _ctx(refs)
    result.meta["evidence_coverage"] = build_coverage(result, question, ctx)
    assert result.meta["evidence_coverage"]["status"] == "complete"
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["地区差异"],
        "blocks": [{
            "id": "comparison", "kind": "analysis",
            "dimension": "地区差异", "title_path": ["共同点与差异"],
            "text": "两篇政策的共同点是各自采取了一项措施",
            "supporting_claim_indexes": [0],
            "supporting_chunk_ids": ["c1"],
            "verification": "model_reviewed_evidence_linked",
        }],
        "coverage": {"status": "complete", "covered": ["地区差异"], "missing": []},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    quality = evaluate(result, question)
    assert quality["status"] == "partial"
    assert "cross_document_analysis_missing" in quality["gaps"]
    result.structured["blocks"][0]["supporting_claim_indexes"] = [0, 1]
    result.structured["blocks"][0]["supporting_chunk_ids"] = ["c1", "c2"]
    quality = evaluate(result, question)
    assert quality["status"] == "complete"
    assert quality["covered_planids"] == ["A", "B"]


def test_comparison_block_from_only_one_policy_not_delivered():
    """An audited single-policy block cannot masquerade as a comparison."""
    from guance_agent.qa_report_verification import _to_structured

    a = _ref("c1", "A", "A规划")
    b = _ref("c2", "B", "B规划")
    result = _result([_claim("c1", "A政策措施"), _claim("c2", "B政策措施")],
                     [a, b])
    result.meta["research_intent"] = {"primary": "compare"}
    result._qa_report_dimensions = ["地区差异"]
    result._qa_report_candidates = []
    first = result.claims[0]
    signature = (first.text, tuple(first.chunk_ids))
    candidate = {
        "id": "single-source-analysis",
        "dimension": "地区差异",
        "kind": "analysis", "text": "A与B没有共同点",
        "title_path": ["两地差异"], "support_signatures": [signature],
    }
    _to_structured(result, [candidate], {signature: (0, first)}, "model_reviewed")
    assert not result.structured["blocks"]
    assert result.structured["coverage"]["status"] == "partial"
    assert "A与B没有共同点" not in result.answer
