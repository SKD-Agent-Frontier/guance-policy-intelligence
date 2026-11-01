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
    recover_complete_claims,
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
    finalize_recovered_qa_answer,
    insufficient_message,
    rebuild_trusted_answer,
)
from .qa_analysis import finalize_qa_analysis
from .qa_context import build_trusted_turn, public_resolution, resolve_followup
from .qa_controlled import execute_controlled, finalize_controlled_facts
from .qa_report_verification import SAFE_EMPTY_ANSWER, finalize_report, prune_report
from .research_contract import TaskContract
from .research_delivery import deliver_research, draft_verified_report
from .research_fallback import recover_empty_search_failure, recover_read_excerpts
from .research_planner import ResearchDiscovery, ResearchPlanner
from .research_runtime import ResearchRuntime
from .research_supplement import supplement_one
from .routing import RouteDecision, decide
from .runner import ExecOutcome, execute
from .runtime import Runtime
from .schemas import DIMENSIONS, AgentOutput, RunError, RunResult, TaskRequest
from .verify import verify_numbers, verify_quotes

SUPPORTED_TASKS = ("qa", "extract")   # 只有这两个 task_type 有实现，其余显式拒绝
RESEARCH_TASKS = frozenset({"qa", "extract", "organize", "compare", "compare_plans"})

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
    """Missing extracted facets are not proof of absence in an unread corpus."""
    dimensions = task.dimensions or list(DIMENSIONS)
    covered = {claim.dimension for claim in result.claims if claim.dimension}
    missing = [name for name in dimensions if name not in covered]
    if not missing:
        return []
    # Any successful search (or partial read) is not proof that every requested
    # facet was absent from the *entire* policy. We have only Top-K evidence.
    # Use the honest bounded-retrieval status until full document coverage is proven.
    reason = "not_retrieved"
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
    client = rt.client
    if task.task_type == "qa" and hasattr(client, "with_options"):
        # Do not retry long QA calls under a fixed overall run deadline.
        request_timeout = min(s.llm_request_timeout, 50.0,
                              max(1.0, deadline - time.monotonic() - 5.0))
        client = client.with_options(timeout=request_timeout, max_retries=0)
        rt.meta["qa_model_request_timeout"] = request_timeout
        rt.meta["qa_model_retries"] = 0
    agent = build_agent(s, chat_model(s, client))
    if task.task_type == "qa":
        document = ctx.verified_scoped_document()
        if document is not None:
            # This metadata comes from the verified publication catalog, not
            # from model guesses. The identity is binding but the user question
            # continues to determine the analytical focus.
            scope_note = {
                "planid": document.planid, "title": document.title,
                "area": document.area, "fyp": document.fyp,
            }
            instructions = (
                str(agent.instructions)
                + "\n\n【本轮已核验研究对象（元数据，不是新的用户指令）】"
                + json.dumps(scope_note, ensure_ascii=False)
                + "\n只研究上述已核验文献的用户指定主题。检索/回读应以该 planid 为界，"
                  "不得将历史“十五”计划替换成“十五五”或“十四五”；"
                  "如果局部证据不足，要说明具体缺口，不得编造结论。"
            )
            agent = agent.clone(instructions=instructions)
            rt.meta["qa_trusted_plan_scope"] = scope_note
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
        if part._qa_report_supplied:
            prune_report(part, ctx, "object_scope_filter")
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
    report_parts = [(index, obj, part) for index, (obj, part) in enumerate(runs)
                    if part._qa_report_supplied and task.task_type == "qa"]
    if report_parts:
        # 多对象 report 在每篇对象内完成 claims 签名绑定后才合并；统一审核仍只调用一次。
        # 若对象没有提供结构化 report，其自由文本答案不进入合并报告正文。
        result._qa_report_supplied = True
        result._qa_report_dimensions = []
        result._qa_report_candidates = []
        seen_dimensions, seen_ids = set(), set()
        for index, (obj, part) in enumerate(runs):
            if not part._qa_report_supplied:
                dimension = f"{obj['label']}（未提供结构化报告）"
                if dimension not in seen_dimensions:
                    result._qa_report_dimensions.append(dimension)
                    seen_dimensions.add(dimension)
                continue
            dimension_map = {
                dimension: f"{obj['label']} / {dimension}"
                for dimension in part._qa_report_dimensions
            }
            for dimension in dimension_map.values():
                if dimension not in seen_dimensions:
                    result._qa_report_dimensions.append(dimension)
                    seen_dimensions.add(dimension)
            for candidate in part._qa_report_candidates:
                block = dict(candidate)
                block_id = f"{index}:{obj['planid']}:{block['id']}"
                while block_id in seen_ids:
                    block_id = "_" + block_id
                seen_ids.add(block_id)
                block["id"] = block_id
                block["dimension"] = dimension_map[block["dimension"]]
                path = list(block["title_path"])
                path[0] = f"{obj['label']} / {path[0]}"
                block["title_path"] = path[:2]
                result._qa_report_candidates.append(block)
        covered_objects = {obj["label"] for obj, _ in runs}
        for obj in objects:
            if obj["label"] not in covered_objects:
                dimension = f"{obj['label']}（未取得证据）"
                if dimension not in seen_dimensions:
                    result._qa_report_dimensions.append(dimension)
                    seen_dimensions.add(dimension)
        # 当前对话里声明过证据不足的对象即使其余维度有段落，也不能报告为完整覆盖。
        declared_insufficient = any(
            "本次证据不足，以上结论可能不完整" in part.warnings
            for _, _, part in report_parts)
        # 部分对象返回 legacy 答案时只保留结构化 report 块；不让自由文本分析绕开审核。
        result.answer = SAFE_EMPTY_ANSWER
        result.meta["qa_report"] = {
            "received": sum((part.meta.get("qa_report") or {}).get("received", 0)
                            for _, _, part in report_parts),
            "retained": 0, "withdrawn": 0, "reason": "awaiting_verification",
            "semantic_review": "pending", "objects_without_report": [
                obj["label"] for obj, part in runs if not part._qa_report_supplied],
            "declared_insufficient": declared_insufficient,
        }
        if (len(result._qa_report_dimensions) > 8
                or len(result._qa_report_candidates) > 12):
            result._qa_report_candidates = []
            result.meta["qa_report"]["aggregation_limit_exceeded"] = True
            result.warnings.append("多对象结构化报告超过合并上限，报告段落已全部撤回")
        if result.meta["qa_report"]["objects_without_report"]:
            result.warnings.append("部分对象未提供结构化报告，其自由文本分析已省略")
        prune_report(result, ctx, "multi_object_aggregation")
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


