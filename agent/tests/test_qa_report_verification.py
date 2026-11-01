import json

import pytest
from pydantic import ValidationError

from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.output import build_result, rebuild_trusted_answer
from guance_agent.qa_report_verification import finalize_report
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import AgentOutput, Claim, QABlock, QAReportDraft
from guance_agent.verify import verify_quotes


def context(env):
    settings, task = env
    ctx = RunContext(settings, get_kb(settings), task.run_id, task.session_id)
    ctx.evidence.add_result(ctx.kb.search("农业", "甲", None, 1), "kb_search")
    return ctx


def report(claims, *, dimensions=None, blocks=None):
    dimensions = dimensions or ["政策工具", "适用对象"]
    if blocks is None:
        blocks = [
            QABlock(id="fact-1", title_path=["总体情况"], dimension="政策工具",
                    kind="fact", text="甲市支持农业发展。", supporting_claim_indexes=[0]),
            QABlock(id="analysis-1", title_path=["总体情况", "比较"], dimension="适用对象",
                    kind="analysis", text="两项事实表明政策安排有所差异。",
                    supporting_claim_indexes=[0, 1]),
        ]
    return QAReportDraft(dimensions=dimensions, blocks=blocks)


class FakeRuntime:
    def __init__(self, settings, response):
        self.settings = settings
        self.hooks = type("Hooks", (), {"turns": 1})()
        self.response = response
        self.calls = []

    async def complete_text(self, messages, *, max_tokens, timeout):
        self.calls.append((messages, max_tokens, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_report_schema_and_build_make_report_the_answer(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claims = [Claim(text="甲市支持农业发展。", chunk_ids=[ref.chunk_id]),
              Claim(text="乙市支持农产品加工。", chunk_ids=[ref.chunk_id])]
    draft = report(claims)

    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="模型夹带的整篇分析，不可展示。", claims=claims, report=draft), None), ctx, {})

    assert result._qa_report_supplied
    assert result._qa_report_dimensions == ["政策工具", "适用对象"]
    assert "甲市支持农业发展。" in result.answer
    assert "整篇分析" not in result.answer
    assert result.structured is None
    assert result._qa_report_candidates[1]["support_signatures"] == [
        (claims[0].text, (ref.chunk_id,)), (claims[1].text, (ref.chunk_id,))]


def test_invalid_report_is_fail_closed_and_never_uses_raw_answer(env):
    ctx = context(env)
    output = AgentOutput.model_construct(answer="未经审查整篇报告", claims=[], report=None)
    result = build_result(env[1], ExecOutcome(output, None), ctx, {})
    result._qa_report_supplied = True
    result._qa_report_dimensions = ["政策工具"]
    result._qa_report_candidates = []
    rebuild_trusted_answer(result, ctx, "quote_verification")
    assert "未经审查" not in result.answer
    assert result.answer.startswith("本轮未形成可核验的报告内容")


def test_report_dimensions_and_block_limits_are_validated():
    with pytest.raises(ValidationError):
        QAReportDraft(dimensions=[str(i) for i in range(9)], blocks=[])
    with pytest.raises(ValidationError):
        QAReportDraft(dimensions=["政策工具", "政策工具"], blocks=[])
    with pytest.raises(ValidationError):
        QABlock(id="x", title_path=["总"], dimension="政策工具", kind="analysis",
                text="分析超过支持事实。", supporting_claim_indexes=[0])


def test_pruning_reindexes_after_removing_fact_without_rebinding(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claims = [Claim(text="将被删除的事实。", chunk_ids=["invalid"]),
              Claim(text="保留的事实甲。", chunk_ids=[ref.chunk_id]),
              Claim(text="保留的事实乙。", chunk_ids=[ref.chunk_id])]
    draft = QAReportDraft(dimensions=["政策工具"], blocks=[
        QABlock(id="removed", title_path=["总览"], dimension="政策工具", kind="fact",
                text="已撤回的内容。", supporting_claim_indexes=[0]),
        QABlock(id="retained", title_path=["总览"], dimension="政策工具", kind="analysis",
                text="甲乙两条事实有差异。", supporting_claim_indexes=[1, 2]),
    ])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="模型原文含全部段落", claims=claims, report=draft), None), ctx, {})
    assert "已撤回的内容" not in result.answer
    assert "甲乙两条事实有差异。" in result.answer
    assert result.meta["qa_report"]["missing_dimensions"] == []
    assert [block["id"] for block in result._qa_report_candidates] == ["retained"]


