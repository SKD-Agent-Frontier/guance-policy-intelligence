import logging
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from guance_agent.config import Settings
from guance_agent.schemas import Claim, EvidenceRef, RunError, RunResult
from pydantic import BaseModel

from app import main as api
from app.schemas import RunRequest


class FullRunResult(BaseModel):
    run_id: str = "run-test"
    session_id: str = "session-test"
    task_type: str = "qa"
    status: str = "failed"
    evidence_status: str = "insufficient"
    answer: str = "未找到充分证据"
    claims: list[dict] = [{"text": "结论", "chunk_ids": ["chunk-1"]}]
    evidence_refs: list[dict] = [{"chunk_id": "chunk-1", "title": "政策"}]
    structured: dict | None = {"policies": []}
    warnings: list[str] = ["证据不足"]
    error: dict = {"code": "AGENT_ERROR", "message": "业务失败"}
    route: dict = {"type": "qa"}
    meta: dict = {"private_agent_value": "retain"}
    dropped_chunk_ids: list[str] = []


@pytest.fixture
def settings(tmp_path):
    return Settings(run_log=tmp_path / "runs.jsonl", session_path=tmp_path / "sessions.sqlite")


@pytest.fixture
def client(monkeypatch, settings):
    monkeypatch.setattr(api, "warm_up", lambda _settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)
    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    with TestClient(api.create_app(settings=settings)) as test_client:
        yield test_client


def test_health_reports_http_readiness_only(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "guance-web-api"}


@pytest.mark.parametrize(
    ("task_type", "payload", "expected"),
    [
        (
            "qa",
            {"question": "  问政策 中文  "},
            {"question": "  问政策 中文  ", "task_type": "qa"},
        ),
        (
            "extract",
            {"question": "提取要素", "task_type": "extract", "targets": ["37982"], "dimensions": ["资金规模"]},
            {"question": "提取要素", "task_type": "extract", "targets": ["37982"], "dimensions": ["资金规模"]},
        ),
        (
            "organize",
            {"question": "梳理政策", "task_type": "organize"},
            {"question": "梳理政策", "task_type": "organize"},
        ),
        (
            "compare",
            {"question": "比较政策", "task_type": "compare", "targets": ["37982", "38366"]},
            {"question": "比较政策", "task_type": "compare", "targets": ["37982", "38366"]},
        ),
        (
            "compare_plans",
            {
                "question": "比较方案",
                "task_type": "compare_plans",
                "targets": ["37982", "38366"],
                "dimensions": ["支持措施"],
                "session_id": "session_1",
            },
            {
                "question": "比较方案",
                "task_type": "compare_plans",
                "targets": ["37982", "38366"],
                "dimensions": ["支持措施"],
                "session_id": "session_1",
            },
        ),
    ],
)
def test_five_task_types_are_forwarded_once(client, monkeypatch, task_type, payload, expected):
    submit = AsyncMock(return_value=FullRunResult(task_type=task_type))
    monkeypatch.setattr(api, "submit", submit)

    response = client.post("/api/runs", json=payload)

    assert response.status_code == 200
    assert response.json()["task_type"] == task_type
    submit.assert_awaited_once()
    call = submit.await_args.kwargs
    for key, value in expected.items():
        assert call[key] == value
    assert call["settings"] is client.app.state.settings


@pytest.mark.parametrize(
    ("status", "evidence_status", "structured"),
    [
        ("failed", "insufficient", {"matrix": []}),
        ("limit_reached", "none", None),
        ("completed", "insufficient", None),
    ],
)
def test_shared_run_result_serializes_completely(
    client, monkeypatch, status, evidence_status, structured
):
    result = RunResult(
        run_id="run-shared",
        session_id="session-shared",
        status=status,
        evidence_status=evidence_status,
        answer="证据不足",
        claims=[Claim(text="结论", chunk_ids=["chunk-1"], quotes=["原文"])],
        evidence_refs=[
            EvidenceRef(
                planid="37982",
                chunk_id="chunk-1",
                title="政策",
                area="北京市",
                fyp=None,
                admin_level="市级",
                plantype_label="实施方案",
                section_idx=1,
                section_path="第一条",
                char_start=0,
                char_end=2,
                source="kb_read",
                observed_ranges=[(0, 2)],
                complete=True,
            )
        ],
        dropped_chunk_ids=["chunk-old"],
        error=RunError(code="AGENT_ERROR", message="业务失败") if status != "completed" else None,
        meta={"agent_meta": "retain"},
        warnings=["证据不足"],
        task_type="compare",
        structured=structured,
        route={"type": "compare"},
    )
    monkeypatch.setattr(api, "submit", AsyncMock(return_value=result))

    response = client.post("/api/runs", json={"question": "问政策"})

    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")


