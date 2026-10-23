"""执行编排：把入口已校验的任务交给执行核心，并组装核验后的结果。

入口只负责接收、校验和转交；模型调用、工具循环、证据上下文与结果装配都在这里完成。
"""
import time

from .agent import build_agent, chat_model
from .config import Settings
from .context import RunContext
from .kb import get_kb
from .output import ERRORS, build_result
from .runner import execute
from .runtime import Runtime
from .schemas import RunError, RunResult, TaskRequest


async def run_task(task: TaskRequest, s: Settings, rt: Runtime) -> RunResult:
    """建立检索与证据上下文，调用执行核心，返回结构化结果；结束时统一落运行日志。"""
    deadline = time.monotonic() + s.run_timeout
    ctx = None
    if not s.llm_base_url or not s.llm_api_key:
        result = build_result(
            task, None, None, rt.meta,
            RunError(code="MODEL_UNAVAILABLE", message=ERRORS["MODEL_UNAVAILABLE"]))
    else:
        ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
        agent = build_agent(s, chat_model(s, rt.client))
        outcome = await execute(agent, task, ctx, rt, deadline=deadline)
        result = build_result(task, outcome, ctx, rt.meta)
    try:
        rt.finish(result, ctx)
    except OSError as exc:
        # ponytail: only the run-log write is best-effort; if finish later owns durable
        # state, let it fail loudly instead.
        result.meta["finish_failed"] = type(exc).__name__
    return result
