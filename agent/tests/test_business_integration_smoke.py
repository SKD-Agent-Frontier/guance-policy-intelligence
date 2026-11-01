import sys
from pathlib import Path

from guance_agent.schemas import Claim, EvidenceRef, RunResult


def _checks(*args):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from business_integration_smoke import _case_checks

    return _case_checks(*args)


def _result(*, structured=None, report_meta=None):
    claim = Claim(text="政策提出人才培养任务", chunk_ids=["chunk-1"], quotes=["逐字原句"])
    ref = EvidenceRef(
        planid="policy-1", chunk_id="chunk-1", title="人才政策", area="甲市", fyp=None,
        admin_level=None, plantype_label=None, section_idx=0, section_path="任务",
        char_start=0, char_end=20, source="kb_read",
    )
    meta = {"quote_verification": {"approved": 1, "rejected": 0, "unverified": 0}}
    if report_meta is not None:
        meta["qa_report"] = report_meta
    return RunResult(run_id="r", session_id="s", status="completed",
                     evidence_status="verified", answer="已核验内容", claims=[claim],
                     evidence_refs=[ref], dropped_chunk_ids=[], error=None, meta=meta,
                     structured=structured)


def test_smoke_accepts_legacy_qa_only_without_a_report():
    result = _result()

    checks, _ = _checks("legacy", "qa", result, None, None, None, True, True)

    assert checks["qa_legacy_without_report"] is True
    assert checks["completed_with_verified_claims"] is True


def test_smoke_validates_qa_report_blocks_and_final_claim_mapping():
    result = _result(
        structured={
            "schema_version": "qa-report/1", "dimensions": ["人才培养"],
            "blocks": [{"id": "b1", "title_path": ["人才培养"],
                       "dimension": "人才培养", "kind": "fact", "text": "政策提出人才培养任务",
                       "supporting_claim_indexes": [0], "supporting_chunk_ids": ["chunk-1"]}],
            "coverage": {"covered": ["人才培养"], "missing": [], "status": "complete"},
        },
        report_meta={"received": 1, "withdrawn": 0, "declared_insufficient": False},
    )
    result.structured["blocks"][0]["verification"] = "model_reviewed_evidence_linked"
    result.structured["verification"] = {
        "facts": "semantic_reviewed_against_quotes",
        "blocks": "model_reviewed_evidence_linked_semantics_not_proven",
    }

    checks, _ = _checks("report", "qa", result, None, None, None, True, True)

    assert checks["qa_report_contract"] is True
    assert checks["completed_with_verified_claims"] is True

    result.structured["blocks"][0]["supporting_claim_indexes"] = [4]
    checks, _ = _checks("report", "qa", result, None, None, None, True, True)
    assert checks["qa_report_contract"] is False

    result.structured["blocks"][0]["supporting_claim_indexes"] = [0, 0]
    checks, _ = _checks("report", "qa", result, None, None, None, True, True)
    assert checks["qa_report_contract"] is False

    result.structured["blocks"][0]["supporting_claim_indexes"] = [0]
    extra_ref = result.evidence_refs[0].model_copy(update={"chunk_id": "chunk-2"})
    result.evidence_refs.append(extra_ref)
    result.structured["blocks"][0]["supporting_chunk_ids"] = ["chunk-1", "chunk-2"]
    checks, _ = _checks("report", "qa", result, None, None, None, True, True)
    assert checks["qa_report_contract"] is False

    result.structured["blocks"][0]["supporting_chunk_ids"] = ["chunk-1"]
    result.structured["blocks"][0]["verification"] = "not_reviewed"
    checks, _ = _checks("report", "qa", result, None, None, None, True, True)
    assert checks["qa_report_contract"] is False

    result.structured["blocks"][0]["verification"] = "model_reviewed_evidence_linked"
    result.structured["verification"]["facts"] = "not_reviewed"
    checks, _ = _checks("report", "qa", result, None, None, None, True, True)
    assert checks["qa_report_contract"] is False
