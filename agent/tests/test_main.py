import asyncio
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
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda s, c: FakeModel([answer()]))
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


async def test_session_created_metadata_for_omitted_and_explicit_ids(configured):
    generated = await submit("你好", settings=configured)
    explicit = await submit("你好", session_id="s_explicit_new", settings=configured)
    assert generated.meta["session_created"] is True
    assert explicit.meta["session_created"] is True


async def test_session_created_metadata_for_existing_id(configured):
    first = await submit("你好", session_id="s_existing", settings=configured)
    second = await submit("继续", session_id="s_existing", settings=configured)
    assert first.meta["session_created"] is True
    assert second.meta["session_created"] is False


def test_task_request_does_not_store_session_creation_intent():
    from guance_agent.schemas import TaskRequest
    assert "is_new_session" not in TaskRequest.model_fields


async def test_forbidden_and_missing_config(configured, env):
    first = await submit("你好", user_id="alice", settings=configured)
    denied = await submit("你好", session_id=first.session_id, user_id="bob", settings=configured)
    assert denied.error.code == "SESSION_FORBIDDEN"
    missing = await submit("你好", settings=env[0])
    assert missing.error.code == "MODEL_UNAVAILABLE"


async def test_session_forbidden_is_mapped_by_type(configured, monkeypatch):
    from guance_agent.runtime import SessionForbidden
    def deny(*args):
        raise SessionForbidden("arbitrary detail")
    monkeypatch.setattr("guance_agent.runtime.open_session", deny)
    result = await submit("你好", session_id="s_denied", settings=configured)
    assert result.error.code == "SESSION_FORBIDDEN"


async def test_unrelated_permission_error_remains_internal(configured, monkeypatch):
    def fail(*args):
        raise PermissionError("SESSION_FORBIDDEN")
    monkeypatch.setattr("guance_agent.runtime.open_session", fail)
    result = await submit("你好", session_id="s_permission", settings=configured)
    assert result.error.code == "INTERNAL"


@pytest.mark.parametrize("exception", [ValueError("bad config"), KeyError("missing config")])
async def test_settings_stage_errors_map_to_config_error(env, monkeypatch, exception):
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr("guance_agent.main.load_settings", fail)
    result = await submit("你好")
    assert result.error.code == "CONFIG_ERROR"
    assert result.error.message == "服务配置有误，请联系管理员"


@pytest.mark.parametrize("exception,code", [
    (ValueError("bad kb config"), "CONFIG_ERROR"),
    (KeyError("missing kb config"), "CONFIG_ERROR"),
    (RuntimeError("kb runtime fault"), "INTERNAL"),
])
async def test_kb_stage_classifies_only_value_and_key_errors(configured, monkeypatch,
                                                              exception, code):
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", fail)
    result = await submit("你好", settings=configured)
    assert result.error.code == code
    if code == "CONFIG_ERROR":
        assert result.error.message == "服务配置有误，请联系管理员"


async def test_execution_value_error_is_internal(configured, monkeypatch):
    async def fail(*args, **kwargs):
        raise ValueError("execution failure")

    monkeypatch.setattr("guance_agent.runner.Runner.run", fail)
    result = await submit("你好", settings=configured)
    assert result.error.code == "INTERNAL"


async def test_submit_tools_and_verified_output(configured, monkeypatch):
    model = FakeModel([[tool("kb_search", query="农业", area="甲", fyp="十四五", top_k=1)],
                       [tool("kb_read", planid="TEST-001", section_idx=0)],
                       answer("农业重点", [{"text":"农业农村发展", "chunk_ids":["TEST-001__s00__p00"]}])])
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda s, c: model)
    result = await submit("农业重点", settings=configured)
    assert result.evidence_status == "verified" and len(result.meta["tool_calls"]) == 2


async def test_trimmed_question_limit_accepts_surrounding_whitespace(configured):
    result = await submit(" " * 100 + "字" * 4000 + " " * 100, settings=configured)
    assert result.status == "completed"


async def test_user_id_limit_accepts_64_and_rejects_65(configured):
    accepted = await submit("你好", user_id="u" * 64, settings=configured)
    rejected = await submit("你好", user_id="u" * 65, settings=configured)
    assert accepted.status == "completed"
    assert rejected.error.code == "INVALID_INPUT"
    assert rejected.error.message == "用户 ID 格式不正确"


async def test_user_id_limit_uses_configured_value(configured):
    custom = replace(configured, max_user_id_chars=80)
    result = await submit("你好", user_id="u" * 65, settings=custom)
    assert result.status == "completed"


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


def test_cli_warms_real_rag_before_accepting_requests(env, monkeypatch):
    from guance_agent.output import build_result
    from guance_agent.runner import ExecOutcome

    events = []
    settings = replace(env[0], kb_backend="rag", rag_store_project="runtime-store")
    result = build_result(env[1], ExecOutcome(answer(), None), None, {})

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "warm_up", lambda received: events.append(("warm", received)))
    monkeypatch.setattr(cli, "close_kb", lambda: events.append(("close", None)))
    monkeypatch.setattr(cli, "aclose_client", lambda: asyncio.sleep(0))

    async def fake_submit(*args, **kwargs):
        events.append(("submit", None))
        return result

    monkeypatch.setattr(cli, "submit", fake_submit)
    assert cli.main(["ask", "问题"]) == 0
    assert [event[0] for event in events] == ["warm", "submit", "close"]
