"""QA 输出格式恢复的回归测试（PR #9 任务二）。

用固定模型输出与假客户端验证：分类、确定性修复、单次受预算修复、失败关闭。
"""
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from guance_agent.format_recovery import (
    INCOMPLETE_JSON,
    MULTIPLE_OBJECTS,
    OK,
    SCHEMA_INVALID,
    classify_output,
    parse_agent_output,
    repair_messages,
    repair_preserves_content,
)
from guance_agent.orchestrator import _recover_qa_output
from guance_agent.output import build_result
from guance_agent.runner import ExecOutcome

VALID = '{"answer":"有依据的回答","claims":[],"insufficient":false}'
INVALID = '{"answer":"可恢复的回答", "claims": }'
RECOVERED = '{"answer":"可恢复的回答","claims":[],"insufficient":false}'


class FakeRuntime:
    """只实现格式恢复需要的接口，记录日志与修复调用。"""

    def __init__(self, reply, *, turns=1):
        self.reply, self.turns = reply, turns
        self.hooks = SimpleNamespace(turns=turns)
        self.calls, self.logs = [], []

    def log(self, stage, **values):
        self.logs.append((stage, values))

    async def complete_text(self, messages, *, max_tokens, timeout):
        self.calls.append({"messages": messages, "max_tokens": max_tokens, "timeout": timeout})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _outcome(raw):
    return ExecOutcome(raw, None)


def test_valid_json_parses():
    classification, parsed, repair = classify_output(VALID)
    assert classification == OK and parsed is not None and repair is None
    assert parse_agent_output(VALID).answer == "有依据的回答"


@pytest.mark.parametrize("raw,expected", [
    ('{"answer":"a","claims":[]', INCOMPLETE_JSON),
    ('{"answer":"a","claims":[]} {"answer":"b","claims":[]}', MULTIPLE_OBJECTS),
    ('{"answer":42,"claims":[]}', SCHEMA_INVALID),
    ("完全没有 JSON 的回答", "not_json"),
])
def test_failure_classification(raw, expected):
    classification, parsed, _ = classify_output(raw)
    assert classification == expected and parsed is None


@pytest.mark.parametrize("raw", [
    '{"answer":"a",}',
    '"{\\"answer\\":\\"a\\",\\"claims\\":[]}"',
    '这里是结果：\n```json\n{"answer":"a","claims":[]}\n```\n结束',
    '<think>思考</think>{"answer":"a","claims":[]}',
])
def test_deterministic_repair_recovers_safe_formats(raw):
    classification, parsed, repair = classify_output(raw)
    assert classification == OK and parsed is not None
    assert parsed.answer == "a"


def test_trailing_comma_repair_keeps_string_content():
    raw = '{"answer":"示例 ,] 内容","claims":[],}'
    classification, parsed, repair = classify_output(raw)
    assert classification == OK and repair == "trailing_comma"
    assert parsed.answer == "示例 ,] 内容"


def test_multiple_objects_behind_code_fence_are_not_collapsed(env):
    """一个对象 + 代码块里的第二个对象：不得被确定性修复收窄成唯一对象。"""
    raw = '{"answer":"A","claims":[]}\n```json\n{"answer":"B","claims":[]}\n```'
    classification, parsed, repair = classify_output(raw)
    assert classification == MULTIPLE_OBJECTS and parsed is None and repair is None
    result = build_result(env[1], _outcome(raw), None, {})
    assert result.error and result.error.code == "OUTPUT_PARSE"


def test_prose_with_unclosed_brace_before_valid_json_fails_closed():
    """正文里出现未闭合花括号时无法安全区分容器与说明：失败关闭，不取后面对象。"""
    raw = '说明：用 { 表示开头\n{"answer":"a","claims":[]}'
    classification, parsed, _ = classify_output(raw)
    assert classification == INCOMPLETE_JSON and parsed is None


def test_inner_object_behind_loose_wrapper_is_not_the_result():
    """wrapper 前缀不以 JSON 分隔符结尾（中文/字母）也不能把内层对象当结果。"""
    for raw in ('{"result": 坏 {"answer":"内层","claims":[]}',
                '{"result": x {"answer":"内层","claims":[]}'):
        classification, parsed, _ = classify_output(raw)
        assert classification == INCOMPLETE_JSON and parsed is None


@pytest.mark.asyncio
async def test_repair_rejected_when_insufficient_flag_flipped(env):
    raw = '{"answer":"可恢复的回答","claims":[],"insufficient": true, "x": }'
    repaired = '{"answer":"可恢复的回答","claims":[],"insufficient":false}'
    rt = FakeRuntime(repaired)
    outcome, meta = await _recover_qa_output(_outcome(raw), env[0], rt,
                                             time.monotonic() + 30)
    assert meta["output_parse"]["repair_rejected"] == "content_changed"
    assert outcome.final_output == raw