@pytest.mark.asyncio
async def test_finalize_fails_closed_when_semantic_review_cannot_run(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claims = [Claim(text="甲市支持农业。", chunk_ids=[ref.chunk_id], quotes=["甲市支持农业"]),
              Claim(text="乙市支持加工。", chunk_ids=[ref.chunk_id], quotes=["乙市支持加工"])]
    draft = report(claims)
    result = build_result(env[1], ExecOutcome(AgentOutput(answer="不可直接使用", claims=claims,
                                                            report=draft), None), ctx, {})
    result.meta["quote_verification"] = {"checked": 2, "approved": 2, "unverified": 0}
    runtime = FakeRuntime(env[0], TimeoutError())

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert result.structured is None or not result.structured.get("blocks")
    assert result.meta["qa_report"]["semantic_review"] == "timeout"
    assert "不可直接使用" not in result.answer


@pytest.mark.asyncio
async def test_finalize_accepts_only_explicitly_supported_review_items(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claims = [Claim(text="甲市支持农业。", chunk_ids=[ref.chunk_id], quotes=["甲市支持农业"]),
              Claim(text="乙市支持加工。", chunk_ids=[ref.chunk_id], quotes=["乙市支持加工"])]
    draft = report(claims)
    result = build_result(env[1], ExecOutcome(AgentOutput(answer="模型原话", claims=claims,
                                                            report=draft), None), ctx, {})
    result.meta["quote_verification"] = {"checked": 2, "approved": 2, "unverified": 0}
    runtime = FakeRuntime(env[0], json.dumps({
        "facts": [{"index": 0, "supported": True}, {"index": 1, "supported": True}],
        "blocks": [{"id": "fact-1", "supported": True},
                   {"id": "analysis-1", "supported": False}],
    }, ensure_ascii=False))

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert [block["id"] for block in result.structured["blocks"]] == ["fact-1"]
    assert result.structured["blocks"][0]["text"] == draft.blocks[0].text
    assert result.meta["qa_report"]["withdrawn"] == 1
    assert result.meta["qa_report"]["missing_dimensions"] == ["适用对象"]
    assert result.evidence_status == "insufficient"


@pytest.mark.asyncio
async def test_unquoted_block_is_withdrawn_locally_and_coverage_stays_partial(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claims = [Claim(text="已核验事实。", chunk_ids=[ref.chunk_id], quotes=["已核验事实"]),
              Claim(text="缺少引句的事实。", chunk_ids=[ref.chunk_id])]
    draft = QAReportDraft(dimensions=["政策工具"], blocks=[
        QABlock(id="verified", title_path=["总览"], dimension="政策工具", kind="fact",
                text="报告事实保持原文。", supporting_claim_indexes=[0]),
        QABlock(id="unverified", title_path=["总览"], dimension="政策工具", kind="fact",
                text="缺少引句的段落。", supporting_claim_indexes=[1]),
    ])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="必须被忽略的原始整篇答案", claims=claims, report=draft), None), ctx, {})
    result.meta["quote_verification"] = {"checked": 1, "approved": 1, "unverified": 0}
    runtime = FakeRuntime(env[0], json.dumps({
        "facts": [{"index": 0, "supported": True}],
        "blocks": [{"id": "verified", "supported": True}],
    }, ensure_ascii=False))

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert [block["id"] for block in result.structured["blocks"]] == ["verified"]
    assert result.structured["coverage"]["status"] == "partial"
    assert "部分报告段落未通过审核" in result.answer
    assert "缺少引句的段落。" not in result.answer
    assert result.meta["qa_report"]["withdrawn"] == 1
    assert runtime.calls[0][1] <= 2000


@pytest.mark.asyncio
async def test_real_quote_verifier_removes_one_dependency_and_keeps_independent_fact(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    ref.observed_ranges = [(ref.char_start, ref.char_end)]
    ctx.kb.verify_quote = lambda _cid, quote, _ranges, **_kwargs: quote == "good"
    claims = [Claim(text="甲市支持农业。", chunk_ids=[ref.chunk_id], quotes=["good"]),
              Claim(text="乙市支持加工。", chunk_ids=[ref.chunk_id], quotes=["bad"])]
    draft = QAReportDraft(dimensions=["政策工具"], blocks=[
        QABlock(id="good", title_path=["事实"], dimension="政策工具", kind="fact",
                text="甲市支持农业。", supporting_claim_indexes=[0]),
        QABlock(id="depends-on-bad", title_path=["分析"], dimension="政策工具", kind="analysis",
                text="两地安排不同。", supporting_claim_indexes=[0, 1]),
    ])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="模型原文不作为报告", claims=claims, report=draft), None), ctx, {})

    await verify_quotes(result, ctx)
    assert [claim.text for claim in result.claims] == [claims[0].text]
    assert [block["id"] for block in result._qa_report_candidates] == ["good"]
    runtime = FakeRuntime(env[0], json.dumps({
        "facts": [{"index": 0, "supported": True}],
        "blocks": [{"id": "good", "supported": True}],
    }, ensure_ascii=False))
    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert [block["id"] for block in result.structured["blocks"]] == ["good"]
    assert "模型原文不作为报告" not in result.answer


