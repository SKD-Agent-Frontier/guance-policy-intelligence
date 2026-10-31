"""执行编排：把入口已校验的任务交给执行核心，并组装核验后的结果。

入口只负责接收、校验和转交；模型调用、工具循环、证据上下文与结果装配都在这里完成。
"""
import asyncio
import json
import time
from dataclasses import replace

from .agent import build_agent, chat_model
from .business import get_module, group_by_document
from .business.contract import BusinessError, BusinessOutcome, normalize_structured
from .business.services import BusinessServices
from .client import thinking_off
from .config import ConfigurationError, Settings
from .context import RunContext
from .extract import build_messages, retrieve
from .format_recovery import (
    REPAIRABLE,
    classify_output,
    repair_messages,
    repair_preserves_content,
)
from .kb import get_kb
from .literal_keywords import enforce_literal_result, literal_keyword
from .output import (
    ERRORS,
    LIMIT_CODES,
    build_result,
    finalize_qa_retrieval,
    insufficient_message,
    rebuild_trusted_answer,
)
from .routing import RouteDecision, decide
from .runner import ExecOutcome, execute
from .runtime import Runtime
from .schemas import DIMENSIONS, AgentOutput, RunError, RunResult, TaskRequest
from .verify import verify_numbers, verify_quotes

SUPPORTED_TASKS = ("qa", "extract")   # 只有这两个 task_type 有实现，其余显式拒绝

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
    result.error = None   # 正常结束，不是内部错误
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
        result.error = None   # 正常结束，不是内部错误
        result.answer = "标题对应多篇政策，请确认是哪一篇：" + "；".join(
            f"《{o['label']}》候选 planid={o['candidates']}" for o in objects if o.get("status") == "ambiguous")
        result.route = {**_route_head(decision), "objects": objects}
        return result
    runs, claims, refs = [], [], {}
    parse_entries, repair_budget = [], {"remaining": 1}
    for obj in objects:
        if not obj.get("planid"):
            continue
        # 统一截止时间：剩余预算不足就停，不再开新对象，避免整体超时
        if deadline - time.monotonic() < 5:
            obj["status"] = "skipped_no_budget"
            obj["error_code"] = "RUN_TIMEOUT"
            continue
        ctx.scope, ctx.object_label = (obj["planid"],), obj["label"]
        try:
            outcome = await _execute_once(task, s, ctx, rt, deadline)
            outcome, parse_meta = await _recover_qa_output(
                outcome, s, rt, deadline, repair_budget=repair_budget,
                min_remaining=REPAIR_TIMEOUT_RESERVE_S + REPAIR_OBJECTS_HEADROOM_S)
            if parse_meta:
                parse_entries.append(parse_meta.get("output_parse"))
        except Exception as exc:            # noqa: BLE001 - 单个对象失败不拖垮整体
            obj["status"] = f"failed:{type(exc).__name__}"
            obj["error_code"] = "INTERNAL"
            continue
        part = build_result(task, outcome, ctx, meta)
        if outcome.telemetry:
            obj["llm"] = outcome.telemetry
        if parse_meta:
            part.meta.update(parse_meta)
        # 证据隔离：只保留确实引自本对象的结论，防止跨对象借证据
        own = {cid for cid in (c for claim in part.claims for c in claim.chunk_ids)
               if getattr(ctx.evidence.get(cid), "planid", None) == obj["planid"]}
        part.claims = [claim for claim in part.claims
                       if claim.chunk_ids and all(cid in own for cid in claim.chunk_ids)]
        if not part.claims and part.status == "completed":
            part.status = "completed"
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
    aggregate_meta = _objects_parse_meta([entry for entry in parse_entries if entry])
    if not runs:
        failures = [obj for obj in objects if obj.get("error_code")]
        budget_stops = [obj for obj in objects
                        if obj.get("status") == "skipped_no_budget"
                        or obj.get("error_code") in LIMIT_CODES]
        if budget_stops:
            code = next((obj.get("error_code") for obj in budget_stops
                         if obj.get("error_code") in LIMIT_CODES), "RUN_TIMEOUT")
            result = build_result(task, None, ctx, meta,
                                  RunError(code=code, message=ERRORS[code]))
            if aggregate_meta:
                result.meta.update(aggregate_meta)
            result.route = {**_route_head(decision), "objects": objects}
            return result
        if failures:
            code = failures[0]["error_code"]
            if code not in ERRORS:
                code = "INTERNAL"
            result = build_result(task, None, ctx, meta,
                                  RunError(code=code, message=ERRORS[code]))
            if aggregate_meta:
                result.meta.update(aggregate_meta)
            result.route = {**_route_head(decision), "objects": objects}
            return result
        result = build_result(task, None, ctx, meta)
        result.status, result.evidence_status = "completed", "insufficient"
        result.error = None   # 正常结束，不是内部错误
        result.answer = "以下对象都没有取得可用证据：" + "；".join(
            f"{o['label']}({o.get('status')})" for o in objects)
        if aggregate_meta:
            result.meta.update(aggregate_meta)
        result.route = {**_route_head(decision), "objects": objects}
        return result
    result = runs[0][1].model_copy(deep=True)
    if aggregate_meta:
        result.meta.update(aggregate_meta)
    result.claims, result.evidence_refs = claims, list(refs.values())
    result.dropped_chunk_ids = []
    result.answer = "\n\n".join(f"【{obj['label']}】\n{part.answer}" for obj, part in runs)
    missing = [o for o in objects if o.get("status") != "ok"]
    if missing:
        result.warnings.append("以下对象未取得证据：" + "；".join(
            f"{o['label']}({o.get('status')})" for o in missing))
    llms = [o["llm"] for o in objects if o.get("llm")]
    if llms:
        result.meta["llm"] = {
            "model_calls": sum(x.get("model_calls") or 0 for x in llms),
            "input_tokens": sum(x.get("input_tokens") or 0 for x in llms),
            "output_tokens": sum(x.get("output_tokens") or 0 for x in llms),
            "per_call": [c for x in llms for c in x.get("per_call", [])],
            "parse": "ok" if result.status == "completed" else "failed"}
    result.route = {**_route_head(decision), "objects": objects}
    return result


