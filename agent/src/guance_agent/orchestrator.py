"""执行编排：把入口已校验的任务交给执行核心，并组装核验后的结果。

入口只负责接收、校验和转交；模型调用、工具循环、证据上下文与结果装配都在这里完成。
"""
import asyncio
import json
import time

from .agent import build_agent, chat_model
from .client import thinking_off
from .config import ConfigurationError, Settings
from .context import RunContext
from .extract import build_messages, retrieve
from .kb import get_kb
from .output import ERRORS, build_result, insufficient_message
from .routing import decide
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
            max_tokens=s.llm_max_output_tokens, extra_body=thinking_off(s),
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




async def _execute_once(task, s, ctx, rt, deadline):
    """按任务类型选择取证方式。

    extract 是「程序检索 + 单次模型调用」；其余（含 qa）保留 Agent 循环——
    复杂问题需要模型自主多轮检索取证，压成单次会牺牲证据完整性。
    """
    if task.task_type == "extract":
        return await _run_oneshot(task, s, ctx, rt, build_messages)
    agent = build_agent(s, chat_model(s, rt.client))
    return await execute(agent, task, ctx, rt, deadline=deadline)


async def _run_oneshot(task: TaskRequest, s: Settings, ctx: RunContext, rt, builder) -> ExecOutcome:
    """程序检索 + 单次模型调用；多轮往返是延迟主因，这里压到 1 次。"""
    blocks, found = await retrieve(ctx, task.question, s.top_k_default)
    if not blocks:
        return ExecOutcome(json.dumps({"answer": insufficient_message(ctx.tool_calls),
                                       "claims": [], "insufficient": True},
                                      ensure_ascii=False), None)
    messages = builder(task.question, task.dimensions, blocks)
    try:
        reply = await rt.client.chat.completions.create(
            model=s.llm_model, messages=messages, timeout=s.llm_request_timeout,
            max_tokens=s.llm_max_output_tokens, extra_body=thinking_off(s),
            **({} if s.llm_temperature is None else {"temperature": s.llm_temperature}))
        usage = getattr(reply, "usage", None)
        return ExecOutcome(reply.choices[0].message.content, None, telemetry={
            "model_calls": 1, "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "finish_reason": getattr(reply.choices[0], "finish_reason", None)})
    except Exception:                      # noqa: BLE001 - 统一走 MODEL_UNAVAILABLE
        return ExecOutcome(None, "MODEL_UNAVAILABLE")


def _blocked(task, ctx, meta, decision) -> RunResult:
    """路由阶段就确定不能检索：如实说明，不能假装检索过。"""
    reason = {
        "query_too_long": f"检索查询超过 200 字（当前 {len(decision.sub_query)} 字），"
                          "请缩小问题范围或拆成多个问题后再问；本轮没有检索，不是没有相关政策。",
        "empty_query": "问题为空，请补充要研究的具体内容。",
    }[decision.blocked]
    result = build_result(task, None, ctx, meta)
    result.status, result.evidence_status = "completed", "none"
    result.answer = reason
    result.route = {"kind": decision.kind, "blocked": decision.blocked,
                    "sub_query_chars": len(decision.sub_query)}
    return result


def _route_head(decision) -> dict:
    return {"kind": decision.kind, "planids": list(decision.planids),
            "titles": list(decision.titles), "areas": list(decision.areas),
            "fyps": list(decision.fyps), "sub_query": decision.sub_query}


async def _lookup_title(ctx, title, decision, timeout):
    """标题定位：返回 (planid|None, 状态, 候选)。歧义时绝不自选第一条。"""
    finder = getattr(ctx.kb, "find_by_title", None)
    if finder is None:
        return None, "title_lookup_unsupported", []
    try:
        candidates, matched = await asyncio.wait_for(
            asyncio.to_thread(finder, title, area=(decision.areas[0] if decision.areas else None),
                              fyp=(decision.fyps[0] if decision.fyps else None)), timeout)
    except Exception:                       # noqa: BLE001 - 定位失败不冒充未找到
        return None, "title_lookup_failed", []
    if matched == 0:
        return None, "not_found", []
    if matched > 1:
        return None, "ambiguous", [c["planid"] for c in candidates[:8]]
    return candidates[0]["planid"], "ok", []


async def _run_objects(task, s, ctx, rt, decision, deadline):
    """多/单对象：逐个定位、逐个取证；证据按对象隔离，失败逐对象记录。"""
    meta = dict(rt.meta)
    objects = [{"label": f"planid {pid}", "planid": pid, "origin": "planid"} for pid in decision.planids]
    for title in decision.titles:
        planid, status, candidates = await _lookup_title(ctx, title, decision, s.tool_timeout)
        if status == "ok":
            objects.append({"label": title, "planid": planid, "origin": "title"})
        else:
            objects.append({"label": title, "planid": None, "origin": "title",
                            "status": status, "candidates": candidates})
    if any(obj.get("status") == "ambiguous" for obj in objects):
        result = build_result(task, None, ctx, meta)
        result.status, result.evidence_status = "completed", "none"
        result.answer = "标题对应多篇政策，请确认是哪一篇：" + "；".join(
            f"《{o['label']}》候选 planid={o['candidates']}" for o in objects if o.get("status") == "ambiguous")
        result.route = {**_route_head(decision), "objects": objects}
        return result
    runs, claims, refs = [], [], {}
    for obj in objects:
        if not obj.get("planid"):
            continue
        ctx.scope, ctx.object_label = (obj["planid"],), obj["label"]
        try:
            outcome = await _execute_once(task, s, ctx, rt, deadline)
        except Exception as exc:            # noqa: BLE001 - 单个对象失败不拖垮整体
            obj["status"] = f"failed:{type(exc).__name__}"
            continue
        part = build_result(task, outcome, ctx, meta)
        if outcome.telemetry:
            obj["llm"] = outcome.telemetry
        obj["status"] = part.status if part.status != "completed" else "ok"
        obj["error_code"] = part.error.code if part.error else None
        if part.status == "completed" and part.claims:
            obj["claims"] = len(part.claims)
            obj["evidence"] = len(part.evidence_refs)
            claims.extend(part.claims)
            refs.update({r.chunk_id: r for r in part.evidence_refs})
            runs.append((obj, part))
        elif part.status == "completed":
            obj["status"] = "no_evidence"
    ctx.scope = None
    if not runs:
        result = build_result(task, None, ctx, meta)
        result.status, result.evidence_status = "completed", "insufficient"
        result.answer = "以下对象都没有取得可用证据：" + "；".join(
            f"{o['label']}({o.get('status')})" for o in objects)
        result.route = {**_route_head(decision), "objects": objects}
        return result
    result = runs[0][1].model_copy(deep=True)
    result.claims, result.evidence_refs = claims, list(refs.values())
    result.dropped_chunk_ids = []
    result.answer = "\n\n".join(f"【{obj['label']}】\n{part.answer}" for obj, part in runs)
    missing = [o for o in objects if o.get("status") != "ok"]
    if missing:
        result.warnings.append("以下对象未取得证据：" + "；".join(
            f"{o['label']}({o.get('status')})" for o in missing))
    result.route = {**_route_head(decision), "objects": objects}
    return result


async def run_task(task: TaskRequest, s: Settings, rt: Runtime) -> RunResult:
    """先路由，再按单对象/多对象/开放主题取证；结束时统一落运行日志。"""
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
        decision = decide(task.question)
        ctx.route = decision
        async with _session_lock(task.session_id):
            if decision.blocked:
                result = _blocked(task, ctx, rt.meta, decision)
            elif decision.kind == "objects":
                result = await _run_objects(task, s, ctx, rt, decision, deadline)
            else:
                result = await _run_single(task, s, ctx, rt, deadline, decision)
        if result.status == "completed":
            await verify_quotes(result, ctx)
            await verify_numbers(result, ctx)
        _attach(result, task)
    try:
        rt.finish(result, ctx)
    except OSError as exc:
        result.meta["finish_failed"] = type(exc).__name__
    return result


async def _run_single(task, s, ctx, rt, deadline, decision):
    """开放主题：沿用原有行为，只把识别出的地区/时期作为检索约束传下去。"""
    ctx.scope = None
    outcome = await _execute_once(task, s, ctx, rt, deadline)
    result = build_result(task, outcome, ctx, rt.meta)
    if outcome.telemetry:
        result.meta["llm"] = {**outcome.telemetry,
                              "parse": "ok" if result.status == "completed" else "failed"}
    result.route = _route_head(decision)
    return result