def test_extension_fields_are_not_dropped(client, monkeypatch):
    monkeypatch.setattr(api, "submit", AsyncMock(return_value=FullRunResult()))

    response = client.post("/api/runs", json={"question": "问政策"})

    assert response.status_code == 200
    assert response.json()["meta"] == {"private_agent_value": "retain"}
    assert response.json()["dropped_chunk_ids"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"question": 123},
        {"question": "问", "targets": [1]},
        {"question": "问", "dimensions": "资金规模"},
        {"question": "问", "session_id": 5},
        {"question": "问", "task_type": None},
        {"question": "问", "unexpected": True},
    ],
)
def test_http_type_errors_and_extra_fields_return_422(client, monkeypatch, payload):
    submit = AsyncMock()
    monkeypatch.setattr(api, "submit", submit)

    response = client.post("/api/runs", json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": "请求参数无效"}
    submit.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "问题", "task_type": "unsupported"},
        {"question": "x" * 4001},
        {"question": "问题", "session_id": "bad session"},
        {"question": "问题", "targets": []},
    ],
)
def test_agent_business_rejection_stays_http_200_without_model_call(client, monkeypatch, payload):
    from guance_agent.main import submit as real_submit

    monkeypatch.setattr(api, "submit", real_submit)

    response = client.post("/api/runs", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_4000_character_question_passes_agent_length_validation(client, monkeypatch):
    from guance_agent import main as agent_main

    monkeypatch.setattr(agent_main, "run_task", AsyncMock(return_value={"status": "accepted"}))
    monkeypatch.setattr(api, "submit", agent_main.submit)

    response = client.post("/api/runs", json={"question": "中" * 4000})

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}


def test_4001_character_question_is_rejected_by_agent(client, monkeypatch):
    from guance_agent.main import submit as real_submit

    monkeypatch.setattr(api, "submit", real_submit)

    response = client.post("/api/runs", json={"question": "中" * 4001})

    assert response.status_code == 200
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_unexpected_submit_exception_is_generic(client, monkeypatch):
    monkeypatch.setattr(api, "submit", AsyncMock(side_effect=RuntimeError("secret path and key")))

    response = client.post("/api/runs", json={"question": "问政策"})

    assert response.status_code == 500
    assert response.json() == {"detail": "服务内部错误"}
    assert "secret" not in response.text


def test_lifespan_loads_settings_and_warms_once(monkeypatch):
    loaded = Settings()
    load = Mock()
    warm = Mock()
    close = Mock()
    close_client = AsyncMock()
    monkeypatch.setattr(api, "load_settings", lambda path: (load(path), loaded)[1])
    monkeypatch.setattr(api, "warm_up", warm)
    monkeypatch.setattr(api, "close_kb", close)
    monkeypatch.setattr(api, "aclose_client", close_client)
    test_app = api.create_app(env_file="chosen.env")

    with TestClient(test_app) as test_client:
        assert test_client.get("/api/health").status_code == 200
        assert test_client.get("/api/health").status_code == 200
        assert test_client.app.state.settings is loaded

    load.assert_called_once_with("chosen.env")
    warm.assert_called_once_with(loaded)
    close.assert_called_once_with()
    close_client.assert_awaited_once()