async def run_task(task: TaskRequest, s: Settings, rt: Runtime) -> RunResult:
    """先路由，再按单对象/多对象/开放主题取证；结束时统一落运行日志。"""
    deadline = time.monotonic() + s.run_timeout
    module = None if task.task_type in SUPPORTED_TASKS else get_module(task.task_type)
    if task.task_type not in SUPPORTED_TASKS and module is None:
        result = build_result(task, None, None, rt.meta, RunError(
            code="INVALID_INPUT", message="不支持的 task_type：" + task.task_type))
        result.task_type = task.task_type
        rt.finish(result, None)
        return result
    if not s.llm_base_url or not s.llm_api_key:
        ctx = None
        result = build_result(task, None, None, rt.meta,
                              RunError(code="MODEL_UNAVAILABLE", message=ERRORS["MODEL_UNAVAILABLE"]))
    else:
        try:
            kb = get_kb(s)
        except (ValueError, KeyError) as exc:
            raise ConfigurationError from exc
        ctx = RunContext(s, kb, task.run_id, task.session_id, question=task.question)
        decision = decide(task.question)
        if task.targets is not None:
            decision = RouteDecision(kind="objects", planids=tuple(task.targets),
                                     areas=decision.areas, fyps=decision.fyps,
                                     sub_query=decision.sub_query, blocked=decision.blocked)
        ctx.route = decision
        if task.task_type == "qa" and decision.kind == "open":
            ctx.literal_keyword = literal_keyword(task.question)
        async with _session_lock(task.session_id):
            if module is not None:
                result = await _run_business(task, s, rt, ctx, deadline, decision, module)
            elif decision.blocked:
                result = _blocked(task, ctx, rt.meta, decision)
            elif decision.kind == "objects":
                result = await _run_objects(task, s, ctx, rt, decision, deadline)
            else:
                result = await _run_single(task, s, ctx, rt, deadline, decision)
        result.task_type = task.task_type
        if result.status == "completed" and module is None:
            # 核验也不能突破整轮运行时限：超时则降级并如实说明，不拖垮请求。
            try:
                await asyncio.wait_for(verify_quotes(result, ctx),
                                       timeout=max(deadline - time.monotonic(), 0.05))
                await asyncio.wait_for(verify_numbers(result, ctx),
                                       timeout=max(deadline - time.monotonic(), 0.05))
            except TimeoutError:
                result.warnings.append("核验超出本次运行时限，未完成逐条核验")
                result.evidence_status = "insufficient"
        if module is None:
            if task.task_type == "qa":
                enforce_literal_result(result, ctx)
            _attach(result, task)
    result.task_type = task.task_type
    result = finalize_qa_retrieval(result, task)
    try:
        rt.finish(result, ctx)
    except OSError as exc:
        result.meta["finish_failed"] = type(exc).__name__
    return result


