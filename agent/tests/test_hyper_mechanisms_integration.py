"""Exercise the opt-in mechanisms inside the real SDK tool loop, with a fake model.

This checks plumbing and contracts, NOT factual quality on the campus Qwen.
"""
import json
import time
from dataclasses import replace

import pytest

from guance_agent.context import RunContext
from guance_agent.harness_inspired import HyperMechanisms
from guance_agent.kb import MockKB
from guance_agent.research_core import (
    ResearchState,
    build_research_core_agent,
    execute_research_core,
)
from guance_agent.routing import decide
from guance_agent.runtime import Runtime

from .fake_model import FakeModel, tool


@pytest.mark.asyncio
@pytest.mark.parametrize("exhaustive", [False, True])
async def test_hyper_experiment_tool_loop_and_review(env, monkeypatch, tmp_path, exhaustive):
    settings, task = env
    settings = replace(
        settings, qa_engine="hyper",
        llm_base_url="https://fake.invalid/v1",
        llm_api_key="offline-test-key",
    )
    question = (
        "列出所有包含农业灌溉设施的政策。"
        if exhaustive else "请查询planid TEST-001中的农业灌溉设施事实。"
    )
    task = task.model_copy(update={
        "question": question, "targets": ["TEST-001"],
    })
    kb = MockKB(settings.mock_kb_path)
    run_context = RunContext(
        settings, kb, task.run_id, task.session_id,
        question=question, route=decide(question), scope=("TEST-001",),
    )
    runtime = Runtime(settings, task)
    state = ResearchState.create(
        run_context, question, time.monotonic() + 60,
        explicit_targets=("TEST-001",), allowed_planids=("TEST-001",),
    )
    state.experiment = HyperMechanisms(task.run_id, question, tmp_path)
    state.experiment.snapshot("initialized", state)
    proposals = ["农业灌溉设施的原文事实"]
    frozen = state.freeze_requirements(proposals)
    requirement_ids = [item["id"] for item in frozen["requirements"]]
    quote = "完善农业灌溉设施"
    monkeypatch.setattr(
        kb, "verify_quote",
        lambda _chunk_id, text, *_args, **_kwargs: text == quote, raising=False,
    )

    async def audit(current, _mapped):
        return json.dumps({
            "passed": True,
            "covered_requirement_ids": [item.id for item in current.requirements],
            "approved_block_ids": ["irrigation-fact"],
            "gaps": [], "unsupported_claims": [],
        })

    model = FakeModel([
        [tool("search", query="农业灌溉设施", top_k=8, requirements=proposals)],
        [tool("read_batch", items=[{
            "planid": "TEST-001", "chunk_id": "TEST-001__s01__p00", "offset": 0,
        }])],
        [tool("submit_draft", evidence_candidates=[{
            "candidate_id": "irrigation", "claim": quote, "quote": quote,
            "chunk_id": "TEST-001__s01__p00",
            "requirement_ids": requirement_ids,
        }], blocks=[{
            "id": "irrigation-fact", "title": "农业灌溉设施",
            "kind": "fact", "text": "",
            "requirement_ids": requirement_ids,
            "evidence_ids": ["irrigation"],
        }])],
    ])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime, audit=audit),
        state, runtime,
    )
    assert quote in answer
    assert state.experiment.preflight_count == 1
    assert model.calls == 3
    assert state.check_result["passed"] is not exhaustive
    assert state.experiment.summary(state)["requirement_count"] >= 1
    assert (tmp_path / (state.experiment.run_id + ".json")).exists()
    if exhaustive:
        assert state.partial_finalized
        assert "exhaustive_catalog_coverage_unverified" in state.check_result["gaps"]
    runtime.close()
