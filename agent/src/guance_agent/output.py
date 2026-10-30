"""只接收本次证据 ID，确定性生成结果和出处。"""
from .context import RunContext, sanitize_error_detail
from .format_recovery import parse_agent_output
from .runner import ExecOutcome
from .schemas import AgentOutput, RunError, RunResult, TaskRequest

LIMIT_CODES = frozenset({"MAX_TURNS", "RUN_TIMEOUT", "TOOL_BUDGET"})
ERRORS = dict(INVALID_INPUT="问题为空、超长或格式不正确", SESSION_FORBIDDEN="无权访问该会话",
              CONFIG_ERROR="服务配置有误，请联系管理员",
              MAX_TURNS="本次任务步骤过多，已停止，请缩小问题范围", RUN_TIMEOUT="本次任务超时，已停止",
              MODEL_UNAVAILABLE="模型服务暂时不可用，请稍后再试", MODEL_BAD_REQUEST="模型接口不支持本次请求参数",
              MODEL_UNAUTHORIZED="模型服务认证失败，请检查接口密钥与权限",
              MODEL_NOT_FOUND="模型或接口地址不存在，请检查模型名称与 Base URL",
              TOOL_BUDGET="本次任务工具调用次数过多，已停止，请缩小问题范围",
              MODEL_BEHAVIOR="模型返回了无法执行的工具调用", OUTPUT_PARSE="模型输出无法解析为约定结构",
              CANCELLED="任务已取消", INTERNAL="系统内部错误")

def insufficient_message(calls):
    messages, statuses = [], {c["status"] for c in calls}
    if statuses & {"timeout", "backend_error"}:
        messages.append("检索服务暂时不可用，本次无法给出有依据的结论，请稍后再试。")
    for status in ("no_text", "not_found"):
        messages.extend(c["message"] for c in calls if c["status"] == status)
    if "empty" in statuses:
        messages.append("未检索到相关政策原文，暂无法给出有依据的结论。")
    if "invalid_argument" in statuses:
        messages.append("本次检索参数不合法，未能完成检索。")
    if "ok" in statuses or not messages:
        messages.append("本次检索结果中没有能够支撑回答的原文证据，暂无法给出有依据的结论。")
    return "\n".join(dict.fromkeys(messages))


def _parse_nonstructured_output(raw: str) -> AgentOutput:
    """兼容入口：解析规则与分类见 format_recovery（只接受唯一完整对象）。"""
    return parse_agent_output(raw)


def rebuild_trusted_answer(result, ctx, reason: str) -> None:
    """证据被删除后只展示仍可追溯的结论，不能保留模型原始答案。"""
    active_ids = {chunk_id for claim in result.claims for chunk_id in claim.chunk_ids}
    result.evidence_refs = [ref for ref in result.evidence_refs if ref.chunk_id in active_ids]
    result.evidence_status = "insufficient"
    result.meta["answer_rebuilt"] = reason
    if result.claims:
        result.answer = "以下仅保留可追溯到已读原文的结论：\n" + "\n".join(
            f"- {claim.text}" for claim in result.claims)
    else:
        result.answer = insufficient_message(ctx.tool_calls if ctx else [])