def _business_prompt(task, ctx, meta, decision, answer, targets=()) -> RunResult:
    output = AgentOutput(answer=answer, claims=[], insufficient=True)
    result = build_result(task, ExecOutcome(output, None), ctx, meta)
    result.status, result.evidence_status, result.error = "completed", "none", None
    result.task_type = task.task_type
    result.route = {**_route_head(decision),
                    "targets": None if task.targets is None and decision.kind == "open" and not targets
                    else list(targets)}
    return result


async def _business_targets(task, s, ctx, decision, deadline, module):
    """确定业务的完整对象集合；任一标题有歧义时不执行部分比较。

    显式 targets 优先。否则逐标题唯一定位，再一次性交给业务模块。逐标题定位
    只为识别身份，和把业务按单对象重复执行后拼接结果是两种不同的操作。
    """
    if task.targets is not None:
        return list(task.targets), None
    requires_targets = getattr(module, "requires_targets", True) is not False
    if decision.kind == "open":
        if decision.blocked == "empty_query":
            return [], "问题为空，请补充要研究的具体内容。"
        if len(decision.areas) > 1 or len(decision.fyps) > 1:
            return [], "当前检索接口不能同时限定多个地区或多个规划期，请缩小范围后重试。"
        if not requires_targets:
            return None, None
    if decision.blocked:
        return [], "请缩小问题范围后重试；当前无法确定业务分析对象。"
    if decision.kind != "objects":
        return [], "请提供明确的政策 planid 或唯一政策标题后重试。"
    title_decision = decision
    if decision.titles and (len(decision.areas) > 1 or len(decision.fyps) > 1):
        if task.task_type not in ("compare", "compare_plans"):
            return [], "标题定位不能同时限定多个地区或多个规划期，请缩小范围后重试。"
        # 比较中的不同标题可分属不同地区/时期。为身份查找去掉冲突的单值过滤，
        # 用完整标题分别唯一定位；仍然有歧义就要求补充对象，不取首个候选。
        # 这里不修改原 decision，也不扩大后续已确定 targets 的正文读取范围。
        title_decision = RouteDecision(kind=decision.kind, titles=decision.titles,
                                       sub_query=decision.sub_query)
    targets = list(decision.planids)
    unresolved = []
    for title in decision.titles:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        pid, status, candidates = await _lookup_title(
            ctx, title, title_decision, min(s.tool_timeout, remaining))
        if status == "ok":
            targets.append(pid)
        else:
            unresolved.append({"title": title, "status": status,
                               "candidates": candidates})
    if time.monotonic() >= deadline:
        raise TimeoutError
    if unresolved:
        return [], "有政策标题无法唯一定位，请补充 planid 或消除歧义：" + json.dumps(
            unresolved, ensure_ascii=False)
    return list(dict.fromkeys(targets)), None