def test_guard_rejects_when_declared_claims_are_dropped():
    raw = ('{"answer":"回答","claims":[{"text":"结论一","chunk_ids":["c-1"]},'
           '{"text":"结论二","chunk_ids":["c-2"]}], "insufficient": true}')
    kept_one = parse_agent_output(
        '{"answer":"回答","claims":[{"text":"结论一","chunk_ids":["c-1"]}],'
        '"insufficient":true}')
    assert not repair_preserves_content(raw, kept_one)
    kept_two = parse_agent_output(
        '{"answer":"回答","claims":[{"text":"结论一","chunk_ids":["c-1"]},'
        '{"text":"结论二","chunk_ids":["c-2"]}],"insufficient":true}')
    assert repair_preserves_content(raw, kept_two)


def test_guard_rejects_evidence_remap_and_quote_loss():
    raw = ('{"answer":"回答","claims":[{"text":"结论一","chunk_ids":["c-1"],'
           '"quotes":["原文引句"]}], "insufficient": }')
    base = parse_agent_output(
        '{"answer":"回答","claims":[{"text":"结论一","chunk_ids":["c-1"],'
        '"quotes":["原文引句"]}],"insufficient":false}')
    assert repair_preserves_content(raw, base)
    remapped = base.model_copy(deep=True)
    remapped.claims[0].chunk_ids = ["c-2"]
    assert not repair_preserves_content(raw, remapped)
    dropped_quote = base.model_copy(deep=True)
    dropped_quote.claims[0].quotes = []
    assert not repair_preserves_content(raw, dropped_quote)
    emptied = base.model_copy(update={"answer": ""})
    assert not repair_preserves_content(raw, emptied)


def test_guard_accepts_unicode_escape_repair():
    """原输出里的 \\uXXXX 转义在修复后按同一文本比对，不应被误杀。"""
    parsed = parse_agent_output('{"answer":"原文","claims":[],"insufficient":false}')
    assert repair_preserves_content('{"answer":"\\u539f\\u6587","claims":[]}', parsed)


def test_broken_wrapper_with_two_inner_objects_is_ambiguous(env):
    raw = ('{"result": [{"text":"甲","chunk_ids":["c-1"]},'
           '{"text":"乙","chunk_ids":["c-2"]}], "broken": }')
    classification, parsed, repair = classify_output(raw)
    assert classification == MULTIPLE_OBJECTS and parsed is None and repair is None


def test_inner_object_inside_broken_wrapper_is_not_the_result(env):
    """外层 JSON 坏掉、内层对象看似完整时，不得把内层当成模型结果。"""
    broken = '{"result": {"answer":"内层","claims":[]}, "broken": }'
    classification, parsed, repair = classify_output(broken)
    assert classification == "invalid_json" and parsed is None and repair is None
    truncated = '{"result": {"answer":"内层","claims":[]}'
    classification, parsed, _ = classify_output(truncated)
    assert classification == "incomplete_json" and parsed is None
    result = build_result(env[1], _outcome(truncated), None, {})
    assert result.error and result.error.code == "OUTPUT_PARSE"


def test_ambiguous_or_incomplete_is_not_recovered(env):
    for raw in ('{"answer":"a","claims":[]} {"answer":"b","claims":[]}',
                '{"answer":"截断","claims":[]'):
        result = build_result(env[1], _outcome(raw), None, {})
        assert result.error and result.error.code == "OUTPUT_PARSE"
        assert result.meta["output_parse"]["classification"] in (MULTIPLE_OBJECTS, INCOMPLETE_JSON)


@pytest.mark.asyncio
async def test_model_repair_succeeds_once(env):
    rt = FakeRuntime(RECOVERED)
    outcome, meta = await _recover_qa_output(
        _outcome(INVALID), env[0], rt, time.monotonic() + 30)
    assert len(rt.calls) == 1
    assert meta["output_parse"]["repair"] == "attempted"
    assert meta["output_parse"]["repair_result"] == OK
    assert meta["output_parse"]["model_calls"] == 1
    assert outcome.final_output is not None and outcome.final_output.answer == "可恢复的回答"


