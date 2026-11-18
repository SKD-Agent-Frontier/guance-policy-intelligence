"""In-memory batch execution that uses the same API contract as interactive runs."""
from __future__ import annotations

import asyncio
import io
import time
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from .validation_excel import (
    MAX_UPLOAD_BYTES,
    TemplateError,
    export_excel,
    parse_excel,
)

router = APIRouter(prefix="/api/validation-batches", tags=["validation-batches"])
UPLOAD_TTL_SECONDS = 1800
RESULT_TTL_SECONDS = 3600
PER_QUESTION_TIMEOUT_SECONDS = 210
MAX_ACTIVE_JOBS = 2
MAX_PENDING_UPLOADS = 8
MAX_RETAINED_JOBS = 8
MAX_RETAINED_RESULT_CHARS = 20_000_000


class ValidationBatchService:
    def __init__(self):
        self.uploads: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.worker_gate = asyncio.Semaphore(1)
        self.tasks: set[asyncio.Task] = set()

    def prune(self):
        now = time.monotonic()
        self.uploads = {
            key: item for key, item in self.uploads.items()
            if now - item["created"] < UPLOAD_TTL_SECONDS
        }
        self.jobs = {
            key: item for key, item in self.jobs.items()
            if item["state"] in {"queued", "running", "cancelling"}
            or now - item["updated"] < RESULT_TTL_SECONDS
        }
        retained_chars = 0
        retained_count = 0
        terminal = sorted(
            (item for item in self.jobs.values() if item["state"] not in {"queued", "running", "cancelling"}),
            key=lambda item: item["updated"],
            reverse=True,
        )
        for item in terminal:
            item_chars = sum(
                len(row.get(key, ""))
                for row in item["rows"]
                for key in ("question", "expected", "answer")
            )
            if retained_count >= MAX_RETAINED_JOBS or (
                retained_count and retained_chars + item_chars > MAX_RETAINED_RESULT_CHARS
            ):
                self.jobs.pop(item["id"], None)
                continue
            retained_count += 1
            retained_chars += item_chars

    def preview(self, rows: list[dict]) -> dict:
        self.prune()
        if len(self.uploads) >= MAX_PENDING_UPLOADS:
            raise HTTPException(status_code=429, detail="待执行的上传文件过多")
        upload_id = uuid4().hex
        self.uploads[upload_id] = {"created": time.monotonic(), "rows": rows}
        return {
            "upload_id": upload_id,
            "total": len(rows),
            "columns": ["问题", "预期答案"],
            "preview": [
                {"question": row["question"], "expected": row["expected"]}
                for row in rows[:10]
            ],
        }

    def start(self, upload_id: str, app) -> dict:
        self.prune()
        if not getattr(app.state, "ready", False):
            raise HTTPException(status_code=503, detail="研究服务尚未就绪")
        active = sum(
            job["state"] in {"queued", "running", "cancelling"}
            for job in self.jobs.values()
        )
        if active >= MAX_ACTIVE_JOBS:
            raise HTTPException(status_code=429, detail="当前批量任务过多")
        upload = self.uploads.pop(upload_id, None)
        if upload is None:
            raise HTTPException(status_code=404, detail="上传已过期或已开始执行")

        job_id = uuid4().hex
        now = time.monotonic()
        job = {
            "id": job_id,
            "rows": upload["rows"],
            "total": len(upload["rows"]),
            "done": 0,
            "state": "queued",
            "cancel": False,
            "current_request": None,
            "created": now,
            "updated": now,
        }
        self.jobs[job_id] = job
        task = asyncio.create_task(self.run(job, app))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {"job_id": job_id, "state": "queued", "total": job["total"]}

    async def run(self, job: dict, app):
        try:
            async with self.worker_gate:
                if job["cancel"]:
                    job["state"] = "cancelled"
                    return
                job["state"] = "running"
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://validation-batch.local",
                    timeout=None,
                ) as client:
                    for row in job["rows"]:
                        if job["cancel"]:
                            row["status"] = "已取消"
                            row["evaluation"] = "未评估"
                            job["state"] = "cancelled"
                            break
                        started = time.monotonic()
                        request_task = asyncio.create_task(client.post(
                            "/api/runs",
                            json={
                                "question": row["question"],
                                "task_type": "qa",
                                "session_id": "v_" + uuid4().hex,
                            },
                        ))
                        job["current_request"] = request_task
                        try:
                            response = await asyncio.wait_for(
                                request_task,
                                timeout=PER_QUESTION_TIMEOUT_SECONDS,
                            )
                            self._record_response(row, response)
                        except asyncio.CancelledError:
                            if not job["cancel"]:
                                raise
                            row["status"] = "已取消"
                            row["evaluation"] = "未评估"
                            job["state"] = "cancelled"
                            break
                        except TimeoutError:
                            row["status"] = "超时"
                            row["evaluation"] = "未评估"
                        except Exception:  # noqa: BLE001
                            row["status"] = "执行失败"
                            row["evaluation"] = "未评估"
                        finally:
                            job["current_request"] = None
                            row["duration"] = round(time.monotonic() - started, 1)
                            job["done"] += 1
                            job["updated"] = time.monotonic()
                    if job["state"] != "cancelled":
                        job["state"] = "finished"
        except asyncio.CancelledError:
            job["state"] = "cancelled"
            raise
        except Exception:  # noqa: BLE001
            job["state"] = "failed"
        finally:
            if job["state"] == "cancelled":
                for row in job["rows"]:
                    if row["status"] == "待运行":
                        row["status"] = "已取消"
            job["updated"] = time.monotonic()

    @staticmethod
    def _record_response(row: dict, response: httpx.Response):
        row["evaluation"] = "未评估"
        if response.status_code != 200:
            row["status"] = "超时" if response.status_code == 504 else "执行失败"
            return
        payload = response.json()
        answer = payload.get("answer", "")
        row["answer"] = answer[:32000] if isinstance(answer, str) else ""
        code = (payload.get("error") or {}).get("code", "")
        if code in {"MODEL_TIMEOUT", "TIMEOUT"}:
            row["status"] = "超时"
        elif payload.get("status") == "completed":
            row["status"] = "执行成功"
        else:
            row["status"] = "执行失败"

    def get(self, job_id: str) -> dict:
        self.prune()
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在或已过期")
        return job

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["state"] in {"queued", "running"}:
            job["cancel"] = True
            job["state"] = "cancelling"
            current_request = job.get("current_request")
            if current_request is not None:
                current_request.cancel()
        return job

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.jobs.clear()
        self.uploads.clear()


