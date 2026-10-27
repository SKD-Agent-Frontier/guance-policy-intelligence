import asyncio
import time
from types import SimpleNamespace

import pytest
from openai import APITimeoutError

from guance_agent.business import (
    BusinessError,
    BusinessOutcome,
    BusinessServices,
    get_module,
    register,
)
from guance_agent.config import Settings
from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.runtime import Runtime


@pytest.fixture
def business_ctx(env, tmp_path):
    settings, task = env
    settings = Settings(session_path=tmp_path / "sessions.sqlite",
                        run_log=tmp_path / "runs.jsonl")
    ctx = RunContext(settings, get_kb(settings), task.run_id, task.session_id)
    return settings, task, ctx, Runtime(settings, task)


def test_outcome_defaults_and_error_codes():
    first, second = BusinessOutcome(answer="ok"), BusinessOutcome(answer="ok")
    first.structured["x"] = 1
    assert second.structured == {}
    assert BusinessError("RUN_TIMEOUT").code == "RUN_TIMEOUT"
    assert BusinessError("not-an-error").code == "INTERNAL"


def _module(name):
    class Module:
        async def __call__(self, task, ctx, services):
            return BusinessOutcome(answer="ok")
    Module.name = name
    return Module()


def test_registry_starts_empty(monkeypatch):
    monkeypatch.setattr("guance_agent.business._MODULES", {})
    assert get_module("compare") is None


def test_registry_registers_explicit_module_and_rejects_duplicate(monkeypatch):
    monkeypatch.setattr("guance_agent.business._MODULES", {})
    module = _module("compare")
    register(module)
    assert get_module("compare") is module
    with pytest.raises(ValueError):
        register(module)


@pytest.mark.parametrize("name", ["qa", "extract"])
def test_registry_rejects_reserved_names(monkeypatch, name):
    monkeypatch.setattr("guance_agent.business._MODULES", {})
    with pytest.raises(ValueError):
        register(_module(name))


@pytest.mark.parametrize("name", ["Bad Name", "UPPER", "", "x" * 65])
def test_registry_rejects_invalid_names(monkeypatch, name):
    monkeypatch.setattr("guance_agent.business._MODULES", {})
    with pytest.raises(ValueError):
        register(_module(name))


async def test_search_registers_evidence_and_respects_scope(business_ctx):
    _, _, ctx, rt = business_ctx
    ctx.scope = ("TEST-001",)
    services = BusinessServices(ctx, rt, time.monotonic() + 5)
    result = await services.search("农业", area="甲", fyp="十四五", top_k=2)
    assert result.status == "ok"
    assert result.data and all(hit.planid == "TEST-001" for hit in result.data)
    assert ctx.evidence.ids()
    assert ctx.tool_calls[-1]["name"] == "kb_search"
    assert rt.hooks.calls == 1


async def test_read_scope_rejected_and_search_validation_reused(business_ctx):
    _, _, ctx, rt = business_ctx
    ctx.scope = ("TEST-001",)
    services = BusinessServices(ctx, rt, time.monotonic() + 5)
    blocked = await services.read("TEST-002", section_idx=0)
    invalid = await services.search(" ")
    assert blocked.status == "invalid_argument"
    assert "不在本轮取证范围" in blocked.message
    assert invalid.status == "invalid_argument"
    assert not ctx.evidence.ids()
    assert [event["status"] for event in ctx.tool_calls] == ["invalid_argument", "invalid_argument"]


async def test_tool_budget_and_deadline(business_ctx):
    settings, _, ctx, rt = business_ctx
    ctx.settings = Settings(session_path=settings.session_path, run_log=settings.run_log,
                            max_tool_calls=1)
    rt.settings = ctx.settings
    services = BusinessServices(ctx, rt, time.monotonic() + 5)
    await services.search("农业", top_k=1)
    with pytest.raises(BusinessError) as budget:
        await services.search("农业", top_k=1)
    assert budget.value.code == "TOOL_BUDGET"
    expired = BusinessServices(ctx, rt, time.monotonic() - 1)
    with pytest.raises(BusinessError) as timeout:
        await expired.read("TEST-001")
    assert timeout.value.code == "RUN_TIMEOUT"


