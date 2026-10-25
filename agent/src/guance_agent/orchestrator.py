"""执行编排：把入口已校验的任务交给执行核心，并组装核验后的结果。

入口只负责接收、校验和转交；模型调用、工具循环、证据上下文与结果装配都在这里完成。
"""
import asyncio
import time

from .agent import build_agent, chat_model
from .config import ConfigurationError, Settings
from .context import RunContext
from .kb import get_kb
from .output import ERRORS, build_result
from .runner import execute
from .runtime import Runtime
from .schemas import RunError, RunResult, TaskRequest
from .verify import verify_numbers, verify_quotes

_LOCKS: dict[str, asyncio.Lock] = {}


def _session_lock(session_id: str) -> asyncio.Lock:
    """同一会话的执行回合串行化；不同会话仍可并发。

    ponytail: 只在进程内生效，多进程部署时需换成分布式锁。
    """
    return _LOCKS.setdefault(session_id, asyncio.Lock())


async def run_task(task: TaskRequest, s: Settings, rt: Runtime) -> RunResult:
    """建立检索与证据上下文，调用执行核心，返回结构化结果；结束时统一落运行日志。"""
    deadline = time.monotonic() + s.run_timeout
    if not s.llm_base_url or not s.llm_api_key:
        ctx = None
        result = build_result(task, None, None, rt.meta,
                              RunError(code="MODEL_UNAVAILABLE", message=ERRORS["MODEL_UNAVAILABLE"]))
    else:
        try:
            kb = get_kb(s)
        except (ValueError, KeyError) as exc:
            raise ConfigurationError from exc
        ctx = RunContext(s, kb, task.run_id, task.session_id)
        agent = build_agent(s, chat_model(s, rt.client))
        async with _session_lock(task.session_id):
            outcome = await execute(agent, task, ctx, rt, deadline=deadline)
        result = build_result(task, outcome, ctx, rt.meta)
        # 引文逐字核验：后端没有 verify_quote（mock/sample）时自动跳过
        await verify_quotes(result, ctx)
        await verify_numbers(result, ctx)
    try:
        rt.finish(result, ctx)
    except OSError as exc:
        # ponytail: only the run-log write is best-effort; if finish later owns durable
        # state, let it fail loudly instead.
        result.meta["finish_failed"] = type(exc).__name__
    return result
