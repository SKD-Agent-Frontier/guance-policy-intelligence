"""证据坐标、版本与已见范围：proposal-v0.3 的确定性校验。

覆盖两件事：
1. 公开工具入口（SDK 层）必须真的能传 chunk_id / offset；
2. 证据登记必须做坐标自洽、章节包含、版本一致与 observed_ranges 合并。
"""
import json
from dataclasses import replace

from agents.tool_context import ToolContext

from guance_agent.config import ROOT, Settings
from guance_agent.context import EvidenceRegistry, RunContext, merge_ranges
from guance_agent.kb import get_kb
from guance_agent.schemas import EvidenceRef, ToolResult
from guance_agent.tools import kb_read

SHORT_CID = "chunk_c5481643e7c67d653e29160c30719104"
LONG_CID = "chunk_594a17c56902e7fdac2a38662282ebda"


def sample_settings():
    base = Settings(session_path=ROOT / "data/t.sqlite", run_log=ROOT / "data/t.jsonl")
    return replace(base, kb_backend="sample")


async def invoke(ctx, **args):
    raw = await kb_read.on_invoke_tool(
        ToolContext(context=ctx, tool_name=kb_read.name, tool_call_id="t",
                    tool_arguments=json.dumps(args)), json.dumps(args))
    return ToolResult.model_validate_json(raw)


async def test_kb_read_tool_exposes_chunk_id_and_offset_via_sdk():
    """必须走 SDK 工具入口验证：内部 _call 通了不代表模型能调到。"""
    s = sample_settings()
    ctx = RunContext(s, get_kb(s), "run", "sess")
    page1 = await invoke(ctx, planid="48787", chunk_id=LONG_CID)
    assert page1.status == "ok"
    assert page1.data.read_scope == "chunk"
    assert page1.data.next_offset == 6000
    page2 = await invoke(ctx, planid="48787", chunk_id=LONG_CID, offset=page1.data.next_offset)
    assert page2.status == "ok" and page2.data.next_offset is None
    assert len(page1.data.content) + len(page2.data.content) == 6134


async def test_kb_read_tool_rejects_section_and_chunk_together():
    s = sample_settings()
    ctx = RunContext(s, get_kb(s), "run", "sess")
    result = await invoke(ctx, planid="20699", section_idx=4, chunk_id="chunk_x")
    assert result.status == "invalid_argument"


async def test_samplekb_section_page_keeps_original_pagination():
    """章节样例只覆盖第一页：不能把这一页当成整章。"""
    s = sample_settings()
    ctx = RunContext(s, get_kb(s), "run", "sess")
    page = await invoke(ctx, planid="20699", section_idx=4)
    assert page.status == "ok"
    assert page.data.truncated is True
    assert page.data.next_offset == 6000
    assert len(page.data.chunks) == 10
    assert any(not c.complete for c in page.data.chunks)
    beyond = await invoke(ctx, planid="20699", section_idx=4, offset=6000)
    assert beyond.status != "ok", "未提供的后续页必须明确失败，而不是返回空成功"


def make_ref(**kw):
    """一条带完整块与章节坐标的证据。"""
    base = dict(planid="20699", chunk_id=SHORT_CID,
                title="\u82cf\u5dde\u5e02\u201c\u5341\u56db\u4e94\u201d\u6c11\u653f\u4e8b\u4e1a\u53d1\u5c55\u89c4\u5212",
                area="\u82cf\u5dde\u5e02", fyp="\u7b2c\u5341\u56db\u4e2a\u4e94\u5e74\u89c4\u5212",
                admin_level="\u5e02", plantype_label="\u4e13\u9879\u89c4\u5212",
                section_idx=4, section_path="\u7b2c\u56db\u90e8\u5206 \u4e3b\u8981\u4efb\u52a1",
                char_start=10864, char_end=11164,
                chunk_char_start=10864, chunk_char_end=11315,
                section_char_start=8286, section_char_end=19608,
                document_version_id="v1", generation_id="g1", source_hash="h1",
                offset_basis="body_first_non_whitespace", offset_unit="unicode_codepoint",
                source="kb_search")
    return EvidenceRef(**{**base, **kw})


def test_rejects_inverted_block_range():
    reg = EvidenceRegistry()
    reg.register(make_ref(chunk_char_start=11315, chunk_char_end=10864))
    assert reg.get(SHORT_CID) is None
    assert reg.rejected and "\u5012\u7f6e" in reg.rejected[0]["reason"]


def test_rejects_preview_outside_block():
    reg = EvidenceRegistry()
    reg.register(make_ref(char_end=12000))
    assert reg.rejected and "\u8d8a\u51fa\u5b8c\u6574\u5757" in reg.rejected[0]["reason"]


def test_rejects_block_outside_section():
    reg = EvidenceRegistry()
    reg.register(make_ref(chunk_char_end=20000))
    assert reg.rejected and "\u8d8a\u51fa\u6240\u5c5e\u7ae0\u8282" in reg.rejected[0]["reason"]


def test_rejects_unknown_coordinate_basis():
    reg = EvidenceRegistry()
    reg.register(make_ref(offset_basis="file_absolute"))
    assert reg.rejected and "\u5750\u6807\u57fa\u51c6" in reg.rejected[0]["reason"]


def test_version_conflict_invalidates_chunk():
    reg = EvidenceRegistry()
    reg.register(make_ref())
    reg.register(make_ref(document_version_id="v2"))
    assert reg.conflicts == [SHORT_CID]
    assert reg.get(SHORT_CID) is None


def test_merge_ranges_keeps_real_gaps():
    assert merge_ranges([(0, 10), (20, 30)]) == [(0, 10), (20, 30)]
    assert merge_ranges([(0, 10), (10, 20)]) == [(0, 20)]
    assert merge_ranges([(0, 10), (5, 20)]) == [(0, 20)]


def test_observed_ranges_merge_across_pages():
    """\u540c\u4e00\u5757\u5206\u4e24\u9875\u8bfb\uff1a\u5df2\u89c1\u8303\u56f4\u5408\u5e76\u4e3a\u6574\u5757\u3002"""
    reg = EvidenceRegistry()
    base = dict(planid="48787", chunk_id=LONG_CID,
                title="\u676d\u5dde\u5e02\u5546\u8d38\u670d\u52a1\u7b2c\u5341\u4e8c\u4e2a\u4e94\u5e74\u89c4\u5212",
                area="\u676d\u5dde\u5e02", fyp=None, admin_level=None, plantype_label=None,
                section_idx=37, section_path="\u516d\u3001\u4fdd\u969c\u63aa\u65bd",
                chunk_char_start=14681, chunk_char_end=20815, source="kb_read")
    reg.register(EvidenceRef(**base, char_start=14681, char_end=20681))
    assert reg.get(LONG_CID).complete is False
    reg.register(EvidenceRef(**base, char_start=20681, char_end=20815))
    merged = reg.get(LONG_CID)
    assert merged.observed_ranges == [(14681, 20815)]
    assert merged.complete is True