async def test_call_model_clamps_tokens_and_records_safe_telemetry(monkeypatch, business_ctx):
    settings, _, ctx, rt = business_ctx
    ctx.settings = Settings(session_path=settings.session_path, run_log=settings.run_log,
                            llm_max_output_tokens=64, max_turns=1,
                            llm_temperature=0.25)
    seen = {}
    async def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(Runtime, "client", property(lambda self: client))
    services = BusinessServices(ctx, rt, time.monotonic() + 5)
    assert await services.call_model([{"role": "user", "content": "secret prompt"}], 100) == "answer"
    assert seen["max_tokens"] == 64
    assert seen["temperature"] == 0.25
    assert seen["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert services.telemetry == {
        "model_calls": 1, "input_tokens": 7, "output_tokens": 3,
        "per_call": [{"input_tokens": 7, "output_tokens": 3, "finish_reason": "stop",
                      "status": "ok", "error_code": None, "elapsed_ms": services.telemetry["per_call"][0]["elapsed_ms"]}],
    }
    assert "secret prompt" not in str(services.telemetry)
    with pytest.raises(BusinessError) as budget:
        await services.call_model([])
    assert budget.value.code == "MAX_TURNS"


async def test_call_model_missing_content_and_timeout_are_mapped(monkeypatch, business_ctx):
    settings, _, ctx, rt = business_ctx
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: asyncio.sleep(0, result=SimpleNamespace(choices=[], usage=None)))))
    monkeypatch.setattr(Runtime, "client", property(lambda self: client))
    missing = BusinessServices(ctx, rt, time.monotonic() + 5)
    with pytest.raises(BusinessError) as parse:
        await missing.call_model([])
    assert parse.value.code == "OUTPUT_PARSE"
    deadline = time.monotonic() + .01
    async def slow(**kwargs):
        try:
            await asyncio.sleep(.1)
        except asyncio.CancelledError:
            await asyncio.sleep(max(0, deadline - time.monotonic()) + .005)
            raise
    client.chat.completions.create = slow
    timed = BusinessServices(ctx, rt, deadline)
    with pytest.raises(BusinessError) as timeout:
        await timed.call_model([])
    assert timeout.value.code == "RUN_TIMEOUT"
    assert timed.telemetry["model_calls"] == 1

    ctx.settings = Settings(session_path=settings.session_path, run_log=settings.run_log,
                            llm_request_timeout=0.01)
    request_limited = BusinessServices(ctx, rt, time.monotonic() + 5)
    with pytest.raises(BusinessError) as request_timeout:
        await request_limited.call_model([])
    assert request_timeout.value.code == "MODEL_UNAVAILABLE"
    assert request_limited.telemetry["per_call"][0]["error_code"] == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize("error_kind", ["request", "asyncio"])
async def test_early_model_timeout_is_not_global_timeout(monkeypatch, business_ctx, error_kind):
    settings, _, ctx, rt = business_ctx
    ctx.settings = Settings(session_path=settings.session_path, run_log=settings.run_log,
                            llm_request_timeout=10)
    deadline = time.monotonic() + 5
    async def fail_early(**kwargs):
        if error_kind == "request":
            raise APITimeoutError(request=None)
        raise TimeoutError("request timed out early")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail_early)))
    monkeypatch.setattr(Runtime, "client", property(lambda self: client))
    services = BusinessServices(ctx, rt, deadline)
    with pytest.raises(BusinessError) as error:
        await services.call_model([])
    assert time.monotonic() < deadline
    assert error.value.code == "MODEL_UNAVAILABLE"
    assert services.telemetry["per_call"][0]["error_code"] == "MODEL_UNAVAILABLE"


async def test_model_token_validation_and_concurrent_budget(monkeypatch, business_ctx):
    settings, _, ctx, rt = business_ctx
    ctx.settings = Settings(session_path=settings.session_path, run_log=settings.run_log,
                            max_turns=1)
    calls = 0
    async def create(**kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(.02)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="ok"), finish_reason="stop")], usage=None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(Runtime, "client", property(lambda self: client))
    services = BusinessServices(ctx, rt, time.monotonic() + 5)
    for invalid in (True, False, 0, -1, 1.5, "10"):
        with pytest.raises(BusinessError) as error:
            await services.call_model([], invalid)
        assert error.value.code == "INVALID_INPUT"
    result = await asyncio.gather(services.call_model([]), services.call_model([]),
                                  return_exceptions=True)
    assert result[0] == "ok"
    assert isinstance(result[1], BusinessError) and result[1].code == "MAX_TURNS"
    assert calls == services.telemetry["model_calls"] == 1


async def test_model_cancellation_is_recorded(monkeypatch, business_ctx):
    settings, _, ctx, rt = business_ctx
    async def slow(**kwargs):
        await asyncio.sleep(1)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=slow)))
    monkeypatch.setattr(Runtime, "client", property(lambda self: client))
    services = BusinessServices(ctx, rt, time.monotonic() + 5)
    task = asyncio.create_task(services.call_model([]))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert services.telemetry["per_call"][0]["error_code"] == "CANCELLED"
