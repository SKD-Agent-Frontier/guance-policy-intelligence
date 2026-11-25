import time
from dataclasses import replace

from guance_agent.orchestrator import run_task
from guance_agent.runtime import Runtime

from .sdk_fact_fakes import FACT, install_fact_model


def _configured(env):
    return replace(env[0], llm_base_url="https://example.invalid/v1", llm_api_key="offline-key")


async def test_execute_receives_absolute_deadline(env, monkeypatch):
    s, task = _configured(env), env[1]
    install_fact_model(monkeypatch, s)
    seen = {}

    from guance_agent.research_core import execute_research_core

    async def observe_execute(agent, state, runtime):
        seen["deadline"] = state.deadline
        return await execute_research_core(agent, state, runtime)

    monkeypatch.setattr("guance_agent.orchestrator.execute_research_core", observe_execute)
    before = time.monotonic()
    result = await run_task(task, s, Runtime(s, task))
    after = time.monotonic()
    assert result.status == "completed"
    assert before + s.run_timeout <= seen["deadline"] <= after + s.run_timeout


async def test_missing_config_does_not_touch_kb(env, tmp_path):
    s = replace(env[0], mock_kb_path=tmp_path / "missing-kb.json")
    result = await run_task(env[1], s, Runtime(s, env[1]))
    assert result.status == "failed"
    assert result.error.code == "MODEL_UNAVAILABLE"


async def test_finish_oserror_keeps_answer(env, monkeypatch):
    s, task = _configured(env), env[1]
    install_fact_model(monkeypatch, s)

    rt = Runtime(s, task)

    def fail_finish(result, ctx):
        raise PermissionError("log is read-only")

    monkeypatch.setattr(rt, "finish", fail_finish)
    result = await run_task(task, s, rt)
    assert result.status == "completed"
    assert FACT in result.answer
    assert result.claims[0].text == FACT
    assert result.meta["finish_failed"] == "PermissionError"
