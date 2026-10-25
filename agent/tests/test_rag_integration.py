"""Agent 侧 RAG 集成：适配器、引文核验、越界降级、异常映射。

不依赖真实 guance_rag 包与运行数据，用假后端验证接线是否正确。
"""
import sys
import types

import pytest

from guance_agent.config import Settings
from guance_agent.context import EvidenceRegistry, RunContext
from guance_agent.kb import close_kb, get_kb, warm_up
from guance_agent.schemas import Claim, EvidenceRef, Hit, Passage, RunResult
from guance_agent.verify import verify_quotes

CID = "chunk_c5481643e7c67d653e29160c30719104"


def hit_dict(**kw):
    base = dict(planid="20699", chunk_id=CID, parent_id="parent_1", title="苏州市规划",
                area="苏州市", fyp="第十四个五年规划", admin_level="市",
                plantype_label="专项规划", section_id="section_a", section_idx=4,
                section_path="第四部分 主要任务", content="养老服务人才建设",
                char_start=10864, char_end=10874,
                chunk_char_start=10864, chunk_char_end=11315,
                section_char_start=8286, section_char_end=19608,
                truncated=True, score=1.0, source_type="text_chunk",
                document_id="doc_1", document_version_id="v1", generation_id="g1",
                source_hash="h1", offset_basis="body_first_non_whitespace",
                offset_unit="unicode_codepoint", body_origin=254,
                contract_version="proposal-v0.3")
    return {**base, **kw}


class FakeRagKB:
    """假的 RagKnowledgeBase：记录调用参数，返回 Agent 类型化前的原始对象。"""

    calls = []

    def __init__(self, options):
        self.options = options

    def search(self, query, area, fyp, top_k, *, source_types=None):
        FakeRagKB.calls.append(("search", source_types))
        return [types.SimpleNamespace(model_dump=lambda: hit_dict())]

    def read(self, planid, section_idx, max_chars, *, chunk_id=None, offset=0,
             document_id=None, generation_id=None):
        FakeRagKB.calls.append(("read", chunk_id, offset))
        data = hit_dict(read_scope="chunk", is_index=False, next_offset=None,
                        chunks=[], sections=None, contexts=[], media_sources=[],
                        required_relation_ids=[], quality_warnings=[])
        data.pop("score")
        data.setdefault("char_end", 11315)
        data["content"] = "x" * (data["char_end"] - data["char_start"])
        data["truncated"] = False
        return types.SimpleNamespace(model_dump=lambda: data)

    def verify_quote(self, chunk_id, quote, ranges, *, document_id=None, generation_id=None):
        FakeRagKB.calls.append(("verify", chunk_id, quote))
        return quote in "养老服务人才建设"

    def close(self):
        FakeRagKB.calls.append(("close",))


@pytest.fixture
def fake_rag(monkeypatch):
    """把假的 guance_rag 注入 sys.modules，避免真实依赖与数据。"""
    module = types.ModuleType("guance_rag")
    module.RagKnowledgeBase = FakeRagKB
    module.RuntimeOptions = lambda **kw: types.SimpleNamespace(**kw, resolved=True)
    monkeypatch.setitem(sys.modules, "guance_rag", module)
    FakeRagKB.calls = []
    monkeypatch.setattr("guance_agent.kb._RAG_KB", None)
    yield module
    close_kb()


def rag_settings(tmp_path):
    return Settings(session_path=tmp_path / "s.sqlite", run_log=tmp_path / "r.jsonl",
                    kb_backend="rag", rag_mode="bm25")


def test_rag_backend_searches_only_native_text(fake_rag, tmp_path):
    """首轮集成只取原生文本，不能把 OCR 和图片命中混进来。"""
    kb = get_kb(rag_settings(tmp_path))
    hits = kb.search("养老服务", "苏州市", None, 5)
    assert [c for c in FakeRagKB.calls if c[0] == "search"][0][1] == ["text_chunk"]
    assert isinstance(hits[0], Hit) and hits[0].chunk_id == CID


def test_rag_backend_maps_passage_and_passes_paging(fake_rag, tmp_path):
    kb = get_kb(rag_settings(tmp_path))
    passage = kb.read("20699", None, 6000, chunk_id=CID, offset=6000)
    assert isinstance(passage, Passage) and passage.read_scope == "chunk"
    assert ("read", CID, 6000) in FakeRagKB.calls


def test_warm_up_builds_once_and_close_releases(fake_rag, tmp_path):
    """真实 RAG 只能在启动时构建一次，请求路径里不能重复加载。"""
    s = rag_settings(tmp_path)
    warm_up(s)
    warm_up(s)
    first = get_kb(s)
    assert get_kb(s) is first
    assert sum(1 for c in FakeRagKB.calls if c[0] == "close") == 0
    close_kb()
    assert ("close",) in FakeRagKB.calls