async def _run_business(task, s, rt, ctx, deadline, decision, module):
    """一次调用业务模块，并统一检查结果形状、引文、数字与降级一致性。"""
    try:
        targets, message = await _business_targets(task, s, ctx, decision, deadline, module)
    except TimeoutError:
        return _business_failure(task, ctx, rt, decision, "RUN_TIMEOUT")
    if time.monotonic() >= deadline:
        return _business_failure(task, ctx, rt, decision, "RUN_TIMEOUT")
    if task.task_type in ("compare", "compare_plans") and task.targets is None and decision.kind == "open":
        return _business_prompt(task, ctx, rt.meta, decision,
                                "比较任务至少需要两个不同的政策对象。", [])
    if message:
        return _business_prompt(task, ctx, rt.meta, decision, message, targets)
    if targets == []:
        return _business_prompt(task, ctx, rt.meta, decision,
                                "请提供至少一个明确政策对象。", targets)
    if targets is not None and len(targets) > 8:
        return _business_failure(task, ctx, rt, decision, "INVALID_INPUT",
                                 targets=targets,
                                 message="业务任务最多支持 8 个政策对象，请缩小对象范围。")
    if task.task_type in ("compare", "compare_plans") and targets is not None and len(targets) < 2:
        return _business_prompt(task, ctx, rt.meta, decision,
                                "比较任务至少需要两个不同的政策对象。", targets)

    # 冻结完整对象范围并构造本次有效任务；共享服务据此约束所有 search/read。
    # 比较模块只调用一次，不能在这里按对象循环后把独立结果拼成矩阵。
    ctx.scope = tuple(targets) if targets is not None else None
    effective_task = task.model_copy(update={"targets": targets})
    services = BusinessServices(ctx, rt, deadline)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        outcome = await asyncio.wait_for(module(effective_task, ctx, services), timeout=remaining)
        if not isinstance(outcome, BusinessOutcome):
            raise TypeError("业务模块必须返回 BusinessOutcome")
        try:
            outcome.structured = normalize_structured(outcome.structured)
        except (TypeError, ValueError) as exc:
            raise BusinessError("OUTPUT_PARSE") from exc
    except BusinessError as exc:
        code = exc.code if exc.code in ERRORS else "INTERNAL"
        return _business_failure(task, ctx, rt, decision, code, services, targets)
    except TimeoutError:
        return _business_failure(task, ctx, rt, decision, "RUN_TIMEOUT", services, targets)
    except Exception as exc:  # noqa: BLE001 - 业务模块失败统一映射为 INTERNAL
        rt.log("business_error", task_type=task.task_type,
               error_detail=f"{type(exc).__name__}: {exc}")
        return _business_failure(task, ctx, rt, decision, "INTERNAL", services, targets)

    # 保存业务事实及来源的快照。structured 可能包含依赖它们的矩阵/分析，
    # 不能在核验删改事实后继续原样展示这些派生内容。
    original_claims = [(claim.text, tuple(claim.chunk_ids)) for claim in outcome.claims]
    output = AgentOutput(answer=outcome.answer, claims=outcome.claims,
                         insufficient=outcome.insufficient)
    result = build_result(effective_task, ExecOutcome(output, None), ctx, rt.meta)
    result.task_type = task.task_type
    result.structured = outcome.structured
    result.route = {**_route_head(decision),
                    "targets": list(targets) if targets is not None else None}
    if isinstance(services.telemetry, dict) and services.telemetry:
        result.meta["business_telemetry"] = dict(services.telemetry)
    if not result.claims and outcome.insufficient:
        result.evidence_status = "insufficient" if ctx.tool_calls else "none"

    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        await asyncio.wait_for(verify_quotes(result, ctx), timeout=remaining)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        await asyncio.wait_for(verify_numbers(result, ctx), timeout=remaining)
    except TimeoutError:
        return _business_failure(task, ctx, rt, decision, "RUN_TIMEOUT", services, targets)

    final_claims = [(claim.text, tuple(claim.chunk_ids)) for claim in result.claims]
    # 当前共享契约采用整份 structured 清空的保守策略；引句空白回贴本身不是
    # 事实正文或来源变化，不单独触发此条件。业务模块不得绕过清空后重放旧矩阵。
    if final_claims != original_claims or result.meta.get("answer_rebuilt"):
        result.structured = {}
        result.warnings.append("部分结论或引用未通过统一核验，已清空业务结构化结果")
    # 没有有效正文事实时只允许保留本轮真实发现的身份元数据，不保留未经核验的
    # 业务回答。显式 targets 只是输入，不是自动获得信任的检索证据。
    if not result.claims or not result.evidence_refs:
        if result.claims and not result.evidence_refs:
            result.claims = []
            result.structured = {}
        elif not original_claims and not result.meta.get("answer_rebuilt"):
            result.structured = _business_document_metadata(services)
        rebuild_trusted_answer(result, ctx, "no_valid_claims")
        result.warnings.append("未形成可核验的有效结论；原始回答和业务事实已移除，正文事实未经核验")
    return result


def _business_document_metadata(services):
    hits = services._discovery_hits
    if not hits:
        return {}
    documents = group_by_document(hits)
    if not documents:
        return {}
    return {
        "documents": [document.model_dump(mode="json") for document in documents],
        "verification": {"policy_facts": "unverified", "reason": "no_valid_claims"},
    }


def _business_failure(task, ctx, rt, decision, code, services=None, targets=None, message=None):
    if code not in ERRORS:
        code = "INTERNAL"
    if targets is None and task.targets is not None:
        targets = task.targets
    result = build_result(task, None, ctx, rt.meta,
                          RunError(code=code, message=message or ERRORS[code]))
    result.task_type = task.task_type
    result.structured = {}
    result.route = {**_route_head(decision),
                    "targets": list(targets) if targets is not None else None}
    if services is not None and isinstance(services.telemetry, dict) and services.telemetry:
        result.meta["business_telemetry"] = dict(services.telemetry)
    return result


REPAIR_MIN_TIMEOUT_S = 1.0       # 剩余时间不够一次可用的修复调用时不做
REPAIR_TIMEOUT_RESERVE_S = 5.0   # 修复后给核验与收尾保留的时间
REPAIR_OBJECTS_HEADROOM_S = 6.0  # 多对象路径：修复前要给后续对象留出预算


