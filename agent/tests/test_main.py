import json
from dataclasses import replace

import pytest

from guance_agent import cli
from guance_agent.main import submit

from .fake_model import FakeModel, answer, tool


@pytest.fixture
def configured(env, monkeypatch):
    s = replace(env[0], llm_base_url="https://example.invalid/v1", llm_api_key="offline-test-key")
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda s: object())
    monkeypatch.setattr("guance_agent.main.chat_model", lambda s, c: FakeModel([answer()]))
    return s


@pytest.mark.parametrize("question,session", [("", None), (" ", None), (None, None), (42, None),
                                              ("字"*4001, None), ("你好", "bad/id"), ("你好", 3)])
async def test_rejected_input(env, question, session):
    r = await submit(question, session_id=session, settings=env[0])
    assert r.status == "failed" and r.error.code == "INVALID_INPUT"
    assert r.run_id.startswith("run_")
    if isinstance(question, str) and len(question) > 4000:
        assert "4000" in r.answer


async def test_run_and_session_ids(configured):
    first = await submit("你好", settings=configured)
    second = await submit("继续", session_id=first.session_id, settings=configured)
    third = await submit("你好", settings=configured)
    assert first.status == second.status == third.status == "completed"
    assert len({first.run_id, second.run_id, third.run_id}) == 3
    assert first.session_id == second.session_id != third.session_id
    events = [json.loads(line) for line in configured.run_log.read_text(encoding="utf-8").splitlines()]
    ends = [e for e in events if e["stage"] == "run_end"]
    assert len(ends) == 3 and all(e["no_tool_answer"] for e in ends)
    assert all(e["tools"] == ["kb_search", "kb_read"] for e in ends)


async def test_forbidden_and_missing_config(configured, env):
    first = await submit("你好", user_id="alice", settings=configured)
    denied = await submit("你好", session_id=first.session_id, user_id="bob", settings=configured)
    assert denied.error.code == "SESSION_FORBIDDEN"
    missing = await submit("你好", settings=env[0])
    assert missing.error.code == "MODEL_UNAVAILABLE"


async def test_submit_tools_and_verified_output(configured, monkeypatch):
    model = FakeModel([[tool("kb_search", query="农业", area="甲", fyp="十四五", top_k=1)],
                       [tool("kb_read", planid="TEST-001", section_idx=0)],
                       answer("农业重点", [{"text":"农业农村发展", "chunk_ids":["TEST-001__s00__p00"]}])])
    monkeypatch.setattr("guance_agent.main.chat_model", lambda s, c: model)
    result = await submit("农业重点", settings=configured)
    assert result.evidence_status == "verified" and len(result.meta["tool_calls"]) == 2


@pytest.mark.parametrize("status,error,expected", [
    ("completed", None, 0), ("failed", "INTERNAL", 1),
    ("failed", "INVALID_INPUT", 2), ("limit_reached", "MAX_TURNS", 3),
])
def test_cli_exit_codes(env, monkeypatch, capsys, status, error, expected):
    from guance_agent.output import build_result
    from guance_agent.runner import ExecOutcome
    result = build_result(env[1], ExecOutcome(answer(), error), None, {})
    result.status = status
    async def fake(*args, **kwargs):
        return result
    monkeypatch.setattr(cli, "submit", fake)
    assert cli.main(["ask", "问题", "--json"]) == expected
    captured = capsys.readouterr()
    assert json.loads(captured.out)["run_id"] == result.run_id
    assert "session_id=" in captured.err


def test_chat_reuses_session(env, monkeypatch):
    from guance_agent.output import build_result
    from guance_agent.runner import ExecOutcome
    result = build_result(env[1], ExecOutcome(answer(), None), None, {})
    seen, inputs = [], iter(["你好", "再问", ""])
    async def fake(question, session_id=None):
        seen.append(session_id)
        return result
    monkeypatch.setattr(cli, "submit", fake)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert cli.main(["chat"]) == 0 and seen == [None, result.session_id]
