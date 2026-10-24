"""只接收本次证据 ID，确定性生成结果和出处。"""
import re

from .context import RunContext
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
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
            parsed = AgentOutput.model_validate_json(raw[raw.index("{"):raw.rindex("}") + 1])
        else:
            parsed = AgentOutput.model_validate(raw)
    except (ValueError, TypeError):
        return build_result(task, None, ctx, meta, RunError(code="OUTPUT_PARSE", message=ERRORS["OUTPUT_PARSE"]))
    refs, dropped, affected = {}, [], 0
    for claim in parsed.claims:
        valid, invalid = [], False
        for chunk_id in claim.chunk_ids:
            ref = ctx.evidence.get(chunk_id) if ctx else None
            if ref:
                refs[chunk_id] = ref
                valid.append(chunk_id)
            else:
                dropped.append(chunk_id)
                invalid = True
        affected += invalid
        if valid:
            result.claims.append(claim.model_copy(update={"chunk_ids": list(dict.fromkeys(valid))}))
    result.meta["dropped_claims"] = len(parsed.claims) - len(result.claims)
    result.dropped_chunk_ids = list(dict.fromkeys(dropped))
    result.evidence_refs, result.answer = list(refs.values()), parsed.answer
    all_dropped = bool(parsed.claims) and not result.claims
    incomplete = bool(result.dropped_chunk_ids or result.meta["dropped_claims"])
    if incomplete and not all_dropped:
        result.answer += f"\n\n（提示：有 {affected} 条结论因缺少有效证据，未被采信。）"
    if result.claims:
        if incomplete or parsed.insufficient:
            result.evidence_status = "insufficient"
        else:
            result.evidence_status = "verified"
        if parsed.insufficient:
            result.answer += "\n\n（提示：本次证据不足，以上结论可能不完整。）"
    # 全部证据被丢弃时模型答案已不可信，保留替换文案；其余 insufficient 保留原话并追加提示。
    elif all_dropped:
        result.evidence_status = "insufficient"
        result.answer = insufficient_message(ctx.tool_calls if ctx else [])
    elif parsed.insufficient:
        result.evidence_status = "insufficient"
        result.answer += "\n\n（提示：本次证据不足，以上结论可能不完整。）"
    elif ctx and ctx.tool_calls:
        result.evidence_status = "insufficient"
        result.answer = insufficient_message(ctx.tool_calls)
    else:
        result.meta["no_tool_answer"] = True
    return result

def to_markdown(r: RunResult) -> str:
    if r.error:
        return r.error.message
    lines, refs = [], {ref.chunk_id: ref for ref in r.evidence_refs}
    if r.evidence_status == "none":
        lines.append("（警告：本回答未引用政策原文，且未调用检索工具，结论未经核验，请谨慎参考。）")
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
    if r.evidence_status == "insufficient":
        lines.append("（本次证据不足，以上结论可能不完整）")
    return "\n".join(lines)
