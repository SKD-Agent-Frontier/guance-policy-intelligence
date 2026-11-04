"""Offline contract and lifecycle regression tests for unified research runs."""
import time
from types import SimpleNamespace

import pytest

from guance_agent.research_contract import (
    PolicyDocument,
    ResearchCoverage,
    ResearchPlan,
    ResearchScope,
    ResearchSubject,
    TaskContract,
)
from guance_agent.research_runtime import ResearchRuntime, ResearchRuntimeError
from guance_agent.schemas import TaskRequest


def contract(task_type="qa", **kwargs):
    task = TaskRequest(run_id="r", session_id="s", question="研究河北产业发展",
                       task_type=task_type, created_at="now", **kwargs)
    return TaskContract.from_task(task)


def selected_document(planid="p1", area="衡水市", fyp="第十三个五年规划"):
    return PolicyDocument(
        planid=planid, document_id=f"doc-{planid}", generation_id="generation-1",
        title=f"{area}{fyp}纲要", area=area, fyp=fyp, source="policy_catalog",
        source_verified=True,
    )


def test_contract_locks_each_existing_mode_and_initial_plan():
    for mode in ("qa", "organize", "extract", "compare", "compare_plans"):
        task_contract = contract(mode)
        plan = ResearchPlan.for_contract(task_contract)
        runtime = ResearchRuntime(task_contract, plan, time.monotonic() + 60)
        assert runtime.contract.task_type == mode
        assert runtime.contract.mode_locked is True
        assert {"kb_search", "kb_read", "evidence_verify"} <= set(plan.capabilities)


def test_plan_cannot_change_locked_task_goal_scope_or_five_year_period():
    task_contract = contract("qa")
    plan = ResearchPlan.for_contract(task_contract)
    with pytest.raises(ResearchRuntimeError, match="task_type"):
        ResearchRuntime(task_contract, plan.model_copy(update={"task_type": "compare"}),
                        time.monotonic() + 60)
    with pytest.raises(ResearchRuntimeError, match="research_goal"):
        ResearchRuntime(task_contract, plan.model_copy(update={"research_goal": "别的问题"}),
                        time.monotonic() + 60)

    scoped = TaskContract.model_copy(task_contract, update={
        "scope": ResearchScope(periods=["第十个五年规划"]),
    })
    for fyp in ("第十五个五年规划", "第十五五规划"):
        invalid = plan.model_copy(update={"scope": ResearchScope(periods=["第十个五年规划"]),
                                          "documents": [selected_document(fyp=fyp)]})
        with pytest.raises(ResearchRuntimeError, match="period"):
            ResearchRuntime(scoped, invalid, time.monotonic() + 60)


def test_subject_identity_is_distinct_and_region_parent_is_required():
    with pytest.raises(ValueError, match="separate identities"):
        ResearchPlan(task_type="qa", research_goal="g", subjects=[
            ResearchSubject(subject_id="same", label="衡水市"),
        ], documents=[selected_document("same")])
    with pytest.raises(ValueError, match="parent_subject_id"):
        ResearchPlan(task_type="qa", research_goal="g", subjects=[
            ResearchSubject(subject_id="city", label="衡水市", parent_subject_id="河北省"),
        ])


def test_transition_table_and_terminal_reason_are_enforced():
    task_contract = contract()
    runtime = ResearchRuntime(task_contract, ResearchPlan.for_contract(task_contract),
                              time.monotonic() + 60)
    with pytest.raises(ResearchRuntimeError, match="invalid transition"):
        runtime.transition("executing")
    runtime.transition("planning")
    runtime.transition("discovering")
    runtime.transition("ready")
    runtime.transition("executing")
    with pytest.raises(ResearchRuntimeError, match="requires a reason"):
        runtime.transition("failed")
    runtime.transition("failed", "offline fixture failure")
    with pytest.raises(ResearchRuntimeError, match="terminal status"):
        runtime.transition("planning")


