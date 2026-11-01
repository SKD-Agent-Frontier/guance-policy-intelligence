"""Integration checks that discovery revisions change the actual execution inputs."""
import time
from dataclasses import replace

from guance_agent.orchestrator import run_task
from guance_agent.research_contract import TaskContract
from guance_agent.research_planner import ResearchPlanner
from guance_agent.research_runtime import ResearchRuntime
from guance_agent.runtime import Runtime
from guance_agent.schemas import RunResult, TaskRequest


def _task():
    return TaskRequest(
        run_id="run_compare",
        session_id="session_compare",
        question="比较衡水市、保定市和邢台市的产业结构政策",
        created_at="2026-10-31T00:00:00Z",
        task_type="compare",
        dimensions=["产业结构"],
    )


def _discovery(task, contract):
    plan = ResearchPlanner.initial(contract)
    documents = [
        {
            "planid": "doc_hengshui",
            "title": "衡水市第十四个五年规划纲要",
            "area": "衡水市",
            "fyp": "第十四个五年规划",
            "document_id": "source_hengshui",
            "generation_id": "generation_1",
        },
        {
            "planid": "doc_baoding",
            "title": "保定市第十四个五年规划纲要",
            "area": "保定市",
            "fyp": "第十四个五年规划",
            "document_id": "source_baoding",
            "generation_id": "generation_1",
        },
    ]
    return ResearchPlanner.with_catalog_documents(
        contract,
        plan,
        documents,
        requested_subjects=["衡水市", "保定市", "邢台市"],
        subject_gaps=["邢台市"],
    )


def _runtime(s, task, contract, *, max_revisions):
    return ResearchRuntime(
        contract,
        ResearchPlanner.initial(contract),
        time.monotonic() + s.run_timeout,
        max_revisions=max_revisions,
    )


async def _run_case(env, monkeypatch, *, max_revisions):
    s, _ = env
    s = replace(s, llm_base_url="https://example.invalid/v1", llm_api_key="offline-key")
    task = _task()
    contract = TaskContract.from_task(task)
    research = _runtime(s, task, contract, max_revisions=max_revisions)
    seen = []

    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda _settings: object())
    monkeypatch.setattr("guance_agent.orchestrator.get_module", lambda _name: object())

    async def discover(_task, _contract, _plan, _deadline):
        return _discovery(task, contract)

    async def execute_business(planned_task, _settings, run_runtime, _ctx, _deadline, _decision, _module):
        seen.append(list(planned_task.targets or []))
        return RunResult(
            run_id=planned_task.run_id,
            session_id=planned_task.session_id,
            status="completed",
            evidence_status="none",
            answer="已按发现结果执行",
            claims=[],
            evidence_refs=[],
            dropped_chunk_ids=[],
            error=None,
            meta=dict(run_runtime.meta),
            task_type=planned_task.task_type,
            structured={},
        )

    monkeypatch.setattr("guance_agent.orchestrator._run_business", execute_business)
    result = await run_task(task, s, Runtime(s, task),
                            research_discoverer=discover, research_runtime=research)
    return result, research, seen


async def test_one_bounded_revision_dispatches_only_revised_document_inputs(env, monkeypatch):
    result, research, seen = await _run_case(env, monkeypatch, max_revisions=1)

    execute_step = next(step for step in research.plan.steps if step.step_id == "execute")
    assert execute_step.input_refs == ["document:doc_hengshui", "document:doc_baoding"]
    assert seen == [["doc_hengshui", "doc_baoding"]]
    assert research.revision_count == 1
    assert result.meta["research"]["plan_revision"] == 1


async def test_zero_revision_budget_stops_before_business_dispatch(env, monkeypatch):
    result, research, seen = await _run_case(env, monkeypatch, max_revisions=0)

    assert seen == []
    assert research.revision_count == 0
    assert research.status == "insufficient"
    assert result.meta["research"]["status"] == "insufficient"
