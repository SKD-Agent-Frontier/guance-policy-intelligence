"""执行编排：把入口已校验的任务交给执行核心，并组装核验后的结果。

入口只负责接收、校验和转交；模型调用、工具循环、证据上下文与结果装配都在这里完成。
"""
import asyncio
import json
import time

from .agent import build_agent, chat_model
from .config import ConfigurationError, Settings
from .context import RunContext
from .extract import build_messages, retrieve
from .kb import get_kb
from .output import ERRORS, build_result, insufficient_message
from .runner import ExecOutcome, execute
from .runtime import Runtime
from .schemas import DIMENSIONS, RunError, RunResult, TaskRequest
from .verify import verify_numbers, verify_quotes

_LOCKS: dict[str, asyncio.Lock] = {}


def _session_lock(session_id: str) -> asyncio.Lock:
    """同一会话的执行回合串行化；不同会话仍可并发。

    ponytail: 只在进程内生效，多进程部署时需换成分布式锁。
    """
    return _LOCKS.setdefault(session_id, asyncio.Lock())



async def _run_extract(task: TaskRequest, s: Settings, ctx: RunContext, rt) -> ExecOutcome:
    """一次程序检索 + 一次模型调用；不进入 Agent 工具循环。"""
    blocks, found = await retrieve(ctx, task.question, s.top_k_default)
    if not blocks:
        # 检索不到不是错误，而是一次「证据不足」的结果：走结构化路径给出可读说明，
        # 不能编一个不存在的错误码（那样会被兜底成 INTERNAL）。
        return ExecOutcome(json.dumps({"answer": insufficient_message(ctx.tool_calls),
                                       "claims": [], "insufficient": True},
                                      ensure_ascii=False), None)
    messages = build_messages(task.question, task.dimensions, blocks)
    try:
        reply = await rt.client.chat.completions.create(
            model=s.llm_model, messages=messages, timeout=s.llm_request_timeout,
            **({} if s.llm_temperature is None else {"temperature": s.llm_temperature}))
        return ExecOutcome(reply.choices[0].message.content, None)
    except Exception:                      # noqa: BLE001 - 统一走 MODEL_UNAVAILABLE
        return ExecOutcome(None, "MODEL_UNAVAILABLE")


def _documents(result) -> list[dict]:
    """本次引用到的篇目，按 planid 去重。"""
    seen = {}
    for ref in result.evidence_refs:
        seen.setdefault(ref.planid, {"planid": ref.planid, "title": ref.title,
                                     "area": ref.area, "fyp": ref.fyp})
    return list(seen.values())


def _missing_dimensions(result, task: TaskRequest) -> list[dict]:
    """没抽到的维度：读取成功过就算「该篇没有」，一次都没读成才算「没检索到」。

    这个区分是政策研究的基本要求——把「没有政策」写成「没查到」会误导判断。
    """
    dimensions = task.dimensions or list(DIMENSIONS)
    covered = {claim.dimension for claim in result.claims if claim.dimension}
    missing = [name for name in dimensions if name not in covered]
    if not missing:
        return []
    statuses = {call.get("status") for call in result.meta.get("tool_calls") or []}
    reason = "not_in_document" if "ok" in statuses else "not_retrieved"
    return [{"dimension": name, "reason": reason} for name in missing]


def _attach(result, task: TaskRequest) -> RunResult:
    """把功能层产物挂到结果上；qa 任务保持原样。"""
    result.task_type = task.task_type
    if task.task_type == "extract":
        result.structured = {
            "dimensions": task.dimensions or list(DIMENSIONS),
            "documents": _documents(result),
            "elements": [claim.model_dump() for claim in result.claims],
            "missing": _missing_dimensions(result, task),
        }
    return result



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
        async with _session_lock(task.session_id):
            if task.task_type == "extract":
                # 程序检索 + 一次模型调用；多轮往返在 180 秒预算内必然超时
                outcome = await _run_extract(task, s, ctx, rt)
            else:
                agent = build_agent(s, chat_model(s, rt.client))
                outcome = await execute(agent, task, ctx, rt, deadline=deadline)
        result = build_result(task, outcome, ctx, rt.meta)
        # 引文与数字核验：后端没有对应能力（mock/sample）时自动跳过
        await verify_quotes(result, ctx)
        await verify_numbers(result, ctx)
        _attach(result, task)
    try:
        rt.finish(result, ctx)
    except OSError as exc:
        # ponytail: only the run-log write is best-effort; if finish later owns durable
        # state, let it fail loudly instead.
        result.meta["finish_failed"] = type(exc).__name__
    return result