def test_mock_backend_is_untouched_by_warm_up(tmp_path):
    s = Settings(session_path=tmp_path / "s.sqlite", run_log=tmp_path / "r.jsonl")
    warm_up(s)              # mock 后端下应为空操作
    assert get_kb(s).name == "mock"


def ref_dict(**kw):
    base = dict(planid="20699", chunk_id=CID, parent_id="parent_1", title="苏州市规划",
                area="苏州市", fyp=None, admin_level=None, plantype_label=None,
                section_id="section_a", section_idx=4, section_path="第四部分 主要任务",
                char_start=10864, char_end=10874,
                chunk_char_start=10864, chunk_char_end=11315,
                section_char_start=8286, section_char_end=19608,
                document_id="doc_1", document_version_id="v1", generation_id="g1",
                source_hash="h1", offset_basis="body_first_non_whitespace",
                offset_unit="unicode_codepoint", body_origin=254,
                contract_version="proposal-v0.3", quality_warnings=[], source="kb_search")
    return EvidenceRef(**{**base, **kw})


def test_backend_flagged_cross_section_is_accepted_but_downgraded():
    """后端已标 chunk_outside_section 时接受证据，但标成不可作单章节引用。"""
    reg = EvidenceRegistry()
    reg.register(ref_dict(chunk_char_end=20000,
                          quality_warnings=["chunk_outside_section"]))
    ref = reg.get(CID)
    assert ref is not None and ref.out_of_section is True


def test_unflagged_cross_section_is_still_rejected():
    """后端没标记却越界，视为数据错误，照旧拒绝。"""
    reg = EvidenceRegistry()
    reg.register(ref_dict(chunk_char_end=20000))
    assert reg.get(CID) is None
    assert reg.rejected and "越出所属章节" in reg.rejected[0]["reason"]


def test_cross_section_warning_reaches_result():
    from guance_agent.output import build_result
    from guance_agent.runner import ExecOutcome
    from guance_agent.schemas import TaskRequest
    task = TaskRequest(run_id="r", session_id="s", question="q", created_at="x")
    reg = EvidenceRegistry()
    reg.register(ref_dict(chunk_char_end=20000,
                          quality_warnings=["chunk_outside_section"]))
    ctx = types.SimpleNamespace(evidence=reg, tool_calls=[], settings=Settings(),
                                kb=types.SimpleNamespace())
    out = ExecOutcome('{"answer":"a","claims":[{"text":"t","chunk_ids":["%s"]}]}' % CID, None)
    result = build_result(task, out, ctx, {})
    assert any("越出其登记章节" in w for w in result.warnings)


class FakeKB:
    def __init__(self, ok):
        self.ok, self.seen = ok, []

    def verify_quote(self, chunk_id, quote, ranges, **_kw):
        self.seen.append((chunk_id, quote, list(ranges)))
        return self.ok


async def run_verify(tmp_path, ok, quotes):
    reg = EvidenceRegistry()
    reg.register(ref_dict())
    kb = FakeKB(ok)
    ctx = RunContext(rag_settings(tmp_path), kb, "r", "s")
    ctx.evidence = reg
    result = RunResult(run_id="r", session_id="s", status="completed",
                       evidence_status="verified", answer="a",
                       claims=[Claim(text="结论", chunk_ids=[CID], quotes=quotes)],
                       evidence_refs=[reg.get(CID)], dropped_chunk_ids=[],
                       error=None, meta={})
    await verify_quotes(result, ctx)
    return result, kb


async def test_verified_quote_is_kept(tmp_path):
    result, kb = await run_verify(tmp_path, True, ["养老服务人才建设"])
    assert result.claims[0].quotes == ["养老服务人才建设"]
    assert kb.seen and kb.seen[0][2] == [(10864, 10874)]


async def test_unverifiable_quote_is_dropped(tmp_path):
    result, _ = await run_verify(tmp_path, False, ["原文里没有这句话"])
    assert result.claims == []
    assert result.evidence_status == "insufficient"
    assert any("未能在已提供的原文范围内逐字核对" in w for w in result.warnings)


async def test_no_verify_quote_backend_skips_silently(tmp_path):
    """mock/sample 没有 verify_quote：跳过而不是假装核过。"""
    reg = EvidenceRegistry()
    reg.register(ref_dict())
    ctx = RunContext(rag_settings(tmp_path), types.SimpleNamespace(), "r", "s")
    ctx.evidence = reg
    result = RunResult(run_id="r", session_id="s", status="completed",
                       evidence_status="verified", answer="a",
                       claims=[Claim(text="结论", chunk_ids=[CID], quotes=["任意"])],
                       evidence_refs=[], dropped_chunk_ids=[], error=None, meta={})
    assert await verify_quotes(result, ctx) == 0
    assert result.claims[0].quotes == ["任意"]