def test_revision_is_real_bounded_and_cannot_expand_scope_or_capabilities():
    task_contract = contract("compare")
    plan = ResearchPlan.for_contract(task_contract)
    runtime = ResearchRuntime(task_contract, plan, time.monotonic() + 60, max_revisions=1)
    runtime.transition("planning")
    smaller_scope = plan.model_copy(update={"revision": 1,
        "scope": ResearchScope(areas=["衡水市"]),
    })
    revised = runtime.revise(smaller_scope, "目录中仅发现衡水市可核验的同期文献")
    assert revised.revision == 1
    assert runtime.revision_count == 1
    assert runtime.plan_versions[-1]["reason"] == "目录中仅发现衡水市可核验的同期文献"
    with pytest.raises(ResearchRuntimeError, match="budget exhausted"):
        runtime.revise(plan.model_copy(update={"revision": 2}), "再次尝试")


def test_revision_rejects_missing_trigger_and_scope_expansion():
    task_contract = contract()
    plan = ResearchPlan.for_contract(task_contract)
    runtime = ResearchRuntime(task_contract, plan, time.monotonic() + 60)
    runtime.transition("planning")
    with pytest.raises(ResearchRuntimeError, match="trigger reason"):
        runtime.revise(plan.model_copy(update={"revision": 1}), " ")


def test_discovery_requires_catalog_identity_and_revision_zero():
    task_contract = contract()
    plan = ResearchPlan.for_contract(task_contract)
    runtime = ResearchRuntime(task_contract, plan, time.monotonic() + 60)
    runtime.transition("planning")
    discovered = plan.model_copy(update={"documents": [selected_document()],
        "subjects": [ResearchSubject(subject_id="s1", label="衡水市", kind="region",
                                      document_ids=["p1"])],
    })
    accepted = runtime.accept_discovery(discovered)
    assert accepted.documents[0].source_verified
    invalid = discovered.model_copy(update={"revision": 1})
    with pytest.raises(ResearchRuntimeError, match="revision 0"):
        runtime.accept_discovery(invalid)


def test_deadline_and_revision_configuration_are_strict():
    task_contract = contract()
    plan = ResearchPlan.for_contract(task_contract)
    with pytest.raises(ValueError, match="max_revisions"):
        ResearchRuntime(task_contract, plan, time.monotonic() + 1, max_revisions=2)
    with pytest.raises(ValueError, match="deadline"):
        ResearchRuntime(task_contract, plan, float("inf"))
    runtime = ResearchRuntime(task_contract, plan, time.monotonic() - 1)
    assert runtime.deadline < time.monotonic()


def test_runtime_marks_uncovered_requested_subjects_partial_after_verified_claim():
    task_contract = contract("compare", dimensions=["产业结构"])
    plan = ResearchPlan.model_validate({
        **ResearchPlan.for_contract(task_contract).model_dump(mode="python"),
        "requested_subjects": ["衡水市", "保定市"],
        "subjects": [ResearchSubject(subject_id="city_hengshui", label="衡水市",
                                     kind="region", document_ids=["p1"])],
        "documents": [selected_document("p1")],
        "coverage": ResearchCoverage(
            required_subject_ids=["city_hengshui"],
            subject_gaps=["保定市"],
            required_dimensions=["产业结构"],
        ),
    })
    runtime = ResearchRuntime(task_contract, plan, time.monotonic() + 60)
    ref = SimpleNamespace(
        chunk_id="chunk1", planid="p1", document_id="doc-p1", generation_id="generation-1",
        document_version_id=None, source_hash=None,
    )

    class Evidence:
        def ids(self):
            return ["chunk1"]

        def get(self, _chunk_id):
            return ref

    result = SimpleNamespace(
        status="completed", error=None, answer="有证据支持的回答", evidence_status="verified",
        claims=[SimpleNamespace(chunk_ids=["chunk1"], dimension=None)],
        evidence_refs=[ref], structured={}, warnings=[], meta={},
    )
    runtime.transition("planning")
    runtime.transition("ready")
    runtime.transition("executing")
    evaluation = runtime.finish(result, SimpleNamespace(evidence=Evidence()))
    assert evaluation.status == "partial"
    assert "requested_subject_uncovered:保定市" in evaluation.gaps
    assert result.evidence_status == "verified"
    assert result.meta["research"]["status"] == "partial"
    assert result.warnings


