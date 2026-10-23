import json
from dataclasses import replace

import pytest

from guance_agent.runtime import Runtime, open_session

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


def test_session_ownership(env):
    s, _ = env
    open_session(s, "s_owned", "alice").close()
    open_session(s, "s_owned", "alice").close()
    for user in ("bob", None):
        with pytest.raises(PermissionError):
            open_session(s, "s_owned", user)


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
