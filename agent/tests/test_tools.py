import json
from dataclasses import replace

import pytest
from agents.tool_context import ToolContext

from guance_agent.config import Settings
from guance_agent.context import RunContext
from guance_agent.kb import MockKB, get_kb
from guance_agent.schemas import Hit, Passage, ToolResult
from guance_agent.tools import _call, kb_read, kb_search


@pytest.fixture
def ctx():
    s = Settings()
    return RunContext(s, get_kb(s), "run_test", "s_test")


async def invoke(tool, ctx, **args):
    raw = await tool.on_invoke_tool(ToolContext(context=ctx, tool_name=tool.name,
        tool_call_id="test_call", tool_arguments=json.dumps(args)), json.dumps(args))
    return ToolResult.model_validate_json(raw)


@pytest.mark.parametrize("query,status", [("农业", "ok"), ("量子通信", "empty"), (" ", "invalid_argument")])
async def test_search_status(ctx, query, status):
    result = await invoke(kb_search, ctx, query=query, area="甲", fyp="十四五", top_k=8)
    assert result.status == status
    assert bool(ctx.evidence.ids()) == (status == "ok")
    assert ctx.tool_calls[-1]["message"] == result.message
    if status == "ok":
        assert result.data[0].fyp == "第十四个五年规划"
    elif status == "empty":
        assert "地区：甲；规划期：第十四个五年规划" in result.message


@pytest.mark.parametrize("planid,section,status", [
    ("TEST-003", None, "no_text"), ("missing", None, "not_found"),
    ("TEST-001", 99, "not_found"), ("TEST-001", -1, "invalid_argument"),
    ("bad/id", 0, "invalid_argument"), ("TEST-001", None, "ok"), ("TEST-001", 0, "ok"),
])
async def test_read_status(ctx, planid, section, status):
    result = await invoke(kb_read, ctx, planid=planid, section_idx=section)
    assert result.status == status
    assert bool(ctx.evidence.ids()) == (status == "ok" and section is not None)
    assert ctx.tool_calls[-1]["message"] == result.message


@pytest.mark.parametrize("fail,delay,status", [("search", 0, "backend_error"), (None, .08, "timeout")])
async def test_failure_and_timeout(ctx, fail, delay, status):
    ctx.settings = replace(ctx.settings, tool_timeout=2 if fail else .02)
    ctx.kb = MockKB(ctx.settings.mock_kb_path, fail=fail, delay=delay)
    result = await invoke(kb_search, ctx, query="农业", area=None, fyp=None, top_k=8)
    assert result.status == status and not ctx.evidence.ids()
    assert ctx.tool_calls[-1]["status"] == status


@pytest.mark.parametrize("args", [
    ("农业", None, None, 0), ("农业", None, None, 21), ("农业", "长"*51, None, 8),
    ("农业", None, "长"*51, 8), ("长"*201, None, None, 8),
])
async def test_rejected_arguments_do_not_call_backend(ctx, args):
    def forbidden(*args):
        pytest.fail("invalid arguments reached backend")
    result = ToolResult.model_validate_json(await _call(ctx, "kb_search", forbidden, *args))
    assert result.status == "invalid_argument"


async def test_conflict_and_per_run_evidence(ctx):
    await invoke(kb_search, ctx, query="农业", area="甲", fyp=None, top_k=1)
    first = ctx.evidence.get(ctx.evidence.ids()[0])
    ctx.evidence.register(first.model_copy(update={"planid": "forged"}))
    # 冲突证据整体不可用：记冲突，且不能被继续引用
    assert ctx.evidence.get(first.chunk_id) is None
    assert first.chunk_id in ctx.evidence.conflicts
    assert ctx.evidence.conflicts == [first.chunk_id]
    other = RunContext(ctx.settings, ctx.kb, "run_other", "s_test")
    assert not other.evidence.ids() and not other.tool_calls


async def test_missing_plan_with_section_and_default_top_k(ctx):
    result = await invoke(kb_read, ctx, planid="missing", section_idx=0)
    assert result.message == "未找到 planid=missing 的篇目"
    ctx.settings = replace(ctx.settings, top_k_default=1)
    result = await invoke(kb_search, ctx, query="农业", area="甲", fyp=None)
    assert len(result.data) == 1


async def test_normalized_parameters_reach_backend(ctx):
    seen = []
    def backend(*args):
        seen.append(args)
        return []
    await _call(ctx, "kb_search", backend, " 农业 ", "甲", '“十四五”', 3)
    assert seen == [("农业", "甲", "第十四个五年规划", 3)]


class _WrongKB:
    """后端返回与操作不匹配的类型，用于验证工具层拒绝。"""

    name = "wrong"

    def search(self, *args, **kwargs):
        return Passage(planid="X", title="X", area=None, fyp=None, admin_level=None,
                       plantype_label=None, section_idx=0, section_path="p", content="c",
                       char_start=0, char_end=1)

    def read(self, *args, **kwargs):
        return [Hit(planid="Y", chunk_id="Y-1", title="Y", area=None, fyp=None, admin_level=None,
                    plantype_label=None, section_idx=0, section_path="p", content="c",
                    char_start=0, char_end=1, score=1.0)]


async def test_wrong_return_type_is_rejected(ctx):
    bad = _WrongKB()
    for name, fn, args in (("kb_search", bad.search, ("农业", None, None, 3)),
                           ("kb_read", bad.read, ("Y", 0, 6000))):
        run = RunContext(ctx.settings, bad, ctx.run_id, ctx.session_id)
        out = json.loads(await _call(run, name, fn, *args))
        assert out["status"] == "backend_error", name
        assert run.evidence.ids() == [], name


async def test_evidence_failure_is_not_masked_as_backend_error(ctx):
    def boom(*args, **kwargs):
        raise AttributeError("EvidenceRegistry.add_result 内部错误")
    ctx.evidence.add_result = boom
    with pytest.raises(AttributeError):
        await _call(ctx, "kb_search", ctx.kb.search, "农业", None, None, 3)

@pytest.mark.parametrize("area,fyp,expected_area,expected_fyp", [
    ("", None, None, None),
    ("   ", None, None, None),
    ("甲市", None, "甲市", None),
    (None, "", None, None),
    (None, "   ", None, None),
    (None, "十四五", None, "第十四个五年规划"),
])
async def test_search_filters_are_normalized(ctx, area, fyp, expected_area, expected_fyp):
    seen = []

    def backend(*args):
        seen.append(args)
        return []

    await _call(ctx, "kb_search", backend, "农业", area, fyp, 3)
    assert seen == [("农业", expected_area, expected_fyp, 3)]


async def test_blank_filters_show_unspecified_in_empty_message(ctx):
    def backend(*args):
        return []

    result = ToolResult.model_validate_json(
        await _call(ctx, "kb_search", backend, "农业", "", "   ", 3))
    assert result.status == "empty"
    assert "地区：未指定" in result.message
    assert "规划期：未指定" in result.message