def test_insufficient_result_keeps_legacy_none_evidence_status():
    task_contract = contract()
    runtime = ResearchRuntime(task_contract, ResearchPlan.for_contract(task_contract),
                              time.monotonic() + 60)
    runtime.transition("planning")
    runtime.transition("ready")
    runtime.transition("executing")
    result = SimpleNamespace(
        status="completed", error=None, answer="没有引用的回答", evidence_status="none",
        claims=[], evidence_refs=[], structured={}, warnings=[], meta={},
    )
    evaluation = runtime.finish(result, SimpleNamespace(evidence=None))
    assert evaluation.status == "insufficient"
    assert result.evidence_status == "none"
    assert result.meta["research"]["status"] == "insufficient"
    assert result.warnings


def test_expired_deadline_overrides_original_run_failure_in_public_result():
    task_contract = contract()
    runtime = ResearchRuntime(task_contract, ResearchPlan.for_contract(task_contract),
                              time.monotonic() - 1)
    result = SimpleNamespace(
        status="failed", error=SimpleNamespace(code="RUN_FAILED", message="slow upstream"),
        answer="", claims=[], evidence_refs=[], evidence_status="none", warnings=[], meta={},
    )
    evaluation = runtime.finish(result, SimpleNamespace(evidence=None))
    assert evaluation.status == "limit_reached"
    assert result.status == "limit_reached"
    assert result.error.code == "RUN_TIMEOUT"
    assert result.evidence_status == "insufficient"


@pytest.mark.parametrize("error_code", ["MAX_TURNS", "TOOL_BUDGET"])
def test_non_deadline_execution_limits_keep_legacy_error_code(error_code):
    task_contract = contract()
    runtime = ResearchRuntime(task_contract, ResearchPlan.for_contract(task_contract),
                              time.monotonic() + 60)
    result = SimpleNamespace(
        status="limit_reached", error=SimpleNamespace(code=error_code, message="bounded stop"),
        answer="", claims=[], evidence_refs=[], evidence_status="none", warnings=[], meta={},
    )
    evaluation = runtime.finish(result, SimpleNamespace(evidence=None))
    assert evaluation.status == "limit_reached"
    assert result.status == "limit_reached"
    assert result.error.code == error_code


def test_compare_requires_two_distinct_subjects_not_just_two_documents():
    """Two policy documents for one city must not satisfy a comparison contract."""
    for mode in ("compare", "compare_plans"):
        required = contract(mode).completion
        assert required.min_documents == 2
        assert required.min_subjects == 2
    assert contract("extract").completion.min_subjects == 1


def test_same_region_policy_comparisons_keep_distinct_research_subjects():
    """Policy identities, not city count, define same-city/scheme comparisons."""
    from guance_agent.research_planner import ResearchPlanner

    policies = [
        {"planid": "h1", "title": "衡水市产业规划", "area": "衡水市",
         "document_id": "d-h1", "generation_id": "g"},
        {"planid": "h2", "title": "衡水市科技政策", "area": "衡水市",
         "document_id": "d-h2", "generation_id": "g"},
    ]
    for mode in ("compare", "compare_plans"):
        task_contract = contract(mode)
        initial = ResearchPlanner.initial(task_contract)
        found = ResearchPlanner.with_catalog_documents(
            task_contract, initial, policies,
        )
        assert len(found.plan.documents) == 2
        assert len(found.plan.subjects) == 2
        assert len({subject.label for subject in found.plan.subjects}) == 2
        assert all(subject.kind == "policy_topic" for subject in found.plan.subjects)
        runtime = ResearchRuntime(task_contract, initial, time.monotonic() + 60)
        runtime.transition("planning")
        runtime.transition("discovering")
        runtime.accept_discovery(found.plan)


