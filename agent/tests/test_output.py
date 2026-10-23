import pytest

from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.output import ERRORS, LIMIT_CODES, build_result, insufficient_message, to_markdown
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import AgentOutput, Claim


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
    assert result.evidence_status == "verified" and result.claims[0].chunk_ids == [ref.chunk_id]
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