def service(request: Request) -> ValidationBatchService:
    return request.app.state.validation_batches


@router.post("/preview")
async def preview(request: Request, file: Annotated[UploadFile, File()]):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="请上传模板4格式的.xlsx文件")
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        rows = parse_excel(content)
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
    return service(request).preview(rows)


@router.post("/{upload_id}/start")
async def start(request: Request, upload_id: str):
    return service(request).start(upload_id, request.app)


@router.get("/jobs/{job_id}")
async def status(request: Request, job_id: str):
    job = service(request).get(job_id)
    return {
        "job_id": job["id"],
        "state": job["state"],
        "total": job["total"],
        "completed": job["done"],
        "rows": [
            {
                "index": index + 1,
                "question": row["question"],
                "status": row["status"],
                "evaluation": row.get("evaluation", "未评估"),
                "duration": row["duration"],
            }
            for index, row in enumerate(job["rows"])
        ],
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel(request: Request, job_id: str):
    job = service(request).cancel(job_id)
    return {"state": job["state"]}


@router.get("/jobs/{job_id}/export")
async def export(request: Request, job_id: str):
    job = service(request).get(job_id)
    if job["state"] not in {"finished", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="任务尚未结束")
    content = export_excel(job["rows"])
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="guance-validation-results.xlsx"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
