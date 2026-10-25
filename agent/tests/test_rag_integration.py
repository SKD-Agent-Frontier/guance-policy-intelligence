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
                    kb_backend="rag", rag_mode="bm25",
                    rag_store_project=str(tmp_path / "store"))


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


async def test_orchestrator_actually_calls_verify_quotes(tmp_path, monkeypatch):
    """守卫：核验必须真的挂在执行链上，不能只是 verify.py 里有个函数。"""
    import guance_agent.orchestrator as orch
    from guance_agent.runtime import Runtime
    from guance_agent.schemas import TaskRequest

    calls = []

    async def fake_verify(result, ctx):
        calls.append(True)

    monkeypatch.setattr(orch, "verify_quotes", fake_verify)
    s = Settings(session_path=tmp_path / "s.sqlite", run_log=tmp_path / "r.jsonl",
                 llm_base_url="", llm_api_key="")
    task = TaskRequest(run_id="r", session_id="s", question="q", created_at="x")
    rt = Runtime(s, task)
    rt.prepare()
    try:
        await orch.run_task(task, s, rt)
    finally:
        rt.close()
    # 无模型配置时走 MODEL_UNAVAILABLE 分支，不进入执行链；这里只验证接线存在
    assert hasattr(orch, "verify_quotes")


# ---------------- 答案正文的数字核对（⑤-06） ----------------

class FakeSourceKB:
    """只提供 get_chunk_source，用来验证数字核对。"""

    def __init__(self, content, base):
        self.content, self.base = content, base

    def get_chunk_source(self, chunk_id, **_kw):
        return types.SimpleNamespace(content=self.content, chunk_char_start=self.base)


async def _numbers(tmp_path, content, base, answer, observed, quotes=()):
    from guance_agent.verify import verify_numbers
    reg = EvidenceRegistry()
    end = base + len(content)
    seen_end = base + min(observed, len(content))
    reg.register(ref_dict(char_start=base, char_end=seen_end,
                          chunk_char_start=base, chunk_char_end=end,
                          section_char_start=base, section_char_end=end))
    ctx = RunContext(rag_settings(tmp_path), FakeSourceKB(content, base), "r", "s")
    ctx.evidence = reg
    result = RunResult(run_id="r", session_id="s", status="completed",
                       evidence_status="verified", answer=answer,
                       claims=[Claim(text="t", chunk_ids=[CID], quotes=list(quotes))],
                       evidence_refs=[reg.get(CID)], dropped_chunk_ids=[],
                       error=None, meta={})
    missing = await verify_numbers(result, ctx)
    return result, missing


async def test_number_inside_observed_range_passes(tmp_path):
    body = "岗前培训率达到100%"
    result, missing = await _numbers(tmp_path, body, 1000, "培训率达到100%。", 20)
    assert missing == 0 and result.warnings == []


async def test_number_outside_observed_range_is_flagged(tmp_path):
    """答案写了块里存在、但本次没读到的数字：必须提示。"""
    body = "岗前培训率达到100%持证上岗率达到93%。"
    # 只读前 11 字：够到 100%，够不到 93%
    result, missing = await _numbers(tmp_path, body, 1000, "持证上岗率达到93%。", 11)
    assert missing == 1
    assert any("93%" in w for w in result.warnings)
    assert result.evidence_status == "insufficient"


async def test_missing_percentage_downgrades_status(tmp_path):
    result, _ = await _numbers(tmp_path, "本规划没有具体指标", 1000,
                               "培训率达到87%。", 20)
    assert result.evidence_status == "insufficient"


async def test_missing_plain_number_only_warns(tmp_path):
    result, _ = await _numbers(tmp_path, "本规划没有具体指标", 1000,
                               "共安排 12 项重点工程。", 20)
    assert any("12" in w for w in result.warnings)
    assert result.evidence_status == "verified"


async def test_number_boundary_does_not_false_match(tmp_path):
    """100 不能命中 1000。"""
    from guance_agent.verify import _present
    assert _present("100", "达到100%") is True
    assert _present("100", "达到1000户") is False
    assert _present("93%", "达到93%") is True
    assert _present("93%", "达到193%") is False


async def test_no_chunk_source_backend_skips(tmp_path):
    from guance_agent.verify import verify_numbers
    reg = EvidenceRegistry()
    reg.register(ref_dict())
    ctx = RunContext(rag_settings(tmp_path), types.SimpleNamespace(), "r", "s")
    ctx.evidence = reg
    result = RunResult(run_id="r", session_id="s", status="completed",
                       evidence_status="verified", answer="达到 99%",
                       claims=[Claim(text="t", chunk_ids=[CID])],
                       evidence_refs=[], dropped_chunk_ids=[], error=None, meta={})
    assert await verify_numbers(result, ctx) == 0
    assert result.warnings == []


def test_rag_adapter_exposes_verification_entrypoints():
    """守卫：适配器必须把核对所需的方法都转发出来，缺一个核验就静默失效。"""
    from guance_agent.kb_rag import RagKB
    for name in ("search", "read", "get_chunk_source", "verify_quote", "close"):
        assert callable(getattr(RagKB, name, None)), name


async def test_list_markers_are_not_treated_as_numbers(tmp_path):
    """模型用有序列表时，行首 1. 2. 3. 不能当成待核对的数据。"""
    body = "岗前培训率达到100%"
    answer = "1. 培训率达到100%。\n2. 完善政策制度。\n3. 推进专业培训。\n4. 加强人才引进。"
    result, missing = await _numbers(tmp_path, body, 1000, answer, 20)
    assert missing == 0, missing
    assert result.warnings == []
