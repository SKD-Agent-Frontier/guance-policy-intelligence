import json
import time
from dataclasses import replace

from guance_agent.orchestrator import run_task
from guance_agent.runner import ExecOutcome
from guance_agent.runtime import Runtime


def _configured(env):
    return replace(env[0], llm_base_url="https://example.invalid/v1", llm_api_key="offline-key")


def _stub_model(monkeypatch):
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda s: object())
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda s, c: object())
    monkeypatch.setattr("guance_agent.orchestrator.build_agent", lambda s, m: object())


def _answer():
    return json.dumps({"answer": "ok", "claims": [], "insufficient": False})


async def test_execute_receives_absolute_deadline(env, monkeypatch):
    s, task = _configured(env), env[1]
    _stub_model(monkeypatch)
    seen = {}

    async def fake_execute(agent, task, ctx, rt, deadline=None):
        seen["deadline"] = deadline
        return ExecOutcome(_answer(), None)

    monkeypatch.setattr("guance_agent.orchestrator.execute", fake_execute)
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
    _stub_model(monkeypatch)

    async def fake_execute(agent, task, ctx, rt, deadline=None):
        return ExecOutcome(_answer(), None)

    monkeypatch.setattr("guance_agent.orchestrator.execute", fake_execute)
    rt = Runtime(s, task)

    def fail_finish(result, ctx):
        raise PermissionError("log is read-only")

    monkeypatch.setattr(rt, "finish", fail_finish)
    result = await run_task(task, s, rt)
    assert result.status == "completed"
    assert result.answer == "ok"
    assert result.meta["finish_failed"] == "PermissionError"
