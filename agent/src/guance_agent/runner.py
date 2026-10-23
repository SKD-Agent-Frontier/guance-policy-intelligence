"""调用 SDK 执行循环并集中映射执行异常。"""
import asyncio
from dataclasses import dataclass

from agents import RunConfig, Runner
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from .context import RunContext
from .runtime import Runtime
from .schemas import TaskRequest


@dataclass
class ExecOutcome:
    final_output: object | None
    error_code: str | None
    error_detail: str = ""


async def execute(agent, task: TaskRequest, ctx: RunContext, rt: Runtime) -> ExecOutcome:
    try:
        async with asyncio.timeout(rt.settings.run_timeout):
            result = await Runner.run(agent, task.question, context=ctx, session=rt.session,
                                      max_turns=rt.settings.max_turns, hooks=rt.hooks,
                                      run_config=RunConfig(tracing_disabled=True))
        return ExecOutcome(result.final_output, None)
    except asyncio.CancelledError:
        rt.log("run_end", status="failed", error_code="CANCELLED")
        raise
    except Exception as exc:
        if isinstance(exc, MaxTurnsExceeded):
            code = "MAX_TURNS"
        elif isinstance(exc, TimeoutError):
            code = "RUN_TIMEOUT"
        elif isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
            code = "MODEL_UNAVAILABLE"
        elif isinstance(exc, APIStatusError):
            code = "MODEL_UNAVAILABLE" if exc.status_code == 429 or exc.status_code >= 500 else "MODEL_BAD_REQUEST"
        elif isinstance(exc, ModelBehaviorError):
            code = "MODEL_BEHAVIOR"
        else:
            code = "INTERNAL"
        detail = (f"{type(exc).__name__}: HTTP {exc.status_code}"
                  if isinstance(exc, APIStatusError) else f"{type(exc).__name__}: {exc}")
        rt.log("execution_error", error_code=code, error_detail=detail)
        return ExecOutcome(None, code, detail)
