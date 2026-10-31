from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from guance_agent.config import Settings
from guance_agent.schemas import RunError, RunResult

from app import main as api


@pytest.mark.parametrize(("code", "status"), [
    ("RAG_TIMEOUT", "failed"),
    ("RAG_BACKEND_ERROR", "failed"),
    ("MODEL_TIMEOUT", "failed"),
    ("RUN_TIMEOUT", "limit_reached"),
    (None, "completed"),
])
def test_qa_failure_categories_survive_http_transport(monkeypatch, tmp_path, code, status):
    result = RunResult(
        run_id="run-test", session_id="session-test", task_type="qa", status=status,
        evidence_status="insufficient", answer="故障提示" if code else "本次未找到充分证据",
        claims=[], evidence_refs=[], dropped_chunk_ids=[],
        error=RunError(code=code, message="明确的失败原因") if code else None,
        meta={"tool_calls": [], "retrieval_failure": {"code": code}}, warnings=[],
    )
    submit = AsyncMock(return_value=result)
    monkeypatch.setattr(api, "submit", submit)
    monkeypatch.setattr(api, "warm_up", lambda settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)
    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    settings = Settings(run_log=tmp_path / "runs.jsonl", session_path=tmp_path / "sessions.sqlite")
    with TestClient(api.create_app(settings=settings)) as client:
        response = client.post("/api/runs", json={"question": "查询政策", "task_type": "qa"})
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    submit.assert_awaited_once()
