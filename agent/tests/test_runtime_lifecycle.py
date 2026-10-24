"""模块⑥ 生命周期与并发边界：会话串行化、资源关闭、历史上限、钩子健壮性。"""
import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

import guance_agent.orchestrator as orchestrator
from guance_agent import client as client_mod
from guance_agent.config import Settings
from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.runtime import Runtime, open_session
from guance_agent.schemas import TaskRequest
from tests.fake_model import FakeModel
from tests.fake_model import answer as reply


def make_settings(tmp_path, **kw):
    base = dict(session_path=tmp_path / "s.sqlite", run_log=tmp_path / "r.jsonl",
                llm_base_url="https://example.invalid/v1", llm_api_key="sk-test-0123456789abcdef")
    return Settings(**{**base, **kw})


def make_task(**kw):
    base = dict(run_id="r", session_id="s", question="q", created_at="x")
    return TaskRequest(**{**base, **kw})


async def test_same_session_runs_are_serialized(tmp_path, monkeypatch):
    """同一 session 的两个回合必须串行：第二个要看到第一个的答案。"""
    s = make_settings(tmp_path)
    slow = FakeModel([reply("答案A")], delay=0.3)
    fast = FakeModel([reply("答案B")], delay=0.05)
    active = {"m": slow}
    monkeypatch.setattr(orchestrator, "chat_model", lambda _s, _c: active["m"])

    async def run(tag):
        task = make_task(run_id=f"run_{tag}", session_id="SAME", question=f"问题{tag}")
        rt = Runtime(s, task)
        rt.prepare()
        try:
            return await orchestrator.run_task(task, s, rt)
        finally:
            rt.close()

    first = asyncio.create_task(run("A"))
    await asyncio.sleep(0.05)
    active["m"] = fast
    second = asyncio.create_task(run("B"))
    await asyncio.gather(first, second)

    seen = json.dumps(fast.inputs[0], ensure_ascii=False, default=str)
    assert "答案A" in seen, "同会话并发未串行化：第二个回合没看到上一个回合的回答"


async def test_different_sessions_are_not_serialized(tmp_path, monkeypatch):
    """不同 session 不受同一把锁影响。"""
    s = make_settings(tmp_path)
    model = FakeModel([reply("ok")], delay=0.2)
    monkeypatch.setattr(orchestrator, "chat_model", lambda _s, _c: model)

    async def run(sid):
        task = make_task(run_id=f"run_{sid}", session_id=sid)
        rt = Runtime(s, task)
        rt.prepare()
        try:
            return await orchestrator.run_task(task, s, rt)
        finally:
            rt.close()

    started = time.perf_counter()
    await asyncio.gather(run("s1"), run("s2"))
    assert time.perf_counter() - started < 0.38


async def test_close_failure_does_not_escape(tmp_path):
    """资源清理失败不能覆盖已经生成的结果。"""
    s = make_settings(tmp_path)
    rt = Runtime(s, make_task())
    rt.prepare()

    def boom():
        raise OSError("关闭失败")

    rt.session.close = boom
    rt.close()  # 不抛


async def test_history_limit_applies(tmp_path):
    s = make_settings(tmp_path, max_history_items=3)
    session, created = open_session(s, "hist", "u")
    assert created
    for i in range(8):
        await session.add_items([{"role": "user", "content": f"m{i}"}])
    items = await session.get_items()
    session.close()
    assert len(items) == 3


async def test_open_existing_session_skips_write_transaction(tmp_path):
    """已存在的会话只走读路径，不再抢写锁。"""
    s = make_settings(tmp_path)
    open_session(s, "fast", "u")[0].close()
    started = time.perf_counter()
    for _ in range(5):
        open_session(s, "fast", "u")[0].close()
    assert (time.perf_counter() - started) / 5 < 0.025


def test_client_cache_ignores_unrelated_settings(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    made = []
    monkeypatch.setattr(client_mod, "AsyncOpenAI", lambda **kw: made.append(kw) or object())
    client_mod.reset_for_tests()
    assert client_mod.get_client(s) is client_mod.get_client(s)
    assert client_mod.get_client(replace(s, max_turns=3)) is client_mod.get_client(s)
    assert len(made) == 1
    client_mod.reset_for_tests()


async def test_aclose_client_releases_cached_clients(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    closed = []

    class FakeClient:
        async def close(self):
            closed.append(True)

    monkeypatch.setattr(client_mod, "AsyncOpenAI", lambda **kw: FakeClient())
    client_mod.reset_for_tests()
    client_mod.get_client(s)
    await client_mod.aclose_client()
    assert closed and client_mod._build.cache_info().currsize == 0


async def test_tool_end_tolerates_non_json_result(tmp_path):
    s = make_settings(tmp_path)
    task = make_task()
    rt = Runtime(s, task)
    rt.prepare()
    ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
    ctx.tool_calls.append(dict(name="kb_search", status="ok", message="", elapsed_ms=1))
    hook_ctx, tool = SimpleNamespace(context=ctx, tool_call_id="c1"), SimpleNamespace(name="kb_search")
    await rt.hooks.on_tool_start(hook_ctx, None, tool)
    await rt.hooks.on_tool_end(hook_ctx, None, tool, "不是 JSON")
    rt.close()
    events = [json.loads(line) for line in s.run_log.read_text(encoding="utf-8").splitlines()]
    assert [e for e in events if e["stage"] == "tool_end"][-1]["status"] == "unparsed"


async def test_rejected_tool_call_is_logged(tmp_path):
    """超出预算的那次调用也要留下事件，并写明工具名。"""
    s = make_settings(tmp_path, max_tool_calls=1)
    task = make_task()
    rt = Runtime(s, task)
    rt.prepare()
    ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
    tool = SimpleNamespace(name="kb_search")
    await rt.hooks.on_tool_start(SimpleNamespace(context=ctx, tool_call_id="c1"), None, tool)
    with pytest.raises(Exception):
        await rt.hooks.on_tool_start(SimpleNamespace(context=ctx, tool_call_id="c2"), None, tool)
    rt.close()
    events = [json.loads(line) for line in s.run_log.read_text(encoding="utf-8").splitlines()]
    rejected = [e for e in events if e["stage"] == "tool_rejected"]
    assert rejected and rejected[-1]["tool"] == "kb_search"
