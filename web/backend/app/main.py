import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from guance_agent.client import aclose_client
from guance_agent.config import load_settings
from guance_agent.kb import close_kb, warm_up
from guance_agent.main import submit

from .preparation import (
    CONFLICT_DETAIL,
    PreparationStateError,
    ResearchPreparationService,
)
from .preparation_models import ResearchPrepareRequest
from .schemas import RunRequest

logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _cors_origins() -> list[str]:
    configured = os.environ.get("GUANCE_CORS_ORIGINS")
    origins = (
        [origin.strip() for origin in configured.split(",") if origin.strip()]
        if configured is not None
        else list(DEFAULT_CORS_ORIGINS)
    )
    if "*" in origins:
        raise ValueError("GUANCE_CORS_ORIGINS 不允许通配 Origin")
    return origins


def create_app(settings=None, env_file=None, preparation_service=None) -> FastAPI:
    configured_origins = _cors_origins()
    selected_env_file = env_file or os.environ.get("GUANCE_ENV_FILE")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.ready = False
        application.state.settings = None
        try:
            stage = "load_settings"
            try:
                runtime_settings = settings if settings is not None else load_settings(selected_env_file)
                application.state.settings = runtime_settings
                stage = "warm_up"
                await asyncio.to_thread(warm_up, runtime_settings)
            except Exception as exc:  # noqa: BLE001
                # 配置或预热失败时保持未就绪，仍启动 HTTP 服务以返回 503。
                logger.error("生命周期失败 stage=%s exception_type=%s", stage, type(exc).__name__)
                application.state.settings = None
            else:
                application.state.ready = True
            yield
        finally:
            application.state.ready = False
            try:
                await asyncio.to_thread(close_kb)
            except Exception as exc:  # noqa: BLE001
                logger.error("生命周期失败 stage=%s exception_type=%s", "close_kb", type(exc).__name__)
            finally:
                try:
                    await aclose_client()
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "生命周期失败 stage=%s exception_type=%s", "aclose_client", type(exc).__name__
                    )

    application = FastAPI(lifespan=lifespan)
    application.state.ready = False
    application.state.settings = None
    application.state.preparation_service = (
        preparation_service if preparation_service is not None else ResearchPreparationService()
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, _exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "请求参数无效"})

    @application.get("/api/health")
    async def health():
        if not application.state.ready:
            return JSONResponse(status_code=503, content={"detail": "服务尚未就绪"})
        return {"status": "ok", "service": "guance-web-api"}

    @application.post("/api/research/prepare")
    async def prepare_research(payload: ResearchPrepareRequest):
        if not application.state.ready:
            return JSONResponse(status_code=503, content={"detail": "服务尚未就绪"})
        try:
            result = await application.state.preparation_service.prepare(payload)
            return JSONResponse(content=jsonable_encoder(result))
        except PreparationStateError:
            return JSONResponse(status_code=409, content={"detail": CONFLICT_DETAIL})
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=500, content={"detail": "服务内部错误"})

    @application.post("/api/runs")
    async def create_run(payload: RunRequest):
        if not application.state.ready:
            return JSONResponse(status_code=503, content={"detail": "服务尚未就绪"})
        try:
            result = await submit(
                question=payload.question,
                task_type=payload.task_type,
                targets=payload.targets,
                dimensions=payload.dimensions,
                session_id=payload.session_id,
                settings=application.state.settings,
            )
            return JSONResponse(content=jsonable_encoder(result))
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=500, content={"detail": "服务内部错误"})

    return application


app = create_app()