def test_lifespan_uses_guance_env_file(monkeypatch):
    load = Mock(return_value=Settings())
    monkeypatch.setenv("GUANCE_ENV_FILE", "from-env.env")
    monkeypatch.setattr(api, "load_settings", load)
    monkeypatch.setattr(api, "warm_up", Mock())
    monkeypatch.setattr(api, "close_kb", Mock())
    monkeypatch.setattr(api, "aclose_client", AsyncMock())

    with TestClient(api.create_app()) as test_client:
        assert test_client.get("/api/health").status_code == 200

    load.assert_called_once_with("from-env.env")


@pytest.mark.parametrize("failure", ["settings", "warm_up"])
def test_startup_failure_returns_503_and_runs_cleanup(monkeypatch, failure):
    close = Mock()
    close_client = AsyncMock()
    monkeypatch.setattr(api, "load_settings", lambda _path: Settings())
    if failure == "settings":
        monkeypatch.setattr(
            api, "load_settings", lambda _path: (_ for _ in ()).throw(RuntimeError("private"))
        )
    else:
        monkeypatch.setattr(api, "warm_up", lambda _settings: (_ for _ in ()).throw(RuntimeError("private")))
    monkeypatch.setattr(api, "close_kb", close)
    monkeypatch.setattr(api, "aclose_client", close_client)

    with TestClient(api.create_app()) as test_client:
        assert test_client.get("/api/health").status_code == 503
        response = test_client.post("/api/runs", json={"question": "问政策"})
        assert response.status_code == 503
        assert "private" not in response.text

    close.assert_called_once_with()
    close_client.assert_awaited_once()


def test_close_kb_error_does_not_skip_client_cleanup(monkeypatch):
    close_client = AsyncMock()
    monkeypatch.setattr(api, "warm_up", lambda _settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: (_ for _ in ()).throw(RuntimeError("private")))
    monkeypatch.setattr(api, "aclose_client", close_client)

    with TestClient(api.create_app(settings=Settings())) as test_client:
        assert test_client.get("/api/health").status_code == 200

    close_client.assert_awaited_once()


@pytest.mark.parametrize("stage", ["load_settings", "warm_up", "close_kb", "aclose_client"])
def test_lifespan_logs_only_stage_and_exception_type(monkeypatch, caplog, stage):
    private_message = "private diagnostic sentinel"
    monkeypatch.setattr(api, "load_settings", Mock(return_value=Settings()))
    monkeypatch.setattr(api, "warm_up", Mock())
    monkeypatch.setattr(api, "close_kb", Mock())
    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    getattr(api, stage).side_effect = RuntimeError(private_message)

    with (
        caplog.at_level(logging.ERROR, logger=api.__name__),
        TestClient(api.create_app()) as test_client,
    ):
        expected_status = 503 if stage in {"load_settings", "warm_up"} else 200
        assert test_client.get("/api/health").status_code == expected_status

    records = [record for record in caplog.records if record.name == api.__name__]
    assert len(records) == 1
    assert records[0].getMessage() == (
        f"生命周期失败 stage={stage} exception_type=RuntimeError"
    )
    assert records[0].exc_info is None
    assert records[0].exc_text is None
    assert private_message not in caplog.text


def test_cors_uses_allowlist_and_rejects_wildcard(monkeypatch):
    monkeypatch.delenv("GUANCE_CORS_ORIGINS", raising=False)
    test_app = api.create_app(settings=Settings())
    with TestClient(test_app) as test_client:
        response = test_client.options(
            "/api/runs",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        disallowed = test_client.get(
            "/api/health",
            headers={"Origin": "https://untrusted.example"},
        )
        assert disallowed.status_code == 200
        assert "access-control-allow-origin" not in disallowed.headers

    monkeypatch.setenv("GUANCE_CORS_ORIGINS", "https://example.test, http://localhost:5173")
    custom_app = api.create_app(settings=Settings())
    assert custom_app.user_middleware[0].kwargs["allow_origins"] == [
        "https://example.test",
        "http://localhost:5173",
    ]
    monkeypatch.setenv("GUANCE_CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match="不允许通配 Origin"):
        api.create_app(settings=Settings())


def test_schema_is_strict_and_rejects_extras():
    with pytest.raises(ValueError):
        RunRequest.model_validate({"question": "问", "other": True})
