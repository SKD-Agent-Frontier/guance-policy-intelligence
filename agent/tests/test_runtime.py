import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from guance_agent.runtime import Runtime, ToolBudgetExceeded, open_session

from .fake_model import FakeModel, answer, tool
from .test_runner import run


async def test_session_history_and_evidence_isolation(env):
    s, task = env
    await run(env, FakeModel([[tool("kb_read", planid="TEST-001", section_idx=0)], answer("历史标记")]))
    same = FakeModel([answer()])
    _, ctx, _ = await run(env, same)
    assert "历史标记" in json.dumps(same.inputs, ensure_ascii=False)
    assert not ctx.evidence.ids()
    other = FakeModel([answer()])
    _, ctx, _ = await run((s, task.model_copy(update={"session_id": "s_other"})), other)
    assert "历史标记" not in json.dumps(other.inputs, ensure_ascii=False)
    assert not ctx.evidence.ids()


async def test_anonymous_callers_sharing_session_id_can_read_history(env):
    s, task = env
    await run(env, FakeModel([[tool("kb_read", planid="TEST-001", section_idx=0)],
                             answer("匿名共享历史")]))
    next_caller = FakeModel([answer()])
    await run((s, task.model_copy(update={"user_id": None})), next_caller)
    assert "匿名共享历史" in json.dumps(next_caller.inputs, ensure_ascii=False)


def test_session_ownership(env):
    s, _ = env
    session, _ = open_session(s, "s_owned", "alice")
    session.close()
    session, _ = open_session(s, "s_owned", "alice")
    session.close()
    for user in ("bob", None):
        with pytest.raises(PermissionError):
            open_session(s, "s_owned", user)


def test_anonymous_first_session_is_registered_as_null(env):
    s, _ = env
    session, created = open_session(s, "s_anonymous", None)
    session.close()
    with sqlite3.connect(s.session_path) as conn:
        assert conn.execute("SELECT user_id FROM guance_session_owner WHERE session_id=?",
                            ("s_anonymous",)).fetchone() == (None,)
    assert created is True


def test_anonymous_session_can_be_reopened(env):
    s, _ = env
    first, created_first = open_session(s, "s_anonymous", None)
    second, created_second = open_session(s, "s_anonymous", None)
    first.close()
    second.close()
    assert created_first is True and created_second is False


@pytest.mark.parametrize("initial,requested,allowed", [
    (None, "alice", False), ("alice", None, False), ("alice", "bob", False),
    ("alice", "alice", True),
])
def test_session_owner_transition_matrix(env, initial, requested, allowed):
    s, _ = env
    created, was_created = open_session(s, "s_owner_matrix", initial)
    created.close()
    assert was_created is True
    if allowed:
        session, was_created = open_session(s, "s_owner_matrix", requested)
        session.close()
        assert was_created is False
    else:
        with pytest.raises(PermissionError):
            open_session(s, "s_owner_matrix", requested)


def test_two_concurrent_owners_cannot_claim_new_session(env):
    s, _ = env
    def claim(user_id):
        try:
            session, created = open_session(s, "s_race", user_id)
            session.close()
            return ("ok", created)
        except PermissionError:
            return ("forbidden", False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ("alice", "bob")))
    assert sorted(status for status, _ in outcomes) == ["forbidden", "ok"]
    assert sum(created for _, created in outcomes) == 1


def test_tool_exception_detail_contains_type_and_message(env):
    from guance_agent.context import RunContext
    from guance_agent.kb import get_kb
    from guance_agent.tools import _call
    s, task = env
    ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
    def fail(*args):
        raise RuntimeError("backend detail")
    import asyncio
    asyncio.run(_call(ctx, "kb_search", fail, "农业", None, None, 8))
    assert ctx.tool_calls[-1]["error_detail"] == "RuntimeError: backend detail"


def test_runtime_log_redacts_then_limits_error_detail(env):
    s, task = env
    s = replace(s, llm_api_key="private-key", llm_base_url="https://private.invalid/v1")
    Runtime(s, task).log("debug", error_detail=(
        "RuntimeError: private-key https://private.invalid/v1 " + "x" * 300))
    event = json.loads(s.run_log.read_text(encoding="utf-8").splitlines()[-1])
    assert "private-key" not in event["error_detail"]
    assert "https://private.invalid/v1" not in event["error_detail"]
    assert "[REDACTED]" in event["error_detail"]
    assert len(event["error_detail"]) <= 200