def build_result(task: TaskRequest, outcome: ExecOutcome | None, ctx: RunContext | None,
                 meta: dict, error: RunError | None = None) -> RunResult:
    meta = {"elapsed_ms": 0, "tool_calls": ctx.tool_calls if ctx else [], **meta}
    code = error.code if error else (outcome.error_code if outcome else "INTERNAL")
    result = RunResult(run_id=task.run_id, session_id=task.session_id, status="completed",
                        evidence_status="none", answer="", claims=[], evidence_refs=[],
                        dropped_chunk_ids=[], error=None, meta=meta)
    if code:
        result.status = ("limit_reached" if code in LIMIT_CODES else "failed")
        result.error = error or RunError(code=code, message=ERRORS.get(code, ERRORS["INTERNAL"]))
        result.answer = result.error.message
        return result
    try:
        raw = outcome.final_output
        if isinstance(raw, str) and not (ctx and ctx.settings.structured_output):
            parsed = _parse_nonstructured_output(raw)
        else:
            parsed = AgentOutput.model_validate(raw)
    except (ValueError, TypeError) as exc:
        # 失败分类是固定码，不含模型原文；用于区分语法坏、歧义与不完整输出。
        meta = {**meta, "output_parse": {"classification": getattr(exc, "classification", "unknown")}}
        # 只在显式开启 LOG_CONTENT 时保存片段，避免把模型原文写进运行日志
        if ctx and ctx.settings.log_content:
            # 只在本地显式开启时保留一段脱敏样本，用于区分格式错误 / 截断 / 解析器问题
            meta = {**meta, "parse_error": type(exc).__name__,
                    "output_sample": sanitize_error_detail(
                        locals().get("raw", ""), ctx.settings, task.question, 200)}
        return build_result(task, None, ctx, meta, RunError(code="OUTPUT_PARSE", message=ERRORS["OUTPUT_PARSE"]))
    refs, dropped, dropped_texts, partial = {}, [], [], 0
    for claim in parsed.claims:
        valid, had_invalid = [], False
        for chunk_id in claim.chunk_ids:
            ref = ctx.evidence.get(chunk_id) if ctx else None
            if ref:
                refs[chunk_id] = ref
                valid.append(chunk_id)
            else:
                dropped.append(chunk_id)
                had_invalid = True
        if valid:
            result.claims.append(claim.model_copy(update={"chunk_ids": list(dict.fromkeys(valid))}))
            partial += had_invalid
        else:
            dropped_texts.append(claim.text)
    result.meta["dropped_claims"] = len(dropped_texts)
    result.dropped_chunk_ids = list(dict.fromkeys(dropped))
    result.evidence_refs, result.answer = list(refs.values()), parsed.answer
    # 提示一律进 warnings：answer 保持模型原话，便于评测比对，也不依赖某个渲染器才可见。
    if dropped_texts:
        result.warnings.append("以下结论缺少可核验证据，未被采信：" + "；".join(dropped_texts))
    if partial:
        result.warnings.append(f"有 {partial} 条结论的部分引用无法核验，相关表述未经完整证实")
    if any(ref.out_of_section for ref in result.evidence_refs):
        result.warnings.append("部分证据的完整块越出其登记章节，引用中的章节仅供定位参考")
    if parsed.insufficient:
        result.warnings.append("本次证据不足，以上结论可能不完整")
    all_dropped = bool(parsed.claims) and not result.claims
    if result.claims:
        result.evidence_status = ("insufficient"
                                  if (dropped_texts or partial or parsed.insufficient) else "verified")
    elif all_dropped:
        result.evidence_status = "insufficient"
        result.answer = insufficient_message(ctx.tool_calls if ctx else [])
    elif parsed.insufficient or (ctx and ctx.tool_calls):
        result.evidence_status = "insufficient"
        if not parsed.insufficient:
            result.answer = insufficient_message(ctx.tool_calls)
    else:
        result.meta["no_tool_answer"] = True
        result.warnings.append("本回答未引用政策原文，也未调用检索工具，结论未经核验，不构成政策依据")
    if dropped_texts or partial:
        rebuild_trusted_answer(result, ctx, "unregistered_evidence")
    return result

def to_markdown(r: RunResult) -> str:
    if r.error:
        return r.error.message
    lines, refs = [f"⚠ {w}" for w in r.warnings], {ref.chunk_id: ref for ref in r.evidence_refs}
    if lines:
        lines.append("")
    lines.append(r.answer)
    for claim in r.claims:
        lines.append("\n" + claim.text)
        lines.extend("> " + quote for quote in claim.quotes)
        for chunk_id in claim.chunk_ids:
            ref = refs.get(chunk_id)
            if ref is None:
                lines.append(f"（出处缺失：{chunk_id}）")
                continue
            lines.append(f"（规划库）《{ref.title}》·{ref.area or '地区未标注'}·"
                         f"{ref.fyp or '规划期未标注'}·planid={ref.planid}·"
                         f"{ref.section_path or '正文'}·片段编号 {chunk_id}")
    return "\n".join(lines)
