"""proposal-v0.3 契约测试：用 RAG 交付的真实样例验证 Agent 侧能吃下该结构。

这些断言的意义：RAG 侧字段一旦变化，这里立刻失败，而不是等到生产才发现字段被丢。
样例目录 contracts/rag_samples/ 来自 RAG 团队交付的真实检索结果投影。
"""
import json
from dataclasses import replace

import pytest

from guance_agent.config import ROOT, Settings
from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.schemas import Hit, Passage
from guance_agent.tools import _call

SAMPLES = ROOT / "contracts/rag_samples"

# proposal-v0.3 要求真实后端必须给全的来源字段
REQUIRED_SOURCE = ("document_id", "document_version_id", "generation_id", "source_hash",
                   "offset_basis", "offset_unit", "body_origin", "contract_version",
                   "section_id", "section_char_start", "section_char_end")
REQUIRED_SECTION_RANGE = ()


def load(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def sample_settings():
    return replace(Settings(session_path=ROOT / "data/t.sqlite",
                            run_log=ROOT / "data/t.jsonl"), kb_backend="sample")


def test_hit_sample_maps_without_field_loss():
    hit = Hit.model_validate(load("hit.json"))
    assert hit.chunk_id.startswith("chunk_")
    assert None not in [getattr(hit, f) for f in REQUIRED_SOURCE]
    assert hit.contract_version == "proposal-v0.3"
    assert hit.offset_basis == "body_first_non_whitespace"
    assert hit.offset_unit == "unicode_codepoint"
    # 预览范围必须落在完整块内
    assert hit.chunk_char_start <= hit.char_start <= hit.char_end <= hit.chunk_char_end
    # 坐标与文本长度必须自洽（右开区间）
    assert hit.char_end - hit.char_start == len(hit.content)
    assert hit.truncated is True


def test_passage_chunk_sample_returns_full_block():
    p = Passage.model_validate(load("passage_chunk.json"))
    assert p.read_scope == "chunk"
    assert p.is_index is False
    assert p.truncated is False and p.next_offset is None
    assert p.chunk_char_end - p.chunk_char_start == len(p.content) == 451
    assert p.chunks and p.chunks[0].complete is True
    assert None not in [getattr(p, f) for f in REQUIRED_SOURCE + REQUIRED_SECTION_RANGE]


def test_passage_section_sample_marks_partial_chunks():
    p = Passage.model_validate(load("passage_section.json"))
    assert p.read_scope == "section"
    assert p.truncated is True and p.next_offset == 6000
    partial = [c for c in p.chunks if not c.complete]
    assert partial, "章节页里出现半块时必须标 complete=false"
    for c in p.chunks:
        assert c.chunk_char_start <= c.char_start <= c.char_end <= c.chunk_char_end


def test_long_chunk_pages_are_contiguous_and_reconstruct():
    p1 = Passage.model_validate(load("passage_long_page1.json"))
    p2 = Passage.model_validate(load("passage_long_page2.json"))
    assert p1.chunk_id == p2.chunk_id
    assert p1.next_offset == 6000 and p2.next_offset is None
    assert p1.char_end == p2.char_start, "分页边界必须紧接"
    full = p1.chunk_char_end - p1.chunk_char_start
    assert len(p1.content) + len(p2.content) == full == 6134


async def test_samplekb_serves_contract_shaped_responses():
    kb = get_kb(sample_settings())
    assert kb.name == "sample"
    hit = kb.search("养老服务", None, None, 1)[0]
    assert hit.chunk_id == "chunk_c5481643e7c67d653e29160c30719104"
    full = kb.read(hit.planid, None, 6000, chunk_id=hit.chunk_id)
    assert full.read_scope == "chunk" and len(full.content) == 451
    assert full.chunk_char_start <= full.char_start <= full.char_end <= full.chunk_char_end


async def test_kb_read_tool_passes_chunk_id_and_paginates():
    """工具层必须把 chunk_id / offset 透传到后端，而不是只改函数签名。"""
    s = sample_settings()
    ctx = RunContext(s, get_kb(s), "run", "sess")
    hit = json.loads((await _call(ctx, "kb_search", ctx.kb.search, "养老服务", None, None, 1)))
    assert hit["status"] == "ok"
    chunk_id = hit["data"][0]["chunk_id"]

    first = json.loads(await _call(ctx, "kb_read", ctx.kb.read, "20699", None, 6000,
                                   chunk_id=chunk_id))
    assert first["status"] == "ok" and first["data"]["read_scope"] == "chunk"

    # 续读：把 next_offset 原样回传
    long_id = "chunk_594a17c56902e7fdac2a38662282ebda"
    page1 = json.loads(await _call(ctx, "kb_read", ctx.kb.read, "48787", None, 6000,
                                   chunk_id=long_id))
    assert page1["data"]["next_offset"] == 6000
    page2 = json.loads(await _call(ctx, "kb_read", ctx.kb.read, "48787", None, 6000,
                                   chunk_id=long_id, offset=page1["data"]["next_offset"]))
    assert page2["status"] == "ok" and page2["data"]["next_offset"] is None
    assert len(page1["data"]["content"]) + len(page2["data"]["content"]) == 6134


async def test_evidence_carries_version_fields():
    """证据必须带版本与坐标基准，否则将来无法判断引用是否过期。"""
    s = sample_settings()
    ctx = RunContext(s, get_kb(s), "run", "sess")
    await _call(ctx, "kb_search", ctx.kb.search, "养老服务", None, None, 1)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    assert ref.document_id and ref.generation_id and ref.source_hash
    assert ref.offset_basis == "body_first_non_whitespace"
    assert ref.section_char_end is not None


@pytest.mark.parametrize("name", ["hit.json", "passage_chunk.json", "passage_section.json",
                                  "passage_long_page1.json", "passage_long_page2.json"])
def test_samples_exist_and_parse(name):
    """样例必须随仓库交付，不能引用外部路径，否则 CI 跑不了。"""
    assert (SAMPLES / name).exists(), f"缺少样例 {name}"
    assert isinstance(load(name), dict)