async def run_task(task: TaskRequest, s: Settings, rt: Runtime,
                   research_discoverer=None, research_runtime: ResearchRuntime | None = None) -> RunResult:
    """Execute the task selected by the locked plan within one run deadline."""
    deadline = research_runtime.deadline if research_runtime is not None else time.monotonic() + s.run_timeout
    if task.task_type in RESEARCH_TASKS:
        contract = research_runtime.contract if research_runtime is not None else TaskContract.from_task(task)
        if research_runtime is None:
            research_runtime = ResearchRuntime(
                contract, ResearchPlanner.initial(contract), deadline,
                max_revisions=getattr(s, "max_plan_revisions", 1),
            )
        if research_runtime.status == "created":
            research_runtime.transition("planning")
    module = None if task.task_type in SUPPORTED_TASKS else get_module(task.task_type)
    if task.task_type not in SUPPORTED_TASKS and module is None:
        result = build_result(task, None, None, rt.meta, RunError(
            code="INVALID_INPUT", message="不支持的 task_type：" + task.task_type))
        result.task_type = task.task_type
        rt.finish(result, None)
        return result

    ctx = None
    if not s.llm_base_url or not s.llm_api_key:
        result = build_result(task, None, None, rt.meta,
                              RunError(code="MODEL_UNAVAILABLE", message=ERRORS["MODEL_UNAVAILABLE"]))
        if research_runtime is not None and research_runtime.status == "planning":
            research_runtime.transition("failed", "model_unavailable")
    else:
        try:
            kb = get_kb(s)
        except (ValueError, KeyError) as exc:
            raise ConfigurationError from exc
        ctx = RunContext(s, kb, task.run_id, task.session_id,
                         question=task.question, research=research_runtime,
                         trusted_turns=list(getattr(rt, "trusted_turns", []) or []))
        async with _session_lock(task.session_id):
            result = None
            planned_task = task
            if research_runtime is not None:
                if research_discoverer is not None:
                    research_runtime.transition("discovering")
                    research_runtime.set_step_status("discover", "running")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        research_runtime.set_step_status("discover", "partial", "run deadline reached")
                        research_runtime.transition("limit_reached", "deadline exceeded during discovery")
                        result = _discovery_result(task, rt, "研究预算已耗尽，未能完成对象发现。", limited=True)
                    else:
                        try:
                            discovery = await asyncio.wait_for(
                                research_discoverer(task, research_runtime.contract,
                                                    research_runtime.plan, deadline),
                                timeout=remaining,
                            )
                        except TimeoutError:
                            research_runtime.set_step_status("discover", "partial", "run deadline reached")
                            research_runtime.transition("limit_reached", "deadline exceeded during discovery")
                            result = _discovery_result(task, rt, "研究预算已耗尽，未能完成对象发现。", limited=True)
                        if result is None:
                            if not isinstance(discovery, ResearchDiscovery):
                                discovery = ResearchDiscovery.model_validate(discovery)
                            research_runtime.accept_discovery(discovery.plan)
                            if discovery.status in {"needs_clarification", "insufficient", "unavailable"}:
                                reason = discovery.message or "当前证据不足以继续研究。"
                                research_runtime.set_step_status("discover", "running")
                                research_runtime.set_step_status("discover", "partial", reason)
                                research_runtime.transition("insufficient", reason)
                                result = _discovery_result(task, rt, reason)
                                # Discovery is not a research conclusion; expose only
                                # verified identity and coverage, never synthesize a
                                # comparison claim from an anchor without peers.
                                result.structured["research_discovery"] = {
                                    "status": discovery.status,
                                    "reason_code": discovery.reason_code,
                                    "documents": [
                                        {"planid": doc.planid, "title": doc.title,
                                         "area": doc.area, "fyp": doc.fyp}
                                        for doc in research_runtime.plan.documents
                                        if doc.source_verified
                                    ],
                                    "subject_gaps": list(research_runtime.plan.coverage.subject_gaps),
                                    "coverage_limited_subjects": list(
                                        research_runtime.plan.coverage.coverage_limited_subjects),
                                }
                            else:
                                discovered_plan = research_runtime.plan
                                requested_subjects = list(dict.fromkeys(
                                    discovery.requested_subjects
                                    or discovered_plan.requested_subjects
                                ))
                                missing_subjects = list(discovered_plan.coverage.subject_gaps)
                                if (task.task_type in {"compare", "compare_plans"}
                                        and len(discovered_plan.documents) >= 2
                                        and requested_subjects
                                        and missing_subjects):
                                    if research_runtime.revision_count >= research_runtime.max_revisions:
                                        reason = "研究计划修订预算已耗尽，缺少部分请求对象文献。"
                                        research_runtime.transition("insufficient", reason)
                                        result = _discovery_result(task, rt, reason)
                                    else:
                                        revised_data = discovered_plan.model_dump(mode="python")
                                        steps = revised_data["steps"]
                                        for step in steps:
                                            if step["step_id"] == "execute":
                                                step["input_refs"] = [
                                                    f"document:{doc.planid}"
                                                    for doc in discovered_plan.documents
                                                ]
                                        revised_data.update({
                                            "revision": discovered_plan.revision + 1,
                                            "scope": discovered_plan.scope.model_copy(update={
                                                "target_planids": [doc.planid for doc in discovered_plan.documents],
                                            }),
                                            "steps": steps,
                                            "coverage": discovered_plan.coverage.model_copy(update={
                                                "subject_gaps": missing_subjects,
                                            }),
                                        })
                                        revised_plan = type(discovered_plan).model_validate(revised_data)
                                        research_runtime.revise(
                                            revised_plan,
                                            "missing_document_coverage: " + ", ".join(missing_subjects),
                                        )
                                        discovered_plan = research_runtime.plan
                                discover_step = next(
                                    (step for step in research_runtime.plan.steps
                                     if step.step_id == "discover"), None)
                                if discover_step is not None and discover_step.status == "pending":
                                    research_runtime.set_step_status("discover", "running")
                                execution_step = next(
                                    (step for step in discovered_plan.steps
                                     if step.step_id == "execute"), None)
                                document_targets = [
                                    ref.removeprefix("document:")
                                    for ref in (execution_step.input_refs if execution_step else [])
                                    if ref.startswith("document:")
                                ]
                                if document_targets:
                                    planned_task = task.model_copy(update={
                                        "targets": document_targets,
                                        "dimensions": list(discovered_plan.dimensions) or task.dimensions,
                                    })
                else:
                    research_runtime.set_step_status("discover", "running")

                if result is None:
                    plan = research_runtime.plan
                    executor_steps = [step for step in plan.steps if step.step_id == "execute"]
                    if len(executor_steps) != 1 or executor_steps[0].capability != plan.task_type:
                        raise ValueError("research plan must select exactly one locked task capability")
                    research_runtime.transition("ready")
                    research_runtime.transition("executing")
                    research_runtime.set_step_status("execute", "running")
                    decision = decide(planned_task.question)
                    if planned_task.targets is not None:
                        decision = RouteDecision(kind="objects", planids=tuple(planned_task.targets),
                                                 areas=decision.areas, fyps=decision.fyps,
                                                 sub_query=decision.sub_query, blocked=decision.blocked)
                    ctx.route = decision
                    ctx.scope = tuple(planned_task.targets) if planned_task.targets is not None else None
                    if plan.task_type == "qa" and decision.kind == "open":
                        ctx.literal_keyword = literal_keyword(planned_task.question)
                    module = get_module(executor_steps[0].capability)
                    if module is not None:
                        result = await _run_business(planned_task, s, rt, ctx, deadline, decision, module)
                    elif decision.blocked:
                        result = _blocked(planned_task, ctx, rt.meta, decision)
                    elif (task.task_type == "qa" and s.kb_backend == "rag"
                          and not s.structured_output
                          and (decision.kind == "open" or planned_task.targets
                               or decision.planids)):
                        # Programmatic source retrieval with one grounded LLM call.
                        result = await _run_controlled_qa(planned_task, s, ctx, rt, deadline)
                    elif decision.kind == "objects":
                        result = await _run_objects(planned_task, s, ctx, rt, decision, deadline)
                    else:
                        result = await _run_single(planned_task, s, ctx, rt, deadline, decision)
                    if task.task_type == "qa" and result.status == "failed":
                        # Only RAG-verified original excerpts can survive model failure.
                        recovered = await recover_read_excerpts(result, ctx, deadline)
                        if not recovered:
                            recover_empty_search_failure(result, ctx)
                    if result.status == "failed":
                        research_runtime.set_step_status("execute", "failed",
                                                         result.error.message if result.error else "执行失败")
                    elif result.status == "limit_reached":
                        research_runtime.set_step_status("execute", "partial",
                                                         result.error.message if result.error else "运行预算已耗尽")
                    elif result.status == "partial":
                        research_runtime.set_step_status("execute", "partial", "业务能力返回部分结果")
                    else:
                        research_runtime.set_step_status("execute", "completed")
                    if any(step.step_id == "discover" and step.status == "running"
                           for step in research_runtime.plan.steps):
                        research_runtime.set_step_status("discover", "completed")
            else:
                decision = decide(task.question)
                if task.targets is not None:
                    decision = RouteDecision(kind="objects", planids=tuple(task.targets),
                                             areas=decision.areas, fyps=decision.fyps,
                                             sub_query=decision.sub_query, blocked=decision.blocked)
                ctx.route = decision
                if task.task_type == "qa" and decision.kind == "open":
                    ctx.literal_keyword = literal_keyword(task.question)
                if module is not None:
                    result = await _run_business(task, s, rt, ctx, deadline, decision, module)
                elif decision.blocked:
                    result = _blocked(task, ctx, rt.meta, decision)
                elif (task.task_type == "qa" and s.kb_backend == "rag"
                      and not s.structured_output
                      and (decision.kind == "open" or task.targets or decision.planids)):
                    result = await _run_controlled_qa(task, s, ctx, rt, deadline)
                elif decision.kind == "objects":
                    result = await _run_objects(task, s, ctx, rt, decision, deadline)
                else:
                    result = await _run_single(task, s, ctx, rt, deadline, decision)
            if result is None:
                result = _discovery_result(task, rt, "研究未能产生结果。")
            if (task.task_type == "qa" and result.status == "failed"
                    and not result.meta.get("execution_recovery")):
                recovered = await recover_read_excerpts(result, ctx, deadline)
                if not recovered:
                    recover_empty_search_failure(result, ctx)
            if research_runtime is not None and research_runtime.status == "executing":
                if result.status == "limit_reached":
                    research_runtime.transition("limit_reached", result.error.message if result.error else "运行预算已耗尽")
                elif result.status == "failed":
                    research_runtime.transition("failed", result.error.message if result.error else "执行失败")
                else:
                    research_runtime.transition("verifying")
                    research_runtime.set_step_status("verify", "running")
            result.task_type = task.task_type
            if task.task_type == "qa" and not result.meta.get("llm"):
                calls = list(getattr(rt.hooks, "usage_per_call", []) or [])
                if calls:
                    result.meta["llm"] = {
                        "model_calls": len(calls),
                        "input_tokens": sum(item.get("input") or 0 for item in calls),
                        "output_tokens": sum(item.get("output") or 0 for item in calls),
                        "per_call": calls,
                        "parse": "ok" if result.status == "completed" else "failed",
                    }
            if result.status == "completed" and module is None:
                try:
                    await asyncio.wait_for(verify_quotes(result, ctx),
                                           timeout=max(deadline - time.monotonic(), 0.05))
                    await asyncio.wait_for(verify_numbers(result, ctx),
                                           timeout=max(deadline - time.monotonic(), 0.05))
                except TimeoutError:
                    result.warnings.append("核验超出本次运行时限，未完成逐条核验")
                    result.evidence_status = "insufficient"
                    if task.task_type == "qa" and result._qa_report_supplied:
                        result.meta["verification_incomplete"] = True
                        result.claims = []
                        result.evidence_refs = []
            if research_runtime is not None and research_runtime.status == "verifying":
                if module is None and task.task_type == "qa":
                    enforce_literal_result(result, ctx)
                if module is None:
                    _attach(result, planned_task)
                research_runtime.set_step_status("verify", "completed")
    result.task_type = task.task_type
    finalize_controlled_facts(result)
    result = finalize_qa_retrieval(result, task)
    if not result.meta.get("execution_recovery"):
        result = finalize_qa_analysis(result, task.question)
    if (task.task_type == "qa" and ctx is not None
            and result.status == "completed"
            and not result.meta.get("execution_recovery")):
        # Synthesis is a draft only; supplementary facts must pass fresh checks.
        # Both share the original task deadline and preserve the existing result
        # when the remaining budget is insufficient.
        # Resolve one evidence gap BEFORE synthesizing the report so added
        # verified claims can support its draft and final semantic review.
        await supplement_one(result, task, s, ctx, rt, deadline,
                             _execute_once, _recover_qa_output)
        await draft_verified_report(result, task.question, rt, deadline)
    if task.task_type == "qa" and result._qa_report_supplied:
        remaining = max(deadline - time.monotonic(), 0.05)
        try:
            result = await asyncio.wait_for(
                finalize_report(result, ctx, rt, deadline), timeout=remaining)
        except TimeoutError:
            result.meta["verification_incomplete"] = True
            result._qa_report_candidates = []
            result.structured = {
                "schema_version": "qa-report/1",
                "dimensions": list(result._qa_report_dimensions), "blocks": [],
                "coverage": {"covered": [], "missing": list(result._qa_report_dimensions),
                             "status": "partial"},
                "verification": {"facts": "not_reviewed", "blocks": "not_reviewed"},
            }
            result.answer = SAFE_EMPTY_ANSWER
            result.evidence_status = "insufficient"
            result.meta.setdefault("qa_report", {}).update(
                reason="semantic_review_timeout", semantic_review="timeout", retained=0)
            result.warnings.append("报告语义审核超出本次运行时限，已撤回报告段落")
    # Recovery from token-cap truncation is allowed to publish only the
    # surviving, quote-verified facts. Do it after all verification gates and
    # before ResearchRuntime assesses final completion.
    finalize_recovered_qa_answer(result, task.question)
    if task.task_type == "qa":
        deliver_research(result, task.question)
    resolution = result.meta.get("qa_context_resolution")
    if (task.task_type == "qa" and isinstance(resolution, dict)
            and resolution.get("status") == "ambiguous"):
        # Ambiguous pronoun references must never silently reuse the first
        # previous document as if it were uniquely identified.
        clarification = resolution.get("clarification")
        if clarification:
            result.answer = clarification
            result.evidence_status = "insufficient"
            result.warnings.append(
                "多轮指代无法唯一确定，已停止检索并要求用户明确研究对象；未混合上一轮证据。")
    if (task.task_type == "qa" and not result.claims
            and isinstance(resolution, dict)
            and resolution.get("status") == "scope_changed"):
        constraints = resolution.get("requested_constraints") or {}
        areas = "、".join(constraints.get("areas") or []) or "未指定地区"
        periods = "、".join(constraints.get("periods") or []) or "未指定时期"
        result.answer = (
            f"本轮已按新的研究范围（{areas}，{periods}）重新检索，但知识库中没有"
            "找到与该地区、时期同时匹配的可引用政策原文；未取得证据不代表相关政策不存在。"
            "上一轮的沧州对象未被混入本轮证据。"
        )
        result.evidence_status = "insufficient"
        result.warnings.append("多轮范围变更后未找到匹配的新研究对象，已按新范围安全拒答。")
    if task.task_type == "qa" and isinstance(result.meta.get("llm"), dict):
        if result.meta.get("original_execution_error") or result.error:
            result.meta["llm"]["parse"] = "failed"
        elif result.meta["llm"].get("parse") == "pending":
            result.meta["llm"]["parse"] = (
                "ok" if not result.meta.get("output_parse")
                or result.meta["output_parse"].get("classification") == "ok"
                else "failed"
            )
    if research_runtime is not None:
        try:
            research_runtime.finish(result, ctx)
        except (ValueError, TypeError) as exc:
            result.status = "failed"
            result.evidence_status = "insufficient"
            result.error = RunError(code="INTERNAL", message="研究任务契约验收失败，结果未通过验证。")
            result.warnings.append("研究运行时无法完成任务契约验收")
            result.meta["research_runtime_error"] = type(exc).__name__
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
    research_gate = outcome.structured.get("research_gate")
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
        # A deterministic eligibility rejection can be explained without claiming
        # the policy document was searched exhaustively.
        if (isinstance(research_gate, dict)
                and research_gate.get("status") == "blocked"
                and research_gate.get("reason") == "topic_mismatch"):
            result.answer = outcome.answer
            result.meta["research_gate"] = research_gate
            result.evidence_status = "insufficient"
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


