import asyncio
import json
import time
from dataclasses import replace

import pytest
from openai import APIConnectionError

from guance_agent.agent import build_agent
from guance_agent.context import RunContext
from guance_agent.kb import MockKB, get_kb
from guance_agent.runner import execute
from guance_agent.runtime import Runtime

from .fake_model import FakeModel, answer, tool


async def run(env, model, kb=None, deadline=None):
    s, task = env
    ctx = RunContext(s, kb or get_kb(s), task.run_id, task.session_id)
    rt = Runtime(s, task)
    rt.received()
    rt.prepare()
    try:
        return await execute(build_agent(s, model), task, ctx, rt, deadline=deadline), ctx, rt
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


@pytest.mark.parametrize("status,code", [(400, "MODEL_BAD_REQUEST"), (429, "MODEL_UNAVAILABLE"), (503, "MODEL_UNAVAILABLE")])
async def test_http_status_classification(env, status, code):
    from openai import APIStatusError
    from openai._exceptions import httpx2
    error = APIStatusError("must never be logged", response=httpx2.Response(
        status, request=httpx2.Request("POST", "https://example.invalid")), body=None)
    outcome, _, _ = await run(env, FakeModel([error]))
    assert outcome.error_code == code
    assert "must never be logged" not in env[0].run_log.read_text(encoding="utf-8")


async def test_structured_mode_and_malformed_call(env):
    from guance_agent.schemas import AgentOutput
    s, task = env
    outcome, _, _ = await run((replace(s, structured_output=True), task), FakeModel([answer()]))
    assert isinstance(outcome.final_output, AgentOutput)
    malformed = tool("kb_search", query="农业").model_copy(update={"arguments": "broken json"})
    outcome, _, _ = await run(env, FakeModel([[malformed]]))
    assert outcome.error_code == "MODEL_BEHAVIOR"


async def test_error_detail_keeps_exception_message(env):
    outcome, _, _ = await run(env, FakeModel([RuntimeError("检索后端未配置 kb_backend=xxx")]))
    assert outcome.error_code == "INTERNAL"
    assert "kb_backend=xxx" in outcome.error_detail
    assert "kb_backend=xxx" in env[0].run_log.read_text(encoding="utf-8")


async def test_library_timeout_is_not_run_timeout(env):
    outcome, _, _ = await run(env, FakeModel([TimeoutError("library timed out")]))
    assert outcome.error_code == "INTERNAL"
    assert "library timed out" in outcome.error_detail


async def test_tool_budget_stops_before_extra_call(env):
    s, task = env
    model = FakeModel([[tool("kb_search", query="农业", area=None, fyp=None, top_k=8),
                        tool("kb_read", planid="TEST-001", section_idx=0)]])
    outcome, ctx, rt = await run((replace(s, max_tool_calls=3), task), model)
    assert outcome.error_code == "TOOL_BUDGET"
    assert len(ctx.tool_calls) == 3 and rt.hooks.calls == 4
    assert "已调用 3 次 / 上限 3" in outcome.error_detail


async def test_deadline_contract(env):
    s, task = env
    deadline = time.monotonic() + .03
    outcome, _, _ = await run((replace(s, run_timeout=10), task), FakeModel([answer()], delay=.2),
                              deadline=deadline)
    assert outcome.error_code == "RUN_TIMEOUT"


async def test_timeout_overrun_is_logged(env):
    class BlockingModel(FakeModel):
        async def get_response(self, *args, **kwargs):
            time.sleep(.5)
            return await super().get_response(*args, **kwargs)

    s, task = env
    outcome, _, _ = await run((replace(s, run_timeout=.3), task), BlockingModel([answer()]))
    assert outcome.error_code == "RUN_TIMEOUT"
    events = [json.loads(line) for line in s.run_log.read_text(encoding="utf-8").splitlines()]
    overruns = [e for e in events if e["stage"] == "timeout_overrun"]
    assert overruns and overruns[0]["seconds"] > .05


async def test_max_turns_detail_has_counts(env):
    s, task = env
    model = FakeModel([[tool("kb_read", planid="TEST-001", section_idx=0)]])
    outcome, _, rt = await run((replace(s, max_turns=1), task), model)
    assert outcome.error_code == "MAX_TURNS"
    assert f"已执行 {rt.hooks.turns} 轮 / 上限 1" in outcome.error_detail


async def test_http_auth_and_not_found(env):
    from openai import APIStatusError
    from openai._exceptions import httpx2
    for status, code in ((401, "MODEL_UNAUTHORIZED"), (403, "MODEL_UNAUTHORIZED"),
                         (404, "MODEL_NOT_FOUND")):
        error = APIStatusError("must never be logged", response=httpx2.Response(
            status, request=httpx2.Request("POST", "https://example.invalid")), body=None)
        outcome, _, _ = await run(env, FakeModel([error]))
        assert outcome.error_code == code
        assert "must never be logged" not in env[0].run_log.read_text(encoding="utf-8")


async def test_clean_timeout_does_not_log_overrun(env):
    s, task = env
    outcome, _, _ = await run((replace(s, run_timeout=.3), task), FakeModel([answer()], delay=10))
    assert outcome.error_code == "RUN_TIMEOUT"
    events = [json.loads(line) for line in s.run_log.read_text(encoding="utf-8").splitlines()]
    assert not any(e["stage"] == "timeout_overrun" for e in events)
