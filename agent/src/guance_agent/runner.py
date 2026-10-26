"""调用 SDK 执行循环并集中映射执行异常。"""
import asyncio
import time
from dataclasses import dataclass

from agents import RunConfig, Runner, ToolExecutionConfig
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from .context import RunContext, sanitize_error_detail
from .runtime import Runtime, ToolBudgetExceeded
from .schemas import TaskRequest


def find_cause(exc, kind, depth: int = 8):
    """沿 __cause__ / __context__ 查找指定类型的异常。

    SDK 会把钩子异常包成 UserError，包装层数不保证只有一层；
    限制深度可防止异常链成环时死循环。
    """
    for _ in range(depth):
        if isinstance(exc, kind):
            return exc
        exc = exc.__cause__ or exc.__context__
        if exc is None:
            break
    return None


@dataclass
class ExecOutcome:
    final_output: object | None
    error_code: str | None
    error_detail: str = ""
    telemetry: dict | None = None   # 每次模型调用的 token / 次数，供事后定位慢与截断


def usage_telemetry(result) -> dict:
    """只记录 SDK 真正拿得到的东西：调用次数与各次 token 用量。

    耗时由 LogHooks 逐轮记录；finish_reason 与重试次数 SDK 未暴露，这里不臆造。
    """
    per_call = []
    for response in getattr(result, "raw_responses", None) or []:
        usage = getattr(response, "usage", None)
        per_call.append({"input": getattr(usage, "input_tokens", None),
                         "output": getattr(usage, "output_tokens", None)})
    return {"model_calls": len(per_call),
            "input_tokens": sum(c["input"] or 0 for c in per_call),
            "output_tokens": sum(c["output"] or 0 for c in per_call),
            "per_call": per_call}


async def execute(agent, task: TaskRequest, ctx: RunContext, rt: Runtime, deadline: float | None = None) -> ExecOutcome:
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
        budget = find_cause(exc, ToolBudgetExceeded)
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
            raw_detail = f"MaxTurnsExceeded: 已执行 {rt.hooks.turns} 轮 / 上限 {rt.settings.max_turns}"
        elif isinstance(budget, ToolBudgetExceeded):
            raw_detail = f"ToolBudgetExceeded: {budget}"
        elif isinstance(exc, APIStatusError):
            raw_detail = f"{type(exc).__name__}: HTTP {exc.status_code}"
        else:
            raw_detail = f"{type(exc).__name__}: {exc}"
        detail = sanitize_error_detail(raw_detail, rt.settings)
        rt.log("execution_error", error_code=code, error_detail=detail)
        return ExecOutcome(None, code, detail)