def _objects_parse_meta(entries):
    """把多对象路径的解析记录聚合成一份 meta，避免只保留第一个对象。"""
    if not entries:
        return None
    failed = [entry.get("classification") for entry in entries
              if entry.get("classification") not in (None, "ok")]
    merged = {"classification": failed[0] if failed else "ok", "objects": entries}
    if any(entry.get("repair") for entry in entries):
        merged["repair"] = "attempted"
    if any(entry.get("repair_rejected") for entry in entries):
        merged["repair_rejected"] = "content_changed"
    if any(entry.get("deterministic_repair") for entry in entries):
        merged["deterministic_repair"] = True
    return {"output_parse": merged}


async def _recover_qa_output(outcome, s, rt, deadline, *, repair_budget=None,
                             min_remaining=None):
    """QA 输出格式恢复：确定性修复优先，必要时做一次受预算约束的格式修复调用。

    修复调用只带模型原文，不重新检索、不携带工具；多个 JSON 对象、截断和非 JSON
    一律失败关闭。修复结果仍要经过原有的证据登记与核验，不能因此获得可信度。
    repair_budget 为整轮运行共享的修复次数预算（{"remaining": n}），多对象路径
    用它保证一次运行最多只修复一次。
    """
    if outcome.error_code is not None or not isinstance(outcome.final_output, str):
        return outcome, None
    if s.structured_output:
        return outcome, None
    classification, parsed, deterministic = classify_output(outcome.final_output)
    meta = {"output_parse": {"classification": classification}}
    if parsed is not None:
        if deterministic:
            meta["output_parse"]["deterministic_repair"] = deterministic
            rt.log("qa_output_parse_repaired", classification=classification,
                   method=deterministic)
            return replace(outcome, final_output=parsed), meta
        return outcome, meta
    if classification not in REPAIRABLE or len(outcome.final_output) > s.max_input_chars:
        meta["output_parse"]["repair"] = "not_attempted"
        rt.log("qa_output_parse_failed", classification=classification, repair="not_attempted")
        return outcome, meta
    remaining = deadline - time.monotonic()
    timeout = min(s.llm_request_timeout, remaining - REPAIR_TIMEOUT_RESERVE_S)
    budget = repair_budget if repair_budget is not None else {"remaining": 1}
    floor = REPAIR_TIMEOUT_RESERVE_S + 1.0 if min_remaining is None else min_remaining
    if (timeout < REPAIR_MIN_TIMEOUT_S or rt.hooks.turns + 1 > s.max_turns
            or budget.get("remaining", 0) <= 0 or remaining < floor):
        meta["output_parse"]["repair"] = "skipped_budget"
        rt.log("qa_output_parse_failed", classification=classification, repair="skipped_budget")
        return outcome, meta
    budget["remaining"] = budget.get("remaining", 0) - 1
    try:
        repaired_raw = await asyncio.wait_for(
            rt.complete_text(repair_messages(outcome.final_output, s.max_input_chars),
                             max_tokens=s.llm_max_output_tokens, timeout=timeout),
            timeout=timeout + REPAIR_TIMEOUT_RESERVE_S)
    except Exception as exc:  # noqa: BLE001 - 修复调用失败不改变原失败结果
        meta["output_parse"].update(repair="failed", repair_error=type(exc).__name__)
        rt.log("qa_output_repair_failed", classification=classification,
               error=type(exc).__name__)
        return outcome, meta
    repaired_classification, repaired, _ = classify_output(repaired_raw)
    meta["output_parse"].update(repair="attempted", repair_result=repaired_classification,
                                model_calls=1)
    if repaired is None:
        rt.log("qa_output_repair_failed", classification=classification,
               result=repaired_classification)
        return outcome, meta
    if not repair_preserves_content(outcome.final_output, repaired):
        # 修复只能改格式：内容被改写或出现原输出里没有的依据时，整次修复作废。
        meta["output_parse"]["repair_rejected"] = "content_changed"
        rt.log("qa_output_repair_failed", classification=classification,
               result="content_changed")
        return outcome, meta
    rt.log("qa_output_parse_repaired", classification=classification, method="model")
    return replace(outcome, final_output=repaired), meta


async def _run_single(task, s, ctx, rt, deadline, decision):
    """开放主题：沿用原有行为，只把识别出的地区/时期作为检索约束传下去。"""
    ctx.scope = None
    outcome = await _execute_once(task, s, ctx, rt, deadline)
    outcome, parse_meta = await _recover_qa_output(outcome, s, rt, deadline,
                                                  repair_budget={"remaining": 1})
    result = build_result(task, outcome, ctx, rt.meta)
    if parse_meta:
        result.meta.update(parse_meta)
    if outcome.telemetry:
        result.meta["llm"] = {**outcome.telemetry,
                              "parse": "ok" if result.status == "completed" else "failed"}
    result.route = _route_head(decision)
    return result
