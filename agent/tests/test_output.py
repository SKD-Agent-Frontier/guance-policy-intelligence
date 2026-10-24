import pytest

from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.output import ERRORS, LIMIT_CODES, build_result, insufficient_message, to_markdown
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import AgentOutput, Claim, RunResult


def context(env):
    s, task = env
    ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
    ctx.evidence.add_result(ctx.kb.search("农业", "甲", None, 1), "kb_search")
    return ctx


@pytest.mark.parametrize("chunk_id", ["forged", "TEST-002__s00__p00"])
def test_unreturned_ids_rejected(env, chunk_id):
    ctx = context(env)
    out = AgentOutput(answer="无证据肯定回答", claims=[Claim(text="伪造", chunk_ids=[chunk_id])])
    result = build_result(env[1], ExecOutcome(out, None), ctx, {})
    assert result.dropped_chunk_ids == [chunk_id] and not result.evidence_refs
    assert result.evidence_status == "insufficient" and result.answer != out.answer
    assert result.meta["dropped_claims"] == 1


def test_valid_citation_and_source_recovery(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    out = AgentOutput(answer="回答", claims=[Claim(text="农业重点", chunk_ids=[ref.chunk_id, "bad"], quotes=["任意引文不作逐字检查"])])
    result = build_result(env[1], ExecOutcome(out, None), ctx, {})
    assert result.evidence_status == "insufficient" and result.claims[0].chunk_ids == [ref.chunk_id]
    assert result.answer == "回答" and "1 条结论" in " ".join(result.warnings)
    md = to_markdown(result)
    assert all(value in md for value in (ref.title, ref.section_path, ref.chunk_id, out.claims[0].quotes[0]))
    p = ctx.kb.read(ref.planid, ref.section_idx, 6000)
    original = p.content[ref.char_start - p.char_start:ref.char_end - p.char_start]
    assert original == ctx.kb.search("农业", "甲", None, 1)[0].content
    out.insufficient = True
    assert build_result(env[1], ExecOutcome(out, None), ctx, {}).evidence_status == "insufficient"


@pytest.mark.parametrize("status,message,expected", [
    ("no_text", "《测试》（planid=TEST-003）没有可用正文，无法引用原文", "没有可用正文"),
    ("empty", "未检索到", "未检索到相关政策原文"),
    ("timeout", "超时", "服务暂时不可用"),
    ("backend_error", "故障", "服务暂时不可用"),
    ("not_found", "未找到 planid=TEST-999 的篇目", "未找到"),
    ("ok", "", "没有能够支撑回答"),
])
def test_insufficient_replaces_model_answer(env, status, message, expected):
    ctx = context(env)
    ctx.tool_calls.append(dict(status=status, message=message))
    result = build_result(env[1], ExecOutcome(AgentOutput(answer="不可展示"), None), ctx, {})
    assert result.evidence_status == "insufficient" and expected in result.answer
    assert "不可展示" not in to_markdown(result)


def test_no_tool_greeting_and_thinking(env):
    ctx = context(env)
    raw = '<think>{隐藏推理}</think>```json\n{"answer":"你好","claims":[],"insufficient":false}\n```'
    result = build_result(env[1], ExecOutcome(raw, None), ctx, {})
    assert result.evidence_status == "none" and result.meta["no_tool_answer"]
    assert "本回答未引用政策原文" in to_markdown(result) and "隐藏" not in result.answer


@pytest.mark.parametrize("code", list(ERRORS))
def test_error_result_complete(env, code):
    result = build_result(env[1], ExecOutcome(None, code), None, {})
    assert result.error.code == code and result.answer == ERRORS[code]
    assert result.status == ("limit_reached" if code in LIMIT_CODES else "failed")
    assert not result.claims and not result.evidence_refs and result.evidence_status == "none"
    assert to_markdown(result) == ERRORS[code]


def test_parse_failure_and_order(env):
    result = build_result(env[1], ExecOutcome("not json", None), context(env), {})
    assert result.error.code == "OUTPUT_PARSE"
    calls = [dict(status="no_text", message="无正文"), dict(status="timeout", message="超时"),
             dict(status="no_text", message="无正文"), dict(status="not_found", message="未找到")]
    message = insufficient_message(calls)
    assert message.startswith("检索服务暂时不可用") and message.count("无正文") == 1


def test_new_error_messages():
    assert ERRORS["TOOL_BUDGET"] == "本次任务工具调用次数过多，已停止，请缩小问题范围"
    assert ERRORS["MODEL_UNAUTHORIZED"] == "模型服务认证失败，请检查接口密钥与权限"
    assert "Base URL" in ERRORS["MODEL_NOT_FOUND"]


def test_partial_invalid_reference_downgrades_and_warns(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    out = AgentOutput(answer="A市补贴 5000 元，B市补贴 8000 元。", claims=[
        Claim(text="A市", chunk_ids=[ref.chunk_id]),
        Claim(text="B市", chunk_ids=["FAKE"])])
    result = build_result(env[1], ExecOutcome(out, None), ctx, {})
    assert result.evidence_status == "insufficient"
    assert result.answer.startswith(out.answer)
    assert any("未被采信" in w and "B市" in w for w in result.warnings)
    assert result.dropped_chunk_ids == ["FAKE"] and result.meta["dropped_claims"] == 1


def test_no_tool_answer_warning_precedes_answer(env):
    ctx = context(env)
    answer = "长沙市中小企业补贴标准是 5000 元。"
    result = build_result(env[1], ExecOutcome(AgentOutput(answer=answer), None), ctx, {})
    md = to_markdown(result)
    assert result.answer == answer
    assert result.evidence_status == "none" and result.meta["no_tool_answer"]
    assert "本回答未引用政策原文" in md and md.index("⚠") < md.index(answer)


def test_citation_has_independent_planid(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    out = AgentOutput(answer="回答", claims=[Claim(text="结论", chunk_ids=[ref.chunk_id])])
    md = to_markdown(build_result(env[1], ExecOutcome(out, None), ctx, {}))
    assert f"planid={ref.planid}" in md and ref.chunk_id in md


def test_invalid_argument_is_not_empty_result():
    invalid = insufficient_message([{"status": "invalid_argument", "message": "参数不合法：top_k"}])
    assert "本次检索参数不合法，未能完成检索" in invalid
    assert "未检索到相关政策原文" not in invalid
    empty = insufficient_message([{"status": "empty", "message": ""}])
    assert "未检索到相关政策原文" in empty


def test_insufficient_preserves_answer_unless_all_evidence_dropped(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    kept = build_result(env[1], ExecOutcome(AgentOutput(
        answer="模型原话", claims=[Claim(text="结论", chunk_ids=[ref.chunk_id])],
        insufficient=True), None), ctx, {})
    assert kept.evidence_status == "insufficient"
    assert kept.answer.startswith("模型原话") and any("证据不足" in w for w in kept.warnings)
    dropped = build_result(env[1], ExecOutcome(AgentOutput(
        answer="不可信原话", claims=[Claim(text="结论", chunk_ids=["FAKE"])],
        insufficient=True), None), ctx, {})
    assert dropped.evidence_status == "insufficient"
    assert "不可信原话" not in dropped.answer


def test_insufficient_without_claims_preserves_answer(env):
    ctx = context(env)
    ctx.tool_calls.append(dict(status="ok", message=""))
    out = AgentOutput(answer="模型已说明证据不足", claims=[], insufficient=True)
    result = build_result(env[1], ExecOutcome(out, None), ctx, {})
    assert result.evidence_status == "insufficient"
    assert result.answer.startswith(out.answer) and any("证据不足" in w for w in result.warnings)


def test_markdown_marks_insufficient_and_verified(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claim = Claim(text="结论", chunk_ids=[ref.chunk_id])
    verified = build_result(env[1], ExecOutcome(AgentOutput(answer="回答", claims=[claim]), None), ctx, {})
    insufficient = build_result(env[1], ExecOutcome(
        AgentOutput(answer="回答", claims=[claim], insufficient=True), None), ctx, {})
    assert "本次证据不足" not in to_markdown(verified)
    assert "本次证据不足，以上结论可能不完整" in to_markdown(insufficient)
    none = build_result(env[1], ExecOutcome(AgentOutput(answer="回答"), None), context(env), {})
    assert "本回答未引用政策原文" in to_markdown(none)


def test_markdown_missing_ref_does_not_crash():
    result = RunResult(run_id="run", session_id="s", status="completed",
                       evidence_status="verified", answer="答案",
                       claims=[Claim(text="结论", chunk_ids=["MISSING"])],
                       evidence_refs=[], dropped_chunk_ids=[], error=None, meta={})
    assert "（出处缺失：MISSING）" in to_markdown(result)
