import json
import time
from types import SimpleNamespace

import pytest

from guance_agent.orchestrator import _freeze_research_requirements


@pytest.mark.asyncio
async def test_requirement_planner_freezes_union_before_answer_and_records_purpose():
    calls = []

    async def complete_text(messages, **kwargs):
        calls.append((messages, kwargs))
        return json.dumps({"requirements": [
            {"kind": "subject_fact", "label": "服务业重点领域", "subject": "服务业"},
            {"kind": "analysis", "label": "重点部署的整体关系", "phase": "synthesis",
             "actionable": False},
        ]}, ensure_ascii=False)

    rt = SimpleNamespace(
        complete_text=complete_text,
        hooks=SimpleNamespace(turns=1),
        settings=SimpleNamespace(max_turns=20, llm_request_timeout=20),
        meta={},
    )
    task = SimpleNamespace(question="产业发展的年销售收入目标和重点部署是什么？")
    prior = {"question": "上一轮研究部署", "documents": [
        {"planid": "620", "title": "某地规划", "area": "某市", "fyp": "十一五"}
    ]}

    frozen = await _freeze_research_requirements(task, SimpleNamespace(), rt,
                                                 time.monotonic() + 120, prior)

    assert calls[0][1]["purpose"] == "requirement_planner"
    user_payload = json.loads(calls[0][0][1]["content"])
    assert user_payload["question"] == task.question
    assert user_payload["trusted_source_identities"][0]["planid"] == "620"
    assert "answer" not in user_payload and "gold" not in user_payload
    assert rt.meta["research_requirements"] == frozen
    assert any(unit["kind"] == "metric" for unit in frozen)
    assert any(unit["label"] == "服务业重点领域" for unit in frozen)
    assert rt.meta["research_requirement_planner"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_requirement_planner_budget_skip_retains_unresolved_requirement():
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("planner should respect the finalization reserve")

    rt = SimpleNamespace(
        complete_text=forbidden,
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=20, llm_request_timeout=20),
        meta={},
    )
    task = SimpleNamespace(question="梳理具有综合意义的政策部署")
    frozen = await _freeze_research_requirements(
        task, SimpleNamespace(), rt, time.monotonic() + 20, None)

    assert rt.meta["research_requirement_planner"]["status"] == "skipped_budget"
    assert any(unit["kind"] == "unresolved_requirement" for unit in frozen)
    assert not any(unit.get("actionable") for unit in frozen
                   if unit["kind"] == "unresolved_requirement")


@pytest.mark.asyncio
async def test_freeze_uses_current_request_and_produces_stable_review_ids():
    from guance_agent.research_quality import requirements

    received = []

    async def complete_text(messages, **_kwargs):
        received.append(json.loads(messages[-1]["content"]))
        return json.dumps({"requirements": [
            {"kind": "output_section", "label": "制造业"},
            {"kind": "output_section", "label": "农业"},
        ]}, ensure_ascii=False)

    rt = SimpleNamespace(complete_text=complete_text, hooks=SimpleNamespace(turns=0),
                         settings=SimpleNamespace(max_turns=20, llm_request_timeout=20), meta={})
    task = SimpleNamespace(question="请将前两轮整理成摘要，分‘制造业’、‘农业’两部分。")
    prior = {"research_query": "某市‘十一五’工业安排，按‘旧栏目’说明。",
             "question": "之前研究产业安排", "requirements": [], "documents": []}
    frozen = await _freeze_research_requirements(
        task, SimpleNamespace(), rt, time.monotonic() + 120, prior)
    assert received[0]["question"] == task.question
    assert not any(unit.get("kind") == "output_section" and unit["label"] in {
        "旧栏目", "十一五"} for unit in frozen)
    assert frozen == requirements(task.question, frozen)["units"]
    assert len({unit["id"] for unit in frozen}) == len(frozen)
