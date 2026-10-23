from dataclasses import FrozenInstanceError

import pytest

from guance_agent.config import ROOT, Settings, load_settings
from guance_agent.schemas import AgentOutput, RunResult, ToolResult


def test_env_precedence_paths_and_secret(tmp_path, monkeypatch):
    env = tmp_path / "settings.env"
    env.write_text("MAX_TURNS=3\nLLM_API_KEY=private-test-key\nSESSION_PATH=data/test.sqlite\n"
                   "LLM_TEMPERATURE=\nSTRUCTURED_OUTPUT=off\n", encoding="utf-8")
    monkeypatch.setenv("MAX_TURNS", "5")
    settings = load_settings(env)
    assert settings.max_turns == 5
    assert settings.session_path == ROOT / "data/test.sqlite"
    assert settings.llm_temperature is None and not settings.structured_output
    assert "private-test-key" not in repr(settings)
    with pytest.raises(FrozenInstanceError):
        settings.max_turns = 6


@pytest.mark.parametrize("kwargs", [
    {"max_turns": 0}, {"tool_timeout": 0}, {"run_timeout": -1},
    {"llm_request_timeout": float("nan")}, {"top_k_default": 21},
    {"kb_backend": "real"}, {"llm_max_retries": -1}, {"llm_temperature": 3},
    {"max_turns": 1.5}, {"llm_max_retries": True},
])
def test_invalid_settings(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_invalid_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("STRUCTURED_OUTPUT", "maybe")
    with pytest.raises(ValueError):
        load_settings(tmp_path / "absent")


def test_max_user_id_chars_default_and_validation():
    assert Settings().max_user_id_chars == 64
    with pytest.raises(ValueError):
        Settings(max_user_id_chars=0)


def test_max_user_id_chars_loads_from_environment(tmp_path):
    env = tmp_path / "settings.env"
    env.write_text("MAX_USER_ID_CHARS=80\n", encoding="utf-8")
    assert load_settings(env).max_user_id_chars == 80


def test_schema_roundtrip_and_independent_defaults():
    first, second = AgentOutput(answer="你好"), AgentOutput(answer="再见")
    first.claims.append({"text": "示例", "chunk_ids": []})
    assert not second.claims
    assert ToolResult.model_validate_json(ToolResult(status="empty").model_dump_json()).status == "empty"
    r = RunResult(run_id="run_1", session_id="s_1", status="failed", evidence_status="none",
                  answer="错误", claims=[], evidence_refs=[], dropped_chunk_ids=[],
                  error={"code": "INTERNAL", "message": "错误"}, meta={})
    assert RunResult.model_validate_json(r.model_dump_json()) == r