def _discovery_result(task, rt, message: str, *, limited: bool = False) -> RunResult:
    return RunResult(
        run_id=task.run_id,
        session_id=task.session_id,
        status="limit_reached" if limited else "completed",
        evidence_status="none" if limited else "insufficient",
        answer=message,
        claims=[],
        evidence_refs=[],
        dropped_chunk_ids=[],
        error=RunError(code="RUN_TIMEOUT", message=message) if limited else None,
        meta=dict(rt.meta),
        warnings=[] if limited else [message],
        task_type=task.task_type,
        structured={"research_discovery": {"status": "limit_reached" if limited else "insufficient"}},
    )


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
    last_output_tokens = ((outcome.telemetry or {}).get("per_call") or [{}])[-1].get("output")
    cap_reached = isinstance(last_output_tokens, int) and last_output_tokens >= s.llm_max_output_tokens
    meta["output_parse"]["token_cap_reached"] = cap_reached
    meta["output_parse"]["output_chars"] = len(outcome.final_output)
    if parsed is not None:
        # 可选 analyses 的格式坏了也不能抹掉完整事实；记录弃用数，防止原始
        # answer 里夹带没有可验证依赖的分析语句。
        try:
            payload = json.loads(outcome.final_output)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("analyses"), list):
            dropped = len(payload["analyses"]) - len(parsed.analyses)
            if dropped > 0:
                meta["output_parse"]["analyses_dropped"] = dropped
        if deterministic:
            if (getattr(parsed, "report", None) is not None
                    and not repair_preserves_content(outcome.final_output, parsed)):
                meta["output_parse"].update(repair="rejected", repair_rejected="content_changed")
                rt.log("qa_output_repair_failed", classification=classification,
                       result="content_changed")
                return outcome, meta
            meta["output_parse"]["deterministic_repair"] = deterministic
            rt.log("qa_output_parse_repaired", classification=classification,
                   method=deterministic)
            return replace(outcome, final_output=parsed), meta
        return outcome, meta
    if classification == "incomplete_json" and cap_reached:
        recovered = recover_complete_claims(outcome.final_output)
        if recovered is not None:
            meta["output_parse"].update(
                recovery="complete_claims_only", recovered_claims=len(recovered.claims))
            rt.log("qa_output_parse_repaired", classification=classification,
                   method="complete_claims_only", recovered_claims=len(recovered.claims))
            return replace(outcome, final_output=recovered), meta
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


