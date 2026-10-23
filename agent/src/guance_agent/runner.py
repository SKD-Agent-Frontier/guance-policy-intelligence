"""调用 SDK 执行循环并集中映射执行异常。"""
import asyncio
import time
from dataclasses import dataclass

from agents import RunConfig, Runner, ToolExecutionConfig
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from .context import RunContext
from .runtime import Runtime, ToolBudgetExceeded
from .schemas import TaskRequest


@dataclass
class ExecOutcome:
    final_output: object | None
    error_code: str | None
    error_detail: str = ""


async def execute(agent, task: TaskRequest, ctx: RunContext, rt: Runtime,
                  deadline: float | None = None) -> ExecOutcome:
    deadline = deadline if deadline is not None else time.monotonic() + rt.settings.run_timeout
    cm = asyncio.timeout_at(deadline)
    try:
        async with cm:
            result = await Runner.run(agent, task.question, context=ctx, session=rt.session,
                                      max_turns=rt.settings.max_turns, hooks=rt.hooks,
                                      run_config=RunConfig(tracing_disabled=True, tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1)))
        return ExecOutcome(result.final_output, None)
    except asyncio.CancelledError:
        rt.log("run_end", status="failed", error_code="CANCELLED")
        raise
    except Exception as exc:
        # SDK 把钩子异常包成 UserError，原因可能在 __cause__ 或 __context__
        budget = exc if isinstance(exc, ToolBudgetExceeded) else (exc.__cause__ or exc.__context__)
        if isinstance(exc, MaxTurnsExceeded):
            code = "MAX_TURNS"
        elif isinstance(budget, ToolBudgetExceeded):
            code = "TOOL_BUDGET"
        elif isinstance(exc, TimeoutError):
            code = "RUN_TIMEOUT" if cm.expired() else "INTERNAL"
            # ponytail: 本层只能发取消请求，真正的硬上限需部署层子进程兜底。
            if cm.expired() and time.monotonic() - deadline > 0.05:
                rt.log("timeout_overrun", seconds=round(time.perf_counter() - deadline, 3))
        elif isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
            code = "MODEL_UNAVAILABLE"
        elif isinstance(exc, APIStatusError):
            code = ("MODEL_UNAUTHORIZED" if exc.status_code in (401, 403) else
                    "MODEL_NOT_FOUND" if exc.status_code == 404 else
                    "MODEL_UNAVAILABLE" if exc.status_code == 429 or exc.status_code >= 500
                    else "MODEL_BAD_REQUEST")
        elif isinstance(exc, ModelBehaviorError):
            code = "MODEL_BEHAVIOR"
        else:
            code = "INTERNAL"
        if isinstance(exc, MaxTurnsExceeded):
            detail = f"MaxTurnsExceeded: 已执行 {rt.hooks.turns} 轮 / 上限 {rt.settings.max_turns}"
        elif isinstance(budget, ToolBudgetExceeded):
            detail = f"ToolBudgetExceeded: {budget}"[:200]
        elif isinstance(exc, APIStatusError):
            detail = f"{type(exc).__name__}:{exc.status_code}"
        else:
            detail = f"{type(exc).__name__}: {exc}"[:200]
        rt.log("execution_error", error_code=code, error_detail=detail)
        return ExecOutcome(None, code, detail)
