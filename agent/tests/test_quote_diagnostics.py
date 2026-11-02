"""Round 2: quote failure diagnostics and fact-retention accounting."""
from types import SimpleNamespace

import pytest

from guance_agent.qa_controlled import claim_budget, messages_for
from guance_agent.schemas import Claim, RunResult
from guance_agent.verify import verify_quotes


class Registry:
    def __init__(self, refs):
        self.refs = {ref.chunk_id: ref for ref in refs}

    def get(self, chunk_id):
        return self.refs.get(chunk_id)

    def ids(self):
        return list(self.refs)


def _ref(chunk_id="c1", planid="2561", document_id="doc", generation_id="v1",
         ranges=((0, 200),)):
    return SimpleNamespace(chunk_id=chunk_id, planid=planid, document_id=document_id,
                           generation_id=generation_id, observed_ranges=list(ranges),
                           title="测试政策", area="沧州市", fyp="第十个五年计划")


def _ctx(refs, source_by_chunk, *, checker=None, log_content=False):
    sources = dict(source_by_chunk)
    calls = {"source": 0, "verify": 0}

    def get_source(chunk_id, **kwargs):
        calls["source"] += 1
        return SimpleNamespace(content=sources[chunk_id], chunk_char_start=0)

    def verify_quote(chunk_id, quote, ranges, **kwargs):
        calls["verify"] += 1
        if checker is not None:
            return checker(chunk_id, quote, ranges, **kwargs)
        content = sources[chunk_id]
        return any(quote in content[start:end] for start, end in ranges)

    return SimpleNamespace(
        settings=SimpleNamespace(tool_timeout=1, log_content=log_content),
        evidence=Registry(refs),
        kb=SimpleNamespace(get_chunk_source=get_source, verify_quote=verify_quote),
        question="沧州市十五计划产业结构调整措施",
        scope=("2561",), route=None, tool_calls=[], calls=calls,
    )


def _result(*claims):
    return RunResult(run_id="r", session_id="s", status="completed",
                     evidence_status="verified", answer="", claims=list(claims),
                     evidence_refs=[], dropped_chunk_ids=[], error=None, meta={})


@pytest.fixture(autouse=True)
def no_rebuild(monkeypatch):
    monkeypatch.setattr("guance_agent.verify.rebuild_trusted_answer",
                        lambda result, ctx, reason: None)


@pytest.mark.asyncio
async def test_exact_quote_passes_and_retention_metrics():
    source = "推进农业科技进步，扩大农业产业化经营。"
    ref = _ref()
    claim = Claim(text="推进农业科技进步", chunk_ids=["c1"],
                  quotes=["推进农业科技进步"])
    result = _result(claim)
    ctx = _ctx([ref], {"c1": source})
    assert await verify_quotes(result, ctx) == 0
    assert len(result.claims) == 1
    assert result.meta["quote_verification"]["approved"] == 1
    assert result.meta["claim_retention"]["claim_retention_rate"] == 1.0
    assert result.meta["quote_diagnostics"] == []


@pytest.mark.asyncio
async def test_anchor_relocates_exact_sentence_after_paraphrased_quote():
    source = "推进农业科技进步，扩大农业产业化经营。"
    ref = _ref()
    claim = Claim(text="推进农业科技进步", chunk_ids=["c1"],
                  quotes=["推进农业科技发展"], anchors=["农业科技进步"])
    result = _result(claim)
    ctx = _ctx([ref], {"c1": source})
    assert await verify_quotes(result, ctx) == 0
    assert len(result.claims) == 1
    assert result.claims[0].quotes == ["推进农业科技进步，扩大农业产业化经营。"]
    assert result.meta["quotes_anchor_repaired"] == 1
    assert result.meta["quote_verification"]["approved"] == 1


@pytest.mark.asyncio
async def test_whitespace_difference_is_snapped_and_approved():
    source = "推进农业科技进步，扩大农业产业化经营。"
    claim = Claim(text="推进农业科技进步", chunk_ids=["c1"],
                  quotes=["推进 农业 科技 进步"])
    result = _result(claim)
    ctx = _ctx([_ref()], {"c1": source})
    await verify_quotes(result, ctx)
    assert len(result.claims) == 1
    assert result.claims[0].quotes == ["推进农业科技进步"]
    assert result.meta["quote_verification"]["approved"] == 1


@pytest.mark.asyncio
async def test_rewritten_quote_is_classified_as_not_in_chunk():
    source = "推进农业科技进步，扩大农业产业化经营。"
    claim = Claim(text="推进农业科技进步", chunk_ids=["c1"],
                  quotes=["农业科技持续进步"])
    result = _result(claim)
    ctx = _ctx([_ref()], {"c1": source})
    await verify_quotes(result, ctx)
    failures = result.meta["quote_diagnostics"]
    assert failures[0]["failure_category"] == "quote_not_in_chunk"
    assert failures[0]["status"] == "rejected"
    assert result.meta["claim_retention"]["claims_rejected"] == 1
    assert result.meta["claim_retention"]["claim_retention_rate"] == 0.0


