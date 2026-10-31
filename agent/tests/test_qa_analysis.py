"""QA analysis acceptance is about evidence dependencies, not proving semantic reasoning."""
from copy import deepcopy

from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.output import build_result
from guance_agent.qa_analysis import finalize_qa_analysis, prepare_candidates
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import AgentOutput, Claim, RunResult


def _claims():
    return [
        Claim(text="政策甲提出产业集聚措施。", chunk_ids=["a"], quotes=["产业集聚措施"]),
        Claim(text="政策乙提出企业数字化支持措施。", chunk_ids=["b"], quotes=["企业数字化支持措施"]),
        Claim(text="其他政策信息。", chunk_ids=["c"], quotes=["其他政策信息"]),
    ]


def _draft():
    return {"kind": "comparison", "text": "两项政策的政策工具作用对象不同。",
            "scope_note": "仅基于本轮取得的两条政策事实。",
            "supporting_claim_indexes": [0, 1]}


def _result():
    claims = _claims()
    result = RunResult(
        run_id="r1", session_id="s1", status="completed", evidence_status="verified",
        answer="模型声称两者有完全因果关系。", claims=claims, evidence_refs=[],
        dropped_chunk_ids=[], error=None,
        meta={"quote_verification": {
            "checked": 3, "approved": 3, "rejected": 0,
            "unverified": 0, "removed_claims": 0}},
        task_type="qa",
    )
    # Metadata-only references suffice for the *dependency* check.
    from types import SimpleNamespace
    result.evidence_refs = [SimpleNamespace(chunk_id=cid) for cid in ("a", "b", "c")]
    result._qa_analysis_candidates = prepare_candidates([_draft()], claims, claims)
    return result


def test_analysis_depends_on_two_distinct_retained_facts():
    result = _result()
    finalize_qa_analysis(result)
    analysis = result.structured["analyses"][0]
    assert analysis["supporting_claim_indexes"] == [0, 1]
    assert analysis["supporting_chunk_ids"] == ["a", "b"]
    assert analysis["verification"] == "evidence_linked_only"
    assert "完全因果关系" not in result.answer
    assert result.structured["verification"]["analysis"].endswith("semantics_not_verified")
    assert result._qa_analysis_candidates == []
    assert "_qa_analysis_candidates" not in result.model_dump()


def test_missing_or_unverified_source_discards_analyses():
    cases = [
        lambda r: r.claims.pop(0),
        lambda r: r.evidence_refs.pop(0),
        lambda r: r.meta.pop("quote_verification"),
        lambda r: r.meta["quote_verification"].update(unverified=1),
        lambda r: r.claims[0].chunk_ids.clear(),
    ]
    for change in cases:
        result = _result()
        change(result)
        finalize_qa_analysis(result)
        assert result.structured is None
        assert result.meta["analysis_contract"]["accepted"] == 0


def test_bad_model_dependencies_never_become_analysis():
    claims = _claims()
    for value in [
        {**_draft(), "supporting_claim_indexes": [0, 99]},
        {**_draft(), "supporting_claim_indexes": [0]},
        {**_draft(), "supporting_claim_indexes": [0, 0]},
        {**_draft(), "supporting_claim_indexes": [0, True]},
        {**_draft(), "kind": "unsupported"},
        {**_draft(), "scope_note": ""},
    ]:
        assert prepare_candidates([value], claims, claims) == []


def test_removed_policy_fact_invalidates_analysis_even_if_indexes_shift():
    result = _result()
    result.claims = result.claims[1:]
    # Index 0 is now another policy fact: analysis must not silently rebind it.
    finalize_qa_analysis(result)
    assert result.structured is None


def test_backwards_compatible_unstructured_qa_and_private_candidates(env):
    s, task = env
    ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
    hits = ctx.kb.search("农业", "甲", None, 2)
    ctx.evidence.add_result(hits, "kb_search")
    ids = ctx.evidence.ids()
    if len(ids) < 2:
        return  # Sample fixture can legitimately have one hit; explicit unit tests cover 2.
    claims = [
        Claim(text="政策事实一", chunk_ids=[ids[0]], quotes=["政策事实一"]),
        Claim(text="政策事实二", chunk_ids=[ids[1]], quotes=["政策事实二"]),
    ]
    output = AgentOutput(answer="未经核验的摘要", claims=claims, analyses=[
        {**_draft(), "supporting_claim_indexes": [0, 1]}])
    result = build_result(task, ExecOutcome(output, None), ctx, {})
    assert result.structured is None
    assert result._qa_analysis_candidates
    assert "_qa_analysis_candidates" not in result.model_dump_json()
    # Without the actual post-run quote verification stage no analysis is deliverable.
    finalize_qa_analysis(result)
    assert result.structured is None
