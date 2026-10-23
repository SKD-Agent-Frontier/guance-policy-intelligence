"""CLI 与未来 Web 共用的唯一任务入口。"""
import re
from datetime import datetime, timezone
from uuid import uuid4

from .agent import build_agent, chat_model
from .config import Settings, load_settings
from .context import RunContext
from .kb import get_kb
from .output import ERRORS, build_result
from .runner import execute
from .runtime import Runtime
from .schemas import RunError, RunResult, TaskRequest


async def submit(question: str, session_id: str | None = None,
                 user_id: str | None = None, settings: Settings | None = None) -> RunResult:
    task = TaskRequest(run_id="run_" + uuid4().hex,
                       session_id=session_id if isinstance(session_id, str) else "s_" + uuid4().hex,
                       is_new_session=session_id is None,
                       user_id=user_id if isinstance(user_id, str) else None,
                       question=question.strip() if isinstance(question, str) else "",
                       created_at=datetime.now(timezone.utc).isoformat())
    rt, ctx = None, None
    try:
        s = settings or load_settings()
        rt = Runtime(s, task)
        rt.received()
        message = None
        if not isinstance(question, str) or not task.question:
            message = "问题为空或格式不正确"
        elif len(task.question) > s.max_input_chars:
            message = f"问题超过 {s.max_input_chars} 字，请精简后再提交"
        elif session_id is not None and (not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id)):
            message = "会话 ID 格式不正确"
        elif user_id is not None and (not isinstance(user_id, str) or not user_id.strip()):
            message = "用户 ID 格式不正确"
        if message:
            rt.log("rejected", status="failed", error_code="INVALID_INPUT")
            result = build_result(task, None, None, rt.meta, RunError(code="INVALID_INPUT", message=message))
        else:
            rt.prepare()
            ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
            if not s.llm_base_url or not s.llm_api_key:
                result = build_result(task, None, ctx, rt.meta, RunError(
                    code="MODEL_UNAVAILABLE", message=ERRORS["MODEL_UNAVAILABLE"]))
            else:
                outcome = await execute(build_agent(s, chat_model(s, rt.client)), task, ctx, rt)
                result = build_result(task, outcome, ctx, rt.meta)
        rt.finish(result, ctx)
        return result
    except Exception as exc:
        code = "SESSION_FORBIDDEN" if isinstance(exc, PermissionError) and str(exc) == "SESSION_FORBIDDEN" else "INTERNAL"
        result = build_result(task, None, ctx, rt.meta if rt else {}, RunError(code=code, message=ERRORS[code]))
        if rt:
            try:
                rt.log("rejected", error_code=code, error_detail=type(exc).__name__)
                rt.finish(result, ctx)
            except OSError:
                pass
        return result
    finally:
        if rt:
            rt.close()
