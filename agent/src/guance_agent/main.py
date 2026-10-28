"""CLI 与未来 Web 共用的唯一任务入口：只做校验、装配与转交。"""
import re
from datetime import datetime, timezone
from uuid import uuid4

from .business import get_module, register
from .business.compare import PolicyComparisonModule, SchemeComparisonModule
from .business.organize import OrganizeModule
from .config import ConfigurationError, Settings, load_settings
from .orchestrator import run_task
from .output import ERRORS, build_result
from .runtime import Runtime, SessionForbidden
from .schemas import RunError, RunResult, TaskRequest

SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

register(PolicyComparisonModule())
register(SchemeComparisonModule())
register(OrganizeModule())

def _build_task(question: str, session_id, user_id, task_type, dimensions, targets) -> TaskRequest:
    return TaskRequest(
        run_id="run_" + uuid4().hex,
        session_id=session_id if isinstance(session_id, str) else "s_" + uuid4().hex,
        user_id=user_id if isinstance(user_id, str) else None,
        question=question.strip() if isinstance(question, str) else "",
        created_at=datetime.now(timezone.utc).isoformat(),
        task_type=task_type if isinstance(task_type, str) else "",
        dimensions=list(dimensions) if isinstance(dimensions, list)
        and all(isinstance(dimension, str) for dimension in dimensions) else [],
        targets=list(targets) if isinstance(targets, list)
        and all(isinstance(target, str) for target in targets) else None)

def _reject_reason(question: str, task: TaskRequest, session_id, user_id, s: Settings,
                   task_type, dimensions, targets) -> str | None:
    if not isinstance(question, str) or not task.question:
        return "问题为空或格式不正确"
    if len(task.question) > s.max_input_chars:
        return f"问题超过 {s.max_input_chars} 字，请精简后再提交"
    if session_id is not None and (not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id)):
        return "会话 ID 格式不正确"
    if user_id is not None and (not isinstance(user_id, str) or not user_id.strip()):
        return "用户 ID 格式不正确"
    if user_id is not None and len(user_id) > s.max_user_id_chars:
        return "用户 ID 格式不正确"
    if not isinstance(task_type, str) or not task_type:
        return "task_type 格式不正确"
    if task_type not in ("qa", "extract"):
        if get_module(task_type) is None:
            return "不支持的 task_type：" + task_type
    if dimensions is not None and (not isinstance(dimensions, list)
                                   or any(not isinstance(dimension, str) for dimension in dimensions)):
        return "dimensions须为字符串列表"
    if targets is not None:
        if (not isinstance(targets, list) or not 1 <= len(targets) <= 8
                or any(not isinstance(target, str)
                       or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", target)
                       for target in targets)
                or len(set(targets)) != len(targets)):
            return "targets须为 1–8 个不重复的有效 planid 列表"
        # 显式对象可以立即校验，避免为单对象“比较”启动检索或模型调用。
        # 未传 targets 时先留给路由唯一定位，再由业务编排检查定位后的对象数。
        if task_type in ("compare", "compare_plans") and len(targets) < 2:
            return "比较任务至少需要两个不同的 targets"
    return None

async def submit(question: str, session_id: str | None = None,
                 user_id: str | None = None, settings: Settings | None = None,
                 task_type: str = "qa",
                 dimensions: list[str] | None = None,
                 targets: list[str] | None = None) -> RunResult:
    task = _build_task(question, session_id, user_id, task_type, dimensions, targets)
    rt = None
    try:
        try:
            s = settings or load_settings()
        except (ValueError, KeyError) as exc:
            raise ConfigurationError from exc
        rt = Runtime(s, task)
        rt.received()
        message = _reject_reason(question, task, session_id, user_id, s,
                                 task_type, dimensions, targets)
        if message:
            rt.log("rejected", status="failed", error_code="INVALID_INPUT")
            result = build_result(task, None, None, rt.meta,
                                  RunError(code="INVALID_INPUT", message=message))
            result.task_type = task.task_type
            rt.finish(result, None)
            return result
        rt.prepare()
        return await run_task(task, s, rt)
    except Exception as exc:
        if isinstance(exc, ConfigurationError):
            code = "CONFIG_ERROR"
        elif isinstance(exc, SessionForbidden):
            code = "SESSION_FORBIDDEN"
        else:
            code = "INTERNAL"
        result = build_result(task, None, None, rt.meta if rt else {},
                              RunError(code=code, message=ERRORS[code]))
        result.task_type = task.task_type
        if rt:
            try:
                rt.log("rejected", error_code=code,
                       error_detail=f"{type(exc).__name__}: {exc}")
                rt.finish(result, None)
            except OSError:
                pass
        return result
    finally:
        if rt:
            rt.close()
