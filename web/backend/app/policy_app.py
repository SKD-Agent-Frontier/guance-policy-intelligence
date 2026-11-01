"""独立启动政策目录 API；不预热 Agent/RAG 检索栈。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .policy_routes import _catalog, router


def _cors_origins() -> list[str]:
    configured = os.environ.get("GUANCE_CORS_ORIGINS")
    origins = ([origin.strip() for origin in configured.split(",") if origin.strip()]
               if configured is not None else ["http://localhost:5173", "http://127.0.0.1:5173"])
    if "*" in origins:
        raise ValueError("GUANCE_CORS_ORIGINS 不允许通配 Origin")
    return origins


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.settings = SimpleNamespace(
        kb_backend="rag", rag_store_project=os.environ.get("RAG_STORE_PROJECT"))
    application.state.policy_catalog = None
    application.state.policy_regions = None
    yield


app = FastAPI(title="政策目录 API", lifespan=lifespan)
app.state.settings = SimpleNamespace(kb_backend="rag", rag_store_project=os.environ.get("RAG_STORE_PROJECT"))
app.state.policy_catalog = None
app.state.policy_regions = None
app.include_router(router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, _exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "请求参数无效"})


@app.get("/api/health")
async def health(request: Request):
    try:
        service = await _catalog(request)
    except HTTPException as exc:
        return JSONResponse(status_code=503, content={"detail": exc.detail})
    dataset = service.dataset()
    return {"status": "ok", "service": "policy-catalog-api",
            "generation_id": dataset["generation_id"], "revision": dataset["revision"],
            "total_documents": dataset["total_documents"],
            "region_mapping_ready": dataset["region_mapping_version"] is not None}
