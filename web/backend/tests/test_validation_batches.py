import asyncio
import io
import time
import zipfile
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from guance_agent.config import Settings
from xlsxwriter import Workbook

from app import main as api
from app.request_limits import RequestBodyLimitMiddleware
from app.schemas import RunRequest
from app.validation_batches import ValidationBatchService, router
from app.validation_excel import HEADERS_OUT, export_excel, parse_excel


def workbook_bytes(rows):
    output = io.BytesIO()
    workbook = Workbook(output, {"in_memory": True})
    sheet = workbook.add_worksheet()
    sheet.write_row(0, 0, ["问题", "预期答案"])
    for index, row in enumerate(rows, 1):
        sheet.write_row(index, 0, row)
    workbook.close()
    return output.getvalue()


def create_client(run_handler):
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware)
    app.state.ready = True
    app.state.validation_batches = ValidationBatchService()

    @app.post("/api/runs")
    async def run(payload: RunRequest):
        return await run_handler(payload)

    app.include_router(router)
    return TestClient(app)


def wait_for_terminal_state(client, job_id, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/validation-batches/jobs/{job_id}")
        assert response.status_code == 200
        status = response.json()
        if status["state"] in {"finished", "failed", "cancelled"}:
            return status
        time.sleep(0.01)
    raise AssertionError("batch did not reach a terminal state")


def test_completed_run_is_execution_success_not_an_accuracy_claim():
    async def run(_payload):
        return {"status": "completed", "answer": "与预期答案相反"}

    with create_client(run) as client:
        preview = client.post(
            "/api/validation-batches/preview",
            files={"file": ("input.xlsx", workbook_bytes([["问题", "正确答案"]]))},
        )
        assert preview.status_code == 200
        started = client.post(f"/api/validation-batches/{preview.json()['upload_id']}/start")
        status = wait_for_terminal_state(client, started.json()["job_id"])

        assert status["rows"][0]["status"] == "执行成功"
        assert status["rows"][0]["evaluation"] == "未评估"
        exported = client.get(f"/api/validation-batches/jobs/{started.json()['job_id']}/export")
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as workbook:
            shared_strings = workbook.read("xl/sharedStrings.xml").decode("utf-8")
        assert "执行状态" in shared_strings
        assert "正确性评估" in shared_strings
        assert "执行成功" in shared_strings
        assert "未评估" in shared_strings


def test_batch_uses_the_application_runs_api_contract(monkeypatch, tmp_path):
    seen = []

    async def submit(**kwargs):
        seen.append(kwargs)
        return {"status": "completed", "answer": "已调用研究运行入口"}

    monkeypatch.setattr(api, "submit", submit)
    monkeypatch.setattr(api, "warm_up", lambda _settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)
    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    settings = Settings(
        run_log=tmp_path / "runs.jsonl",
        session_path=tmp_path / "sessions.sqlite",
    )

    with TestClient(api.create_app(settings=settings)) as client:
        preview = client.post(
            "/api/validation-batches/preview",
            files={"file": ("input.xlsx", workbook_bytes([["真实问题", "预期答案"]]))},
        )
        started = client.post(f"/api/validation-batches/{preview.json()['upload_id']}/start")
        status = wait_for_terminal_state(client, started.json()["job_id"])

    assert preview.status_code == 200
    assert started.status_code == 200
    assert status["rows"][0]["status"] == "执行成功"
    assert len(seen) == 1
    assert seen[0]["question"] == "真实问题"
    assert seen[0]["task_type"] == "qa"
    assert seen[0]["session_id"].startswith("v_")


def test_cancel_interrupts_current_run_request_and_marks_row_cancelled():
    async def run(_payload):
        import asyncio

        await asyncio.Event().wait()

    with create_client(run) as client:
        preview = client.post(
            "/api/validation-batches/preview",
            files={"file": ("input.xlsx", workbook_bytes([["问题", "答案"]]))},
        )
        started = client.post(f"/api/validation-batches/{preview.json()['upload_id']}/start")
        job_id = started.json()["job_id"]
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if client.get(f"/api/validation-batches/jobs/{job_id}").json()["state"] == "running":
                break
            time.sleep(0.01)

        cancelled = client.post(f"/api/validation-batches/jobs/{job_id}/cancel")
        status = wait_for_terminal_state(client, job_id)

        assert cancelled.json()["state"] == "cancelling"
        assert status["state"] == "cancelled"
        assert status["rows"][0]["status"] == "已取消"


@pytest.mark.asyncio
async def test_cancel_queued_batch_marks_unstarted_rows_cancelled():
    entered = asyncio.Event()
    release = asyncio.Event()
    app = FastAPI()
    app.state.ready = True
    service = ValidationBatchService()

    @app.post("/api/runs")
    async def run(_payload: RunRequest):
        entered.set()
        await release.wait()
        return {"status": "completed", "answer": "回答"}

    rows = parse_excel(workbook_bytes([["问题", "答案"]]))
    first = service.start(service.preview(rows)["upload_id"], app)
    await asyncio.wait_for(entered.wait(), 1)
    second = service.start(
        service.preview(parse_excel(workbook_bytes([["排队问题", "答案"]])))["upload_id"], app
    )
    service.cancel(second["job_id"])
    release.set()
    await asyncio.wait_for(asyncio.gather(*service.tasks), 2)

    assert service.get(first["job_id"])["state"] == "finished"
    queued = service.get(second["job_id"])
    assert queued["state"] == "cancelled"
    assert queued["rows"][0]["status"] == "已取消"
    assert queued["done"] == 0


def test_per_question_timeout_is_reported_as_unassessed_timeout(monkeypatch):
    async def run(_payload):
        import asyncio

        await asyncio.Event().wait()

    monkeypatch.setattr("app.validation_batches.PER_QUESTION_TIMEOUT_SECONDS", 0.01)
    with create_client(run) as client:
        preview = client.post(
            "/api/validation-batches/preview",
            files={"file": ("input.xlsx", workbook_bytes([["问题", "答案"]]))},
        )
        started = client.post(f"/api/validation-batches/{preview.json()['upload_id']}/start")
        status = wait_for_terminal_state(client, started.json()["job_id"])

        assert status["rows"][0]["status"] == "超时"
        assert status["rows"][0]["evaluation"] == "未评估"


def test_finished_results_have_count_and_content_retention_caps(monkeypatch):
    service = ValidationBatchService()
    now = time.monotonic()
    service.jobs = {
        key: {"id": key, "state": "finished", "updated": now + index,
              "rows": [{"question": "q", "expected": "a", "answer": "x" * 3}]}
        for index, key in enumerate(("old", "middle", "new"))
    }
    monkeypatch.setattr("app.validation_batches.MAX_RETAINED_JOBS", 2)
    monkeypatch.setattr("app.validation_batches.MAX_RETAINED_RESULT_CHARS", 10)

    service.prune()

    assert set(service.jobs) == {"new", "middle"}
    monkeypatch.setattr("app.validation_batches.MAX_RETAINED_JOBS", 8)
    monkeypatch.setattr("app.validation_batches.MAX_RETAINED_RESULT_CHARS", 5)
    service.prune()
    assert set(service.jobs) == {"new"}


def test_excel_output_names_execution_and_evaluation_separately():
    assert HEADERS_OUT == ("问题", "预期答案", "智能体实际回复", "耗时(秒)", "执行状态", "正确性评估")
    rows = [{
        "question": "问题", "expected": "答案", "answer": "回答",
        "duration": 1.2, "status": "执行成功", "evaluation": "未评估",
    }]
    exported = export_excel(rows)
    assert parse_excel(workbook_bytes([["问题", "答案"]]))[0]["expected"] == "答案"
    assert exported.startswith(b"PK")


def test_preview_rejects_aggregate_input_over_budget(monkeypatch):
    monkeypatch.setattr("app.validation_excel.MAX_TOTAL_INPUT_CHARS", 5)
    with pytest.raises(ValueError, match="总长度过大"):
        parse_excel(workbook_bytes([["问题", "预期答案"]]))


def test_preview_rejects_wrong_extension_and_oversized_upload():
    async def run(_payload):
        return {"status": "completed", "answer": "回答"}

    with create_client(run) as client:
        wrong_extension = client.post(
            "/api/validation-batches/preview",
            files={"file": ("input.csv", workbook_bytes([["问题", "答案"]]))},
        )
        too_large = client.post(
            "/api/validation-batches/preview",
            files={"file": ("input.xlsx", b"x" * (3 * 1024 * 1024 + 1))},
        )
        request_too_large = client.post(
            "/api/validation-batches/preview",
            files={"file": ("input.xlsx", b"x" * (3 * 1024 * 1024 + 64 * 1024 + 1))},
        )

    assert wrong_extension.status_code == 422
    assert too_large.status_code == 422
    assert request_too_large.status_code == 413


def test_preview_rejects_formula_cells():
    output = io.BytesIO()
    workbook = Workbook(output, {"in_memory": True})
    sheet = workbook.add_worksheet()
    sheet.write_row(0, 0, ["问题", "预期答案"])
    sheet.write_formula(1, 0, "=1+1")
    sheet.write(1, 1, "答案")
    workbook.close()

    try:
        parse_excel(output.getvalue())
    except ValueError as exc:
        assert "不能是公式" in str(exc)
    else:
        raise AssertionError("formula input was accepted")