def test_backend_error_detail_is_redacted_before_limit_in_logs_and_result_meta(env):
    import asyncio

    from guance_agent.context import RunContext
    from guance_agent.kb import get_kb
    from guance_agent.output import build_result
    from guance_agent.runner import ExecOutcome
    from guance_agent.tools import _call

    s, task = env
    api_key, base_url = "private-api-key-0123456789", "https://private.invalid/v1"
    s = replace(s, llm_api_key=api_key, llm_base_url=base_url)
    rt = Runtime(s, task)
    ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
    raw = "x" * 170 + api_key + " " + base_url + " " + "long text " * 40

    def fail(*args):
        raise RuntimeError(raw)

    asyncio.run(_call(ctx, "kb_search", fail, "农业", None, None, 8))
    rt.log("tool_end", error_detail=raw)
    rt.log("run_end", error_detail=raw)
    result = build_result(task, ExecOutcome("{}", None), ctx, {})
    rt.finish(result, ctx)

    details = [ctx.tool_calls[-1]["error_detail"], result.meta["tool_calls"][-1]["error_detail"]]
    events = [json.loads(line) for line in s.run_log.read_text(encoding="utf-8").splitlines()]
    details.extend(e["error_detail"] for e in events if "error_detail" in e)
    assert len(details) >= 4
    for detail in details:
        assert api_key not in detail and base_url not in detail
        assert "[REDACTED]" in detail
        assert len(detail) <= 200


async def test_log_privacy_and_linkage(env):
    s, task = env
    s = replace(s, llm_api_key="super-private-key")
    await run((s, task), FakeModel([[tool("kb_search", query="农业", area=None, fyp=None, top_k=8)], answer()]))
    log = s.run_log.read_text(encoding="utf-8")
    assert task.question not in log and s.llm_api_key not in log and "发展农业农村" not in log
    events = [json.loads(line) for line in log.splitlines()]
    assert all(e["run_id"] == task.run_id for e in events)
    assert {"received", "run_start", "tool_end", "llm_end"} <= {e["stage"] for e in events}
    assert any(e["tool"] == "kb_search" and e["status"] == "ok" for e in events)
    rt = Runtime(replace(s, log_content=True), task)
    rt.log("debug", answer="super-private-key")
    assert "super-private-key" not in s.run_log.read_text(encoding="utf-8")


def test_client_reused_without_unconfigured_parameters(env, monkeypatch):
    """缓存只按连接四项做键：其它配置变化不该重建客户端，也不该丢连接。"""
    from guance_agent import client as client_mod
    calls = []
    def constructor(**kwargs):
        calls.append(kwargs)
        return object()
    monkeypatch.setattr(client_mod, "AsyncOpenAI", constructor)
    client_mod.reset_for_tests()
    s = replace(env[0], llm_base_url="https://example.invalid/v1", llm_api_key="offline-key")
    assert client_mod.get_client(s) is client_mod.get_client(s)
    assert client_mod.get_client(replace(s, max_turns=3)) is client_mod.get_client(s)
    assert len(calls) == 1 and calls[0]["max_retries"] == 2 and calls[0]["timeout"] == 60
    client_mod.reset_for_tests()


def test_tracing_disabled():
    from agents.tracing import get_trace_provider
    assert get_trace_provider()._disabled


async def test_tool_budget_hook_counts_and_raises(env):
    s, task = env
    rt = Runtime(replace(s, max_tool_calls=1), task)
    from types import SimpleNamespace
    tool = SimpleNamespace(name="kb_search")
    await rt.hooks.on_tool_start(None, None, tool)
    with pytest.raises(ToolBudgetExceeded, match="已调用 1 次 / 上限 1"):
        await rt.hooks.on_tool_start(None, None, tool)

def test_error_detail_redacts_user_question_when_log_content_off(env):
    from guance_agent.context import sanitize_error_detail
    s, task = env
    hidden = sanitize_error_detail(f"检索失败：{task.question}", replace(s, log_content=False), task.question)
    assert task.question not in hidden and "检索失败" in hidden
    shown = sanitize_error_detail(f"检索失败：{task.question}", replace(s, log_content=True), task.question)
    assert task.question in shown
    assert len(sanitize_error_detail("x" * 500, s)) == 200


def test_log_write_failure_is_reported_in_result_meta(env, tmp_path):
    from guance_agent.output import build_result
    from guance_agent.runner import ExecOutcome
    s, task = env
    rt = Runtime(s, task)
    rt.settings = replace(s, run_log=tmp_path)      # 目录，写入必然失败
    result = build_result(task, ExecOutcome(answer(), None), None, {})
    rt.finish(result, None)
    assert result.meta.get("log_write_failed"), "日志写失败应体现在 meta 中"


def test_result_meta_error_detail_hides_user_question(env):
    from guance_agent.context import RunContext
    from guance_agent.kb import get_kb
    from guance_agent.output import build_result
    from guance_agent.runner import ExecOutcome
    s, task = env
    ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
    ctx.tool_calls.append(dict(name="kb_search", status="backend_error", message="x", elapsed_ms=1,
                               error_detail=f"RuntimeError: 查询失败 {task.question}"))
    rt = Runtime(s, task)
    rt.received()
    rt.prepare()
    result = build_result(task, ExecOutcome(None, "INTERNAL"), ctx, rt.meta)
    rt.finish(result, ctx)
    rt.close()
    detail = result.meta["tool_calls"][0]["error_detail"]
    assert task.question not in detail and "[REDACTED]" in detail