@pytest.mark.asyncio
async def test_unsupported_fact_withdraws_dependent_analysis_even_when_block_verdict_true(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claims = [Claim(text="不受支持的事实。", chunk_ids=[ref.chunk_id], quotes=["引句甲"]),
              Claim(text="受支持的事实。", chunk_ids=[ref.chunk_id], quotes=["引句乙"])]
    draft = QAReportDraft(dimensions=["政策工具"], blocks=[
        QABlock(id="supported-fact", title_path=["事实"], dimension="政策工具", kind="fact",
                text="受支持的事实。", supporting_claim_indexes=[1]),
        QABlock(id="analysis", title_path=["分析"], dimension="政策工具", kind="analysis",
                text="事实甲乙形成比较。", supporting_claim_indexes=[0, 1]),
    ])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="raw", claims=claims, report=draft), None), ctx, {})
    result.meta["quote_verification"] = {"checked": 2, "approved": 2, "unverified": 0}
    runtime = FakeRuntime(env[0], json.dumps({
        "facts": [{"index": 0, "supported": False}, {"index": 1, "supported": True}],
        "blocks": [{"id": "supported-fact", "supported": True},
                   {"id": "analysis", "supported": True}],
    }, ensure_ascii=False))

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert [block["id"] for block in result.structured["blocks"]] == ["supported-fact"]
    assert "事实甲乙形成比较。" not in result.answer
    assert [claim.text for claim in result.claims] == [claims[1].text]
    assert [ref.chunk_id for ref in result.evidence_refs] == [ref.chunk_id]
    assert result.structured["blocks"][0]["supporting_claim_indexes"] == [0]


def test_duplicate_non_boolean_and_missing_verdicts_fail_only_matching_items():
    from guance_agent.qa_report_verification import _parse_review

    verdicts = _parse_review(json.dumps({
        "facts": [{"index": 1, "supported": True}, {"index": 1, "supported": False},
                  {"index": 2, "supported": 1}],
        "blocks": [{"id": "a", "supported": True}, {"id": "a", "supported": False}],
    }), {1, 2, 3}, {"a", "b"})

    assert verdicts == ({}, {}, {1, 2, 3}, {"a", "b"}, False)


@pytest.mark.asyncio
async def test_review_budget_skip_makes_no_model_call(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claims = [Claim(text="支持农业。", chunk_ids=[ref.chunk_id], quotes=["支持农业"])]
    draft = QAReportDraft(dimensions=["政策工具"], blocks=[
        QABlock(id="fact", title_path=["事实"], dimension="政策工具", kind="fact",
                text="支持农业。", supporting_claim_indexes=[0])])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="raw", claims=claims, report=draft), None), ctx, {})
    result.meta["quote_verification"] = {"checked": 1, "approved": 1, "unverified": 0}
    runtime = FakeRuntime(env[0], "unused")
    runtime.hooks.turns = env[0].max_turns

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert not runtime.calls
    assert not result.structured["blocks"]
    assert result.meta["qa_report"]["reason"] == "review_budget_unavailable"


@pytest.mark.asyncio
async def test_unbound_block_number_is_withdrawn_without_losing_same_dimension_peer(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claims = [Claim(text="农业支持继续推进。", chunk_ids=[ref.chunk_id], quotes=["农业支持"]),
              Claim(text="加工支持继续推进。", chunk_ids=[ref.chunk_id], quotes=["加工支持"])]
    draft = QAReportDraft(dimensions=["政策工具"], blocks=[
        QABlock(id="plain", title_path=["现状"], dimension="政策工具", kind="fact",
                text="农业支持继续推进。", supporting_claim_indexes=[0]),
        QABlock(id="numbered", title_path=["补贴3000元"], dimension="政策工具", kind="fact",
                text="加工支持继续推进。", supporting_claim_indexes=[1]),
    ])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="raw", claims=claims, report=draft), None), ctx, {})
    result.meta["quote_verification"] = {"checked": 2, "approved": 2, "unverified": 0}
    runtime = FakeRuntime(env[0], json.dumps({
        "facts": [{"index": 0, "supported": True}],
        "blocks": [{"id": "plain", "supported": True}],
    }, ensure_ascii=False))

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert [block["id"] for block in result.structured["blocks"]] == ["plain"]
    assert result.structured["coverage"]["status"] == "partial"
