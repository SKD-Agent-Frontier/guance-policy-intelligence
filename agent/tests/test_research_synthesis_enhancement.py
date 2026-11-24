"""Regression checks for evidence-organized synthesis and audited delivery."""
from guance_agent.research_delivery import (
    _append_completion,
    _render_structured_report,
    _research_map,
    verify_summary,
)
from guance_agent.research_quality import evaluate, requirements
from guance_agent.research_requirements import claim_fingerprint, requirements_fingerprint
from guance_agent.schemas import Claim, EvidenceRef, RunResult


def _result():
    claims = [
        Claim(text="规划提出建设公共算力平台。", chunk_ids=["chunk-a"],
              quotes=["建设公共算力平台"]),
        Claim(text="规划提出开展人工智能人才培养。", chunk_ids=["chunk-b"],
              quotes=["开展人工智能人才培养"]),
    ]
    refs = [EvidenceRef(
        planid=planid, chunk_id=chunk_id, title="某地规划", area="某市",
        fyp="十四五", admin_level=None, plantype_label=None, section_idx=1,
        section_path="重点任务", char_start=0, char_end=50, source="kb_read",
    ) for planid, chunk_id in (("P1", "chunk-a"), ("P2", "chunk-b"))]
    return RunResult(
        run_id="run", session_id="session", status="completed",
        evidence_status="verified", answer="", claims=claims, evidence_refs=refs,
        dropped_chunk_ids=[], error=None,
        meta={"quote_verification": {"unverified": 0}}, task_type="qa",
    )


def test_research_map_uses_only_current_semantically_reviewed_links():
    result = _result()
    question = "综合分析算力建设与人才培养的关系"
    units = requirements(question)["units"]
    result.meta["research_requirements"] = units
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(units),
        "supports": {units[0]["id"]: [0]},
    }
    facts = [
        {"index": 0, "claim_index": 0, "sources": [{"planid": "P1"}]},
        {"index": 1, "claim_index": 1, "sources": [{"planid": "P2"}]},
    ]

    outline = _research_map(result, facts, {"units": units})

    assert outline["link_basis"] == "semantic_reviewed_requirement_support"
    assert outline["requirement_links"][0]["fact_indexes"] == [0]
    assert outline["source_groups"] == [
        {"planid": "P1", "fact_indexes": [0]},
        {"planid": "P2", "fact_indexes": [1]},
    ]
    result.meta["requirement_support"]["claim_fingerprint"] = "stale"
    assert _research_map(result, facts, {"units": units})["link_basis"] == "none"
    assert _research_map(result, facts, {"units": units})["requirement_links"] == []


def test_draft_precheck_pass_is_not_semantic_delivery_approval():
    result = _result()
    candidate = {
        "id": "draft-analysis", "kind": "analysis",
        "text": "两项措施形成协同关系。", "support_signatures": [
            (claim.text, tuple(claim.chunk_ids)) for claim in result.claims],
    }

    precheck = verify_summary(result, [candidate])
    outcome = verify_summary(result, [candidate], require_semantic=True)

    assert precheck["approved"] == ["draft-analysis"]
    assert precheck["coverage"]["draft-analysis"] == 1.0
    assert outcome["approved"] == []
    assert outcome["coverage"]["draft-analysis"] == 0.0
    assert outcome["rejected"] == [{
        "block_id": "draft-analysis", "reason": "semantic_review_missing"}]


def test_only_final_semantically_reviewed_block_counts_as_audited_proposition():
    result = _result()
    block = {
        "id": "analysis-1", "kind": "analysis", "dimension": "综合判断",
        "text": "两项措施形成协同关系。", "supporting_chunk_ids": ["chunk-a", "chunk-b"],
        "verification": "model_reviewed_evidence_linked",
        "support_signatures": [(claim.text, tuple(claim.chunk_ids))
                               for claim in result.claims],
    }
    result.structured = {
        "schema_version": "qa-report/1", "blocks": [block],
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }

    outcome = verify_summary(result, [block])

    assert outcome["approved"] == ["analysis-1"]
    assert outcome["coverage"]["analysis-1"] == 1.0
    assert outcome["rejected"] == []


def test_same_id_with_different_text_or_support_is_not_audited():
    result = _result()
    final = {
        "id": "reused-id", "kind": "analysis", "dimension": "综合判断",
        "text": "两项措施形成协同关系。", "supporting_claim_indexes": [0],
        "supporting_chunk_ids": ["chunk-a"],
        "verification": "model_reviewed_evidence_linked",
    }
    result.structured = {
        "schema_version": "qa-report/1", "blocks": [final],
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    candidate = {**final, "text": "两项措施形成全面协同关系。",
                 "support_signatures": [(claim.text, tuple(claim.chunk_ids))
                                        for claim in result.claims]}

    outcome = verify_summary(result, [candidate], require_semantic=True)

    assert outcome["approved"] == []
    assert outcome["rejected"] == [{
        "block_id": "reused-id", "reason": "semantic_review_missing"}]


def test_report_rendering_drops_duplicate_text_but_keeps_conclusion_first():
    rendered = _render_structured_report({"blocks": [
        {"id": "fact", "kind": "fact", "title_path": ["依据"],
         "text": "规划提出建设算力平台。"},
        {"id": "conclusion", "kind": "analysis", "title_path": ["研究结论"],
         "text": "建设算力平台是该规划的重点部署。"},
        {"id": "repeat", "kind": "fact", "title_path": ["重复依据"],
         "text": "规划提出建设算力平台。"},
    ]})

    assert rendered.startswith("## 研究结论")
    assert rendered.count("规划提出建设算力平台。") == 1


def test_quality_counts_repeated_audited_analysis_as_one_judgment():
    result = _result()
    repeated = "建设与人才措施共同服务于数字产业发展。"
    result.answer = repeated
    block = {
        "kind": "analysis", "text": repeated,
        "supporting_chunk_ids": ["chunk-a", "chunk-b"],
        "verification": "model_reviewed_evidence_linked",
    }
    result.structured = {
        "schema_version": "qa-report/1", "coverage": {"status": "partial"},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
        "blocks": [
            {**block, "id": "first", "dimension": "综合判断"},
            {**block, "id": "duplicate", "dimension": "政策意义"},
        ],
    }

    quality = evaluate(result, "综合分析算力建设与人才培养的关系")

    assert quality["audited_analysis_blocks"] == 1
    assert quality["audited_analysis_propositions"] == 1


def test_partial_completion_discloses_scope_and_uncovered_requirements():
    result = _result()
    quality = {"status": "partial", "missing_requirements": ["实际实施效果"],
               "missing_dimensions": [], "gaps": ["audited_analysis_missing"],
               "verified_claim_count": 2}

    _append_completion(result, quality, {}, ["P1", "P2"])

    assert "研究范围与局限" in result.answer
    assert "planid=P1、planid=P2" in result.answer
    assert "尚未覆盖或尚未形成综合判断的要求：实际实施效果。" in result.answer