def test_explicit_qa_facets_prevent_false_complete_even_without_report():
    """F02: one verified fact cannot satisfy two user-requested QA facets."""
    contract_ = contract("qa", dimensions=["成本", "适用范围"])
    assert contract_.completion.required_dimensions == ["成本", "适用范围"]
    runtime = ResearchRuntime(contract_, ResearchPlan.for_contract(contract_),
                              time.monotonic() + 60)
    ref = SimpleNamespace(chunk_id="chunk1", planid="p1", document_id=None,
                          document_version_id=None, generation_id=None, source_hash=None)

    class Evidence:
        def ids(self):
            return ["chunk1"]
        def get(self, chunk_id):
            return ref if chunk_id == "chunk1" else None

    result = SimpleNamespace(
        status="completed", error=None, answer="有据可查的事实",
        evidence_status="verified",
        claims=[SimpleNamespace(chunk_ids=["chunk1"], dimension="成本")],
        evidence_refs=[ref], structured=None, warnings=[], meta={},
    )
    runtime.transition("planning")
    runtime.transition("ready")
    runtime.transition("executing")
    evaluation = runtime.finish(result, SimpleNamespace(evidence=Evidence()))
    assert evaluation.status != "completed"
    assert "dimension:适用范围" in evaluation.required_units
    assert "research_dimension_uncovered:适用范围" in evaluation.gaps


def test_qa_runtime_uses_itemized_requirement_support_instead_of_dimension_labels():
    task_contract = contract("qa", dimensions=["成本"])
    runtime = ResearchRuntime(task_contract, ResearchPlan.for_contract(task_contract),
                              time.monotonic() + 60)
    ref = SimpleNamespace(chunk_id="chunk1", planid="p1", document_id=None,
                          document_version_id=None, generation_id=None, source_hash=None)

    class Evidence:
        def ids(self):
            return ["chunk1"]
        def get(self, chunk_id):
            return ref if chunk_id == "chunk1" else None

    result = SimpleNamespace(
        status="completed", error=None, answer="有据可查的回答",
        evidence_status="verified",
        claims=[SimpleNamespace(chunk_ids=["chunk1"], dimension="成本")],
        evidence_refs=[ref], structured=None, warnings=[],
        meta={"research_quality": {
            "status": "partial", "required_dimensions": ["成本"],
            "covered_dimensions": ["成本"],
            "requirement_coverage": [{
                "id": "metric:cost", "kind": "metric", "label": "成本",
                "status": "missing", "phase": "evidence",
                "evidence_claim_indexes": [],
            }],
            "needs_report": False, "needs_analysis": False,
        }},
    )
    runtime.transition("planning")
    runtime.transition("ready")
    runtime.transition("executing")
    evaluation = runtime.finish(result, SimpleNamespace(evidence=Evidence()))

    assert evaluation.status == "partial"
    assert "requirement:metric:cost" in evaluation.required_units
    assert "research_requirement_uncovered:metric:cost" in evaluation.gaps
    assert result.meta["research"]["status"] == "partial"
    assert result.status == "completed"


def test_qa_runtime_accepts_all_verified_requirement_units():
    task_contract = contract("qa")
    runtime = ResearchRuntime(task_contract, ResearchPlan.for_contract(task_contract),
                              time.monotonic() + 60)
    ref = SimpleNamespace(chunk_id="chunk1", planid="p1", document_id=None,
                          document_version_id=None, generation_id=None, source_hash=None)

    class Evidence:
        def ids(self):
            return ["chunk1"]
        def get(self, chunk_id):
            return ref if chunk_id == "chunk1" else None

    result = SimpleNamespace(
        status="completed", error=None, answer="有据可查的回答",
        evidence_status="verified",
        claims=[SimpleNamespace(chunk_ids=["chunk1"], dimension=None)],
        evidence_refs=[ref], structured=None, warnings=[],
        meta={"research_quality": {
            "status": "complete", "requirement_coverage": [{
                "id": "subject:cost", "kind": "subject_fact", "label": "成本",
                "status": "covered", "phase": "evidence",
                "evidence_claim_indexes": [0],
            }],
            "needs_report": False, "needs_analysis": False,
        }},
    )
    runtime.transition("planning")
    runtime.transition("ready")
    runtime.transition("executing")
    evaluation = runtime.finish(result, SimpleNamespace(evidence=Evidence()))

    assert evaluation.status == "completed"
    assert evaluation.gaps == []
