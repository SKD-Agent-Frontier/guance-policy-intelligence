import asyncio
import json
from dataclasses import replace

import pytest
from openai import APIConnectionError

from guance_agent.agent import build_agent
from guance_agent.context import RunContext
from guance_agent.kb import MockKB, get_kb
from guance_agent.runner import execute
from guance_agent.runtime import Runtime

from .fake_model import FakeModel, answer, tool


async def run(env, model, kb=None):
    s, task = env
    ctx = RunContext(s, kb or get_kb(s), task.run_id, task.session_id)
    rt = Runtime(s, task)
    rt.received()
    rt.prepare()
    try:
        return await execute(build_agent(s, model), task, ctx, rt), ctx, rt
    finally:
        rt.close()


async def test_multistep(env):
    model = FakeModel([[tool("kb_search", query="农业", area="甲", fyp="十四五", top_k=8)],
                       [tool("kb_read", planid="TEST-001", section_idx=0)], answer("完成")])
    outcome, ctx, _ = await run(env, model)
    assert outcome.error_code is None and model.calls == 3
    assert [c["name"] for c in ctx.tool_calls] == ["kb_search", "kb_read"]
    assert ctx.evidence.ids()


async def test_turn_counting(env):
    s, task = env
    model = FakeModel([[tool("kb_search", query="农业", area=None, fyp=None, top_k=8),
                        tool("kb_read", planid="TEST-001", section_idx=0)], answer()])
    outcome, ctx, rt = await run((replace(s, max_turns=2), task), model)
    assert outcome.error_code is None and model.calls == rt.hooks.turns == 2
    assert len(ctx.tool_calls) == 2


async def test_no_tools_and_max_turns(env):
    outcome, ctx, _ = await run(env, FakeModel([answer()]))
    assert outcome.error_code is None and not ctx.evidence.ids()
    s, task = env
    model = FakeModel([[tool("kb_read", planid="TEST-001", section_idx=0)]])
    outcome, _, _ = await run((replace(s, max_turns=1), task), model)
    assert outcome.error_code == "MAX_TURNS" and model.calls == 1


async def test_unknown_tool_and_connection(env):
    outcome, _, _ = await run(env, FakeModel([[tool("shell", command="forbidden")]]))
    assert outcome.error_code == "MODEL_BEHAVIOR"
    from openai._exceptions import httpx2 as httpx
    error = APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    outcome, _, _ = await run(env, FakeModel([error]))
    assert outcome.error_code == "MODEL_UNAVAILABLE"


async def test_run_and_tool_timeouts(env):
    s, task = env
    outcome, _, _ = await run((replace(s, run_timeout=.03), task), FakeModel([answer()], delay=.15))
    assert outcome.error_code == "RUN_TIMEOUT"
    kb = MockKB(s.mock_kb_path, delay=.15)
    outcome, ctx, _ = await run((replace(s, tool_timeout=.03), task), FakeModel([
        [tool("kb_read", planid="TEST-001", section_idx=0)], answer(insufficient=True)]), kb)
    assert outcome.error_code is None and ctx.tool_calls[0]["status"] == "timeout"


async def test_cancellation_logged_and_propagated(env):
    pending = asyncio.create_task(run(env, FakeModel([answer()], delay=10)))
    await asyncio.sleep(.1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    events = [json.loads(line) for line in env[0].run_log.read_text(encoding="utf-8").splitlines()]
    assert any(e["error_code"] == "CANCELLED" for e in events)
