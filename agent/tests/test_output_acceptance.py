"""模块⑤验收测试：按输出规格断言，不依赖 output.py 的内部实现。"""
import json

from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.output import build_result, insufficient_message, to_markdown
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import AgentOutput, Claim
from guance_agent.tools import _call


async def _context(env, search=True):
    s, task = env
    ctx = RunContext(s, get_kb(s), task.run_id, task.session_id)
    if search:
        raw = await _call(ctx, "kb_search", ctx.kb.search, "农业", "甲", None, 1)
        assert json.loads(raw)["status"] == "ok"
    return ctx


def _out(answer, claims):
    return ExecOutcome(AgentOutput(answer=answer, claims=claims), None)


def _ref(ctx):
    return ctx.evidence.get(ctx.evidence.ids()[0])


async def test_partial_invalid_citations_are_not_verified(env):
    ctx = await _context(env)
    ref = _ref(ctx)
    result = build_result(env[1], _out("回答", [
        Claim(text="有效结论", chunk_ids=[ref.chunk_id]),
        Claim(text="伪造结论", chunk_ids=["TEST-999__s00__p00"]),
    ]), ctx, {})
    assert result.evidence_status != "verified"
    md = to_markdown(result)
    assert any(term in md for term in
               ("未被采信", "未采信", "未采纳", "不予采信", "未通过核验", "有效证据"))


async def test_all_invalid_citations_are_insufficient(env):
    ctx = await _context(env)
    result = build_result(env[1], _out("伪造回答", [
        Claim(text="伪造结论", chunk_ids=["TEST-999__s00__p00", "TEST-998__s00__p00"]),
    ]), ctx, {})
    assert result.evidence_status == "insufficient"


async def test_no_tool_answer_warns_before_answer(env):
    ctx = await _context(env, search=False)
    result = build_result(env[1], _out("甲市2025年农业补贴为每亩300元。", []), ctx, {})
    assert result.evidence_status == "none"
    md = to_markdown(result)
    warning_terms = ("未检索原文", "未检索", "未引用政策原文", "没有检索")
    positions = [md.index(term) for term in warning_terms if term in md]
    assert positions and min(positions) < md.index("每亩300元")


async def test_valid_citation_is_verified_without_warning(env):
    ctx = await _context(env)
    ref = _ref(ctx)
    result = build_result(env[1], _out("农业重点", [
        Claim(text="农业重点", chunk_ids=[ref.chunk_id]),
    ]), ctx, {})
    assert result.evidence_status == "verified"
    md = to_markdown(result)
    assert all(term not in md for term in
               ("证据不足", "未被采信", "未采信", "未检索", "未引用政策原文"))


async def test_citation_line_has_explicit_planid_label(env):
    ctx = await _context(env)
    ref = _ref(ctx)
    result = build_result(env[1], _out("农业重点", [
        Claim(text="农业重点", chunk_ids=[ref.chunk_id]),
    ]), ctx, {})
    assert result.evidence_status == "verified"
    assert "planid=" in to_markdown(result)


def test_invalid_argument_only_does_not_claim_no_results():
    message = insufficient_message([{"status": "invalid_argument", "message": "参数不合法"}])
    assert message.strip()
    assert "未检索到相关政策原文" not in message


async def test_insufficient_markdown_states_evidence_is_insufficient(env):
    ctx = await _context(env)
    result = build_result(env[1], _out("看起来确定的回答", [
        Claim(text="伪造结论", chunk_ids=["not-in-evidence"]),
    ]), ctx, {})
    assert result.evidence_status == "insufficient"
    md = to_markdown(result)
    assert any(term in md for term in
               ("证据不足", "依据不足", "无法给出有依据", "没有能够支撑", "无法支撑"))


async def test_markdown_tolerates_claim_id_missing_from_refs(env):
    ctx = await _context(env)
    ref = _ref(ctx)
    result = build_result(env[1], _out("回答", [
        Claim(text="结论", chunk_ids=[ref.chunk_id]),
    ]), ctx, {})
    assert result.evidence_status == "verified"
    result.claims[0].chunk_ids.append("not-in-evidence-refs")
    md = to_markdown(result)
    assert "回答" in md


async def test_context_conflict_poisons_chunk_id(env):
    ctx = await _context(env)
    first = _ref(ctx)
    ctx.evidence.register(first.model_copy(update={"planid": "forged"}))
    assert ctx.evidence.get(first.chunk_id) is None
    assert first.chunk_id in ctx.evidence.conflicts
    ctx.evidence.register(first)
    assert ctx.evidence.get(first.chunk_id) is None


async def test_context_same_plan_registration_overwrites(env):
    ctx = await _context(env)
    first = _ref(ctx)
    replacement = first.model_copy(update={"section_path": first.section_path + "#v2"})
    ctx.evidence.register(replacement)
    assert ctx.evidence.get(first.chunk_id) == replacement
    assert not ctx.evidence.conflicts