@pytest.mark.asyncio
async def test_quote_from_other_chunk_and_policy_are_distinguished():
    refs = [_ref("c1", "2561"), _ref("c2", "9999")]
    claim = Claim(text="工业技术改造", chunk_ids=["c1"],
                  quotes=["工业技术改造"])
    result = _result(claim)
    ctx = _ctx(refs, {"c1": "甲政策只讲农业。", "c2": "乙政策讲工业技术改造。"})
    await verify_quotes(result, ctx)
    assert result.meta["quote_diagnostics"][0]["failure_category"] in {
        "quote_in_other_chunk", "quote_in_other_policy"}


@pytest.mark.asyncio
async def test_quote_outside_read_range_is_not_reported_as_absent():
    source = "第一段讲农业。第二段讲工业技术改造。"
    ref = _ref(ranges=((0, 7),))
    claim = Claim(text="工业技术改造", chunk_ids=["c1"],
                  quotes=["工业技术改造"])
    result = _result(claim)
    ctx = _ctx([ref], {"c1": source})
    await verify_quotes(result, ctx)
    assert result.meta["quote_diagnostics"][0]["failure_category"] == "quote_outside_read_range"


@pytest.mark.asyncio
async def test_invalid_evidence_ref_is_not_quote_absence():
    claim = Claim(text="工业技术改造", chunk_ids=["missing"], quotes=["工业技术改造"])
    result = _result(claim)
    ctx = _ctx([], {})
    await verify_quotes(result, ctx)
    assert result.meta["quote_diagnostics"][0]["failure_category"] == "invalid_evidence_ref"


@pytest.mark.asyncio
async def test_verifier_exception_is_not_reinterpreted_as_quote_absence():
    source = "推进农业科技进步，扩大农业产业化经营。"

    def explode(*args, **kwargs):
        raise RuntimeError("verifier unavailable")

    claim = Claim(text="推进农业科技进步", chunk_ids=["c1"],
                  quotes=["推进农业科技进步"])
    result = _result(claim)
    ctx = _ctx([_ref()], {"c1": source}, checker=explode)
    await verify_quotes(result, ctx)
    assert result.meta["quote_diagnostics"][0]["failure_category"] == "verification_error"


@pytest.mark.asyncio
async def test_partial_claim_failure_reports_generated_vs_retained():
    source = "推进农业科技进步，扩大农业产业化经营。"
    good = Claim(text="推进农业科技进步", chunk_ids=["c1"],
                 quotes=["推进农业科技进步"])
    bad = Claim(text="扩大农业产业化经营", chunk_ids=["c1"],
                quotes=["农业产业化经营"])  # actually present; make it rejected
    bad.quotes = ["农业产业化经营规模翻番"]
    result = _result(good, bad)
    ctx = _ctx([_ref()], {"c1": source})
    await verify_quotes(result, ctx)
    assert len(result.claims) == 1
    assert result.meta["claim_retention"]["claims_generated"] == 2
    assert result.meta["claim_retention"]["claims_retained"] == 1
    assert result.meta["claim_retention"]["claim_retention_rate"] == 0.5
    assert result.meta["claim_retention"]["quotes_rejected"] == 1


@pytest.mark.asyncio
async def test_diagnostic_quote_preview_only_when_opt_in():
    source = "推进农业科技进步，扩大农业产业化经营。"
    claim = Claim(text="推进农业科技进步", chunk_ids=["c1"],
                  quotes=["农业科技持续进步"])
    ctx_off = _ctx([_ref()], {"c1": source})
    result_off = _result(claim)
    await verify_quotes(result_off, ctx_off)
    assert "quote_preview" not in result_off.meta["quote_diagnostics"][0]

    claim_on = Claim(text="推进农业科技进步", chunk_ids=["c1"],
                     quotes=["农业科技持续进步"])
    ctx_on = _ctx([_ref()], {"c1": source}, log_content=True)
    result_on = _result(claim_on)
    await verify_quotes(result_on, ctx_on)
    assert result_on.meta["quote_diagnostics"][0]["quote_preview"] == "农业科技持续进步"


def test_claim_budget_is_task_adaptive_but_bounded():
    assert claim_budget("沧州市十五计划中产业布局的总体设想是什么？") == 3
    assert claim_budget("沧州市十五计划中产业结构调整的主要措施有哪些？") == 6
    assert claim_budget("请比较海西州与双流区在数字基础设施方面的异同。") == 8


def test_messages_carry_budget_and_exact_quote_instruction():
    task = SimpleNamespace(question="比较两篇政策的差异")
    evidence = [{"chunk_id": "c1", "planid": "p1", "title": "t",
                 "section_path": "s", "text": "政策原文。"}]
    messages = messages_for(task, evidence)
    user = messages[1]["content"]
    assert "最多输出 8 条 claims" in user
    assert "text 必须是原文直引" in user
