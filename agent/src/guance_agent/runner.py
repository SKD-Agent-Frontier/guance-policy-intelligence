"""调用 SDK 执行循环并集中映射执行异常。"""
import asyncio
import inspect
import time
from dataclasses import dataclass, replace

from agents import FunctionTool, RunConfig, RunHooks, Runner, ToolExecutionConfig
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


class _TurnReserveHooks(RunHooks):
    """包装运行钩子，转发遥测并标识 SDK 工具校验阶段。"""

    def __init__(self, delegate, state):
        self.delegate, self.state = delegate, state

    async def on_llm_start(self, *args, **kwargs):
        await self.delegate.on_llm_start(*args, **kwargs)

    async def on_llm_end(self, *args, **kwargs):
        await self.delegate.on_llm_end(*args, **kwargs)
        self.state["dispatch_pending"] = True

    async def on_tool_start(self, *args, **kwargs):
        self.state["dispatch_pending"] = False
        await self.delegate.on_tool_start(*args, **kwargs)

    async def on_tool_end(self, *args, **kwargs):
        await self.delegate.on_tool_end(*args, **kwargs)


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
            max_turns = rt.settings.max_turns
            run_agent = agent
            hooks = rt.hooks
            run_config = RunConfig(
                tracing_disabled=True,
                tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
            )
            if task.task_type == "qa" and max_turns >= 2:
                model_calls = 0
                state = {"dispatch_pending": False}
                tools = []
                for tool in agent.tools:
                    if not isinstance(tool, FunctionTool):
                        tools.append(tool)
                        continue
                    original_is_enabled = tool.is_enabled

                    async def can_use_tool(run_context, tool_agent, *, original=original_is_enabled):
                        if (model_calls >= max_turns - 1
                                and not state["dispatch_pending"]):
                            return False
                        if isinstance(original, bool):
                            return original
                        enabled = original(run_context, tool_agent)
                        if inspect.isawaitable(enabled):
                            enabled = await enabled
                        return bool(enabled)

                    tools.append(replace(tool, is_enabled=can_use_tool))
                run_agent = agent.clone(tools=tools)

                def reserve_final_turn(filter_data):
                    nonlocal model_calls
                    model_calls += 1
                    model_data = filter_data.model_data
                    if model_calls < max_turns:
                        return model_data
                    rt.meta["qa_final_turn_reserved"] = True
                    notice = (
                        "最终回答轮次：只能依据本轮已获得的证据回答；若证据不足，明确说明不足，"
                        "不得编造事实。严格保持既定输出格式（包括 JSON 格式）；不要调用工具。"
                    )
                    instructions = f"{model_data.instructions or ''}\n\n{notice}"
                    return replace(model_data, instructions=instructions)

                run_config.call_model_input_filter = reserve_final_turn
                hooks = _TurnReserveHooks(rt.hooks, state)

            result = await Runner.run(run_agent, task.question, context=ctx, session=rt.session,
                                      max_turns=max_turns, hooks=hooks, run_config=run_config)
        return ExecOutcome(result.final_output, None, telemetry=usage_telemetry(result))
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
        elif isinstance(exc, APITimeoutError):
            code = "RUN_TIMEOUT" if cm.expired() else "MODEL_TIMEOUT"
        elif isinstance(exc, (APIConnectionError, RateLimitError)):
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