@pytest.mark.asyncio
async def test_repair_failure_fails_closed(env):
    rt = FakeRuntime('{"answer":"还是坏的",')
    outcome, meta = await _recover_qa_output(
        _outcome(INVALID), env[0], rt, time.monotonic() + 30)
    assert len(rt.calls) == 1
    assert meta["output_parse"]["repair"] == "attempted"
    assert meta["output_parse"]["repair_result"] == INCOMPLETE_JSON
    result = build_result(env[1], outcome, None, {})
    assert result.error and result.error.code == "OUTPUT_PARSE"


@pytest.mark.asyncio
async def test_repair_rejected_when_content_rewritten(env):
    """修复调用把回答改写成原输出里没有的内容时，整次修复作废并失败关闭。"""
    rt = FakeRuntime('{"answer":"模型新编的回答","claims":[],"insufficient":false}')
    outcome, meta = await _recover_qa_output(
        _outcome(INVALID), env[0], rt, time.monotonic() + 30)
    assert len(rt.calls) == 1
    assert meta["output_parse"]["repair_rejected"] == "content_changed"
    assert outcome.final_output == INVALID
    result = build_result(env[1], outcome, None, {})
    assert result.error and result.error.code == "OUTPUT_PARSE"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    '{"answer":"a","claims":[]} {"answer":"b","claims":[]}',   # 多个对象不修复
    "完全没有 JSON 的回答",                                      # 非 JSON 不修复
    '{"answer":"a","claims":[]',                                # 截断不修复
])
async def test_unrepairable_classes_never_call_model(env, raw):
    rt = FakeRuntime(VALID)
    outcome, meta = await _recover_qa_output(
        _outcome(raw), env[0], rt, time.monotonic() + 30)
    assert rt.calls == []
    assert meta["output_parse"]["repair"] == "not_attempted"
    assert outcome.final_output == raw


@pytest.mark.asyncio
async def test_repair_respects_time_and_turn_budget(env):
    s = env[0]
    short = replace(s, max_turns=2)
    rt = FakeRuntime(RECOVERED, turns=2)
    _, meta = await _recover_qa_output(_outcome(INVALID), short, rt,
                                       time.monotonic() + 30)
    assert rt.calls == [] and meta["output_parse"]["repair"] == "skipped_budget"
    rt2 = FakeRuntime(RECOVERED, turns=0)
    _, meta2 = await _recover_qa_output(_outcome(INVALID), s, rt2,
                                        time.monotonic() + 2)
    assert rt2.calls == [] and meta2["output_parse"]["repair"] == "skipped_budget"


@pytest.mark.asyncio
async def test_repair_does_not_rerun_retrieval(env):
    rt = FakeRuntime(RECOVERED)
    await _recover_qa_output(_outcome(INVALID), env[0], rt,
                             time.monotonic() + 30)
    messages = rt.calls[0]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "tools" not in rt.calls[0]
    assert rt.calls[0]["max_tokens"] and rt.calls[0]["timeout"] > 0


@pytest.mark.asyncio
async def test_repair_cannot_smuggle_unregistered_evidence(env):
    """修复结果里的伪造 chunk_id 仍按原规则丢弃，不能凭修复获得证据状态。"""
    broken = ('{"answer":"回答","claims":[{"text":"结论","chunk_ids":["forged-id"]}],'
              ' "insufficient": }')
    repaired = ('{"answer":"回答","claims":[{"text":"结论","chunk_ids":["forged-id"]}],'
                ' "insufficient": false}')
    rt = FakeRuntime(repaired)
    outcome, meta = await _recover_qa_output(_outcome(broken), env[0], rt,
                                             time.monotonic() + 30)
    assert meta["output_parse"]["repair"] == "attempted"
    result = build_result(env[1], outcome, None, {})
    assert result.claims == [] and result.evidence_refs == []
    assert "forged-id" in result.dropped_chunk_ids
    assert result.evidence_status == "insufficient"


def test_repair_messages_clip_output_and_carry_no_question():
    messages = repair_messages("x" * 100, 40)
    assert len(messages) == 2 and len(messages[1]["content"]) == 40
    assert "检索" not in messages[0]["content"]
    assert "输出结构" in messages[0]["content"]


def test_repair_preserves_content_guard():
    parsed = parse_agent_output('{"answer":"原文回答","claims":[{"text":"原文结论",'
                                '"chunk_ids":["chunk-real"]}],"insufficient":false}')
    raw = '{"answer":"原文回答","claims":[{"text":"原文结论","chunk_ids":["chunk-real"]}'
    assert repair_preserves_content(raw, parsed)
    forged = parsed.model_copy(update={"answer": "新编回答"})
    assert not repair_preserves_content(raw, forged)
    fake_id = parsed.model_copy(deep=True)
    fake_id.claims[0].chunk_ids = ["forged-id"]
    assert not repair_preserves_content(raw, fake_id)