async def _run_controlled_qa(task, s, ctx, rt, deadline):
    """Keep all existing QA parse, quote, number and report audit gates."""
    resolution = resolve_followup(task.question, getattr(ctx, "trusted_turns", []) or [],
                                  getattr(ctx, "route", None))
    ctx.qa_context_resolution = public_resolution(resolution)
    rt.meta["qa_context_resolution"] = ctx.qa_context_resolution
    if resolution["status"] == "ambiguous":
        outcome = ExecOutcome(json.dumps({
            "answer": resolution.get("clarification")
            or "无法唯一确定上一轮的研究对象，请明确具体文献或地区后再提问。",
            "claims": [], "insufficient": True,
        }, ensure_ascii=False), None)
        result = build_result(task, outcome, ctx, rt.meta)
        result.meta["qa_execution_mode"] = "bounded_retrieval"
        return result
    if resolution.get("inherited_planids"):
        # The prior source identity is trusted only after the current session
        # was claimed; current evidence is still re-read and re-verified below.
        ctx.scope = tuple(resolution["inherited_planids"])
    if resolution.get("inherited_planids") or resolution.get("context_note"):
        # Context notes and explicit region/period constraints both need to
        # reach controlled retrieval, but only inherited identities change scope.
        ctx.qa_followup = resolution
    outcome = await execute_controlled(task, s, ctx, rt, deadline)
    outcome, parse_meta = await _recover_qa_output(
        outcome, s, rt, deadline, repair_budget={"remaining": 0})
    result = build_result(task, outcome, ctx, rt.meta)
    if parse_meta:
        result.meta.update(parse_meta)
    turn = build_trusted_turn(task.question, ctx, result)
    if turn and result.status == "completed":
        rt.save_trusted_turn(turn)
    return result


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
