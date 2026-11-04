import json

import pytest
from pydantic import ValidationError

from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.output import build_result, rebuild_trusted_answer
from guance_agent.qa_report_verification import (
    _to_structured,
    finalize_report,
    prune_report,
    review_requirement_support,
)
from guance_agent.research_quality import requirements
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import AgentOutput, Claim, EvidenceRef, QABlock, QAReportDraft, RunResult
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


def _analysis_result(
        question: str, planids: tuple[str, str],
        source_areas: tuple[str, str] = ("密云区", "密云区")):
    claims = [
        Claim(text="十五期间已启动国家级生态县建设。", chunk_ids=["old-period"],
              quotes=["十五期间已启动国家级生态县建设。"]),
        Claim(text="十一五继续深化生态县建设并加强水源涵养。", chunk_ids=["new-period"],
              quotes=["十一五继续深化生态县建设并加强水源涵养。"]),
    ]
    refs = [
        EvidenceRef(planid=planids[index], chunk_id=chunk_id,
                    title="密云县国民经济和社会发展第十一个五年规划纲要",
                    area=source_areas[index], fyp="第十一个五年规划",
                    admin_level=None, plantype_label=None, section_idx=1,
                    section_path="生态建设", char_start=0, char_end=50, source="kb_read")
        for index, chunk_id in enumerate(("old-period", "new-period"))
    ]
    result = RunResult(
        run_id="run", session_id="session", status="completed", evidence_status="verified",
        answer="", claims=claims, evidence_refs=refs, dropped_chunk_ids=[], error=None,
        meta={"research_intent": {"primary": "compare"}, "research_question": question},
        warnings=[], route={"areas": ["密云区"]},
    )
    candidates = [{
        "id": "analysis", "title_path": ["对比分析"], "dimension": "综合判断",
        "kind": "analysis", "text": "规划延续了生态县建设，并将生态优势转向产业发展。",
        "support_signatures": [
            (claim.text, tuple(claim.chunk_ids)) for claim in claims
        ],
    }]
    current = {
        (claim.text, tuple(claim.chunk_ids)): (index, claim)
        for index, claim in enumerate(claims)
    }
    return result, candidates, current


def test_same_document_period_comparison_allows_audited_analysis():
    question = "与密云县'十五'期间相比，'十一五'规划中生态优势有哪些延续和新发展？"
    result, candidates, current = _analysis_result(question, ("620", "620"))

    _to_structured(result, candidates, current, "model_reviewed")

    assert [block["kind"] for block in result.structured["blocks"]] == ["analysis"]


def test_single_region_topic_comparison_does_not_require_another_document():
    question = "比较密云县规划中工业与农业的发展安排有何不同？"
    result, candidates, current = _analysis_result(question, ("620", "620"))

    _to_structured(result, candidates, current, "model_reviewed")

    assert [block["kind"] for block in result.structured["blocks"]] == ["analysis"]


def test_same_document_topic_comparison_without_explicit_route_keeps_analysis():
    question = "请比较规划中产业目标与农业支持方式有何不同？"
    claims = [
        Claim(text="产业目标是建设先进制造基地。", chunk_ids=["industry"],
              quotes=["产业目标是建设先进制造基地。"]),
        Claim(text="农业支持方式是建设示范基地。", chunk_ids=["agriculture"],
              quotes=["农业支持方式是建设示范基地。"]),
    ]
    refs = [
        EvidenceRef(planid="1831", chunk_id=chunk_id,
                    title="同一规划", area=None, fyp="第十一个五年规划",
                    admin_level=None, plantype_label=None, section_idx=index,
                    section_path=path, char_start=0, char_end=30, source="kb_read")
        for index, (chunk_id, path) in enumerate((
            ("industry", "产业发展/工业"),
            ("agriculture", "产业发展/农业"),
        ), start=1)
    ]
    result = RunResult(
        run_id="run", session_id="session", status="completed",
        evidence_status="verified", answer="", claims=claims,
        evidence_refs=refs, dropped_chunk_ids=[], error=None,
        meta={"research_intent": {"primary": "compare"},
              "research_question": question,
              "qa_report_required_dimensions": ["地区差异"]},
        warnings=[], route={},
    )
    candidates = [{
        "id": "analysis", "title_path": ["比较分析"],
        "dimension": "地区差异", "required_dimensions": ["地区差异"],
        "kind": "analysis", "text": "产业侧重建设制造基地，农业侧重示范基地，支持方式不同。",
        "support_signatures": [(claim.text, tuple(claim.chunk_ids)) for claim in claims],
    }]
    current = {
        (claim.text, tuple(claim.chunk_ids)): (index, claim)
        for index, claim in enumerate(claims)
    }

    _to_structured(result, candidates, current, "model_reviewed")

    assert [block["kind"] for block in result.structured["blocks"]] == ["analysis"]


def test_same_document_comparison_rejects_analysis_without_both_periods_in_quotes():
    question = "与密云县'十五'期间相比，'十一五'规划中生态优势有哪些延续和新发展？"
    result, candidates, current = _analysis_result(question, ("620", "620"))
    result.claims[0].quotes = ["国家级生态县建设已经启动。"]

    _to_structured(result, candidates, current, "model_reviewed")

    assert result.structured["blocks"] == []


@pytest.mark.parametrize(
    "claim_text,analysis_text,kept",
    [
        ("新能源装备产业年销售收入目标为300万元。",
         "规划目标年销售收入为300万元，高于用户提出的200万元门槛。", True),
        ("新材料产业年销售收入目标未公开，固定资产投资额达到3亿元。",
         "年销售收入目标未公开，固定资产投资额为3亿元。", False),
    ],
)
def test_prune_allows_single_source_analysis_only_for_supported_value_check(
        claim_text, analysis_text, kept):
    question = "新能源装备产业年销售收入是否不少于200万元？"
    claim = Claim(text=claim_text, chunk_ids=["c1"], quotes=[claim_text])
    ref = EvidenceRef(
        planid="p1", chunk_id="c1", title="示例规划", area="新河市",
        fyp="十五五", admin_level=None, plantype_label=None, section_idx=1,
        section_path="产业发展", char_start=0, char_end=len(claim_text),
        source="kb_read",
    )
    result = RunResult(
        run_id="r", session_id="s", status="completed", evidence_status="verified",
        answer="", claims=[claim], evidence_refs=[ref], dropped_chunk_ids=[],
        error=None, meta={"research_requirements": requirements(question)["units"]},
        warnings=[], route={},
    )
    result._qa_report_supplied = True
    result._qa_report_dimensions = []
    result._qa_report_candidates = [{
        "id": "threshold", "title_path": ["数值核验"],
        "dimension": "数值核验", "kind": "analysis", "text": analysis_text,
        "support_signatures": [(claim.text, tuple(claim.chunk_ids))],
    }]

    prune_report(result, None, "initial_filter")

    assert bool(result._qa_report_candidates) is kept


def test_cross_region_comparison_still_requires_distinct_source_documents():
    question = "比较密云县与延庆县两地规划中的生态发展政策差异。"
    result, candidates, current = _analysis_result(question, ("620", "621"))
    result.route = {"areas": ["密云区", "延庆区"]}

    _to_structured(result, candidates, current, "model_reviewed")

    assert result.structured["blocks"] == []


def test_cross_region_analysis_requires_evidence_from_each_requested_area():
    question = "比较密云县与延庆县两地规划中的生态发展政策差异。"
    result, candidates, current = _analysis_result(question, ("620", "621"))
    result.route = {"areas": ["密云区", "延庆区"]}
    result.evidence_refs[1].area = "延庆区"

    _to_structured(result, candidates, current, "model_reviewed")

    assert [block["kind"] for block in result.structured["blocks"]] == ["analysis"]


def test_named_cross_region_comparison_fails_without_both_area_refs_even_without_route():
    question = "比较密云县与延庆县两地规划中的生态发展政策差异。"
    result, candidates, current = _analysis_result(question, ("620", "621"))
    result.route = {}

    _to_structured(result, candidates, current, "model_reviewed")

    assert result.structured["blocks"] == []


def test_comparison_without_question_or_frozen_scope_fails_closed():
    result, candidates, current = _analysis_result("", ("p1", "p2"),
                                                   ("甲市", "乙市"))
    result.route = {}

    _to_structured(result, candidates, current, "model_reviewed")

    assert result.structured["blocks"] == []


class FakeRuntime:
    def __init__(self, settings, response):
        self.settings = settings
        self.hooks = type("Hooks", (), {"turns": 1})()
        self.response = response
        self.calls = []

    async def complete_text(self, messages, *, max_tokens, timeout, purpose="text_completion"):
        self.calls.append((messages, max_tokens, timeout, purpose))
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
    block = QABlock(id="x", title_path=["总"], dimension="政策工具", kind="analysis",
                    text="分析超过支持事实。", supporting_claim_indexes=[0])
    assert block.supporting_claim_indexes == [0]


def test_prune_still_rejects_single_fact_generic_analysis():
    claim = Claim(text="工业目标包括先进制造。", chunk_ids=["c1"],
                  quotes=["工业目标包括先进制造"])
    ref = EvidenceRef(
        planid="p1", chunk_id="c1", title="示例规划", area="新河市",
        fyp="十五五", admin_level=None, plantype_label=None, section_idx=1,
        section_path="产业发展", char_start=0, char_end=30, source="kb_read",
    )
    result = RunResult(
        run_id="r", session_id="s", status="completed", evidence_status="verified",
        answer="", claims=[claim], evidence_refs=[ref], dropped_chunk_ids=[],
        error=None, meta={"research_requirements": []}, warnings=[], route={},
    )
    result._qa_report_supplied = True
    result._qa_report_candidates = [{
        "id": "analysis", "title_path": ["综合判断"],
        "dimension": "综合判断", "kind": "analysis",
        "text": "工业目标可以带动各方面发展。",
        "support_signatures": [(claim.text, tuple(claim.chunk_ids))],
    }]

    prune_report(result, None, "initial_filter")

    assert result._qa_report_candidates == []


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
async def test_reviewed_requirement_support_maps_report_alias_to_frozen_dimension(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claim = Claim(text="重点布局先进制造和现代服务业。",
                  chunk_ids=[ref.chunk_id], quotes=["重点布局先进制造和现代服务业"])
    draft = QAReportDraft(dimensions=["产业统筹布局"], blocks=[
        QABlock(id="industry", title_path=["发展重点"], dimension="产业统筹布局",
                kind="fact", text="产业重点发力方向包括先进制造和现代服务业。",
                supporting_claim_indexes=[0]),
    ])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="raw", claims=[claim], report=draft), None), ctx, {})
    unit = {"id": "dimension:产业发展方向", "kind": "dimension",
            "label": "产业发展方向", "phase": "evidence", "actionable": True}
    result.meta.update(
        research_question="请概括产业发展方向。",
        research_requirements=[unit],
        qa_report_required_dimensions=["产业发展方向"],
        quote_verification={"checked": 1, "approved": 1, "unverified": 0},
    )
    runtime = FakeRuntime(env[0], json.dumps({
        "facts": [{"index": 0, "supported": True}],
        "blocks": [{"id": "industry", "supported": True}],
        "requirement_support": {"supports": {unit["id"]: [0]}},
    }, ensure_ascii=False))

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert result.structured["coverage"]["status"] == "complete"
    assert result.structured["coverage"]["covered"] == ["产业发展方向"]
    assert result.structured["blocks"][0]["required_dimensions"] == ["产业发展方向"]


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
    assert "部分报告段落未通过审核" not in result.answer
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
async def test_requirement_support_review_runs_directly_after_quote_verification(env):
    from guance_agent.research_quality import requirements

    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claim = Claim(text="甲市重点发展设施农业。", chunk_ids=[ref.chunk_id],
                  quotes=["重点发展设施农业"])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="甲市重点发展设施农业。", claims=[claim]), None), ctx, {})
    result.meta.update(
        research_question="甲市产业发展重点是什么？",
        research_requirements=requirements("甲市产业发展重点是什么？")["units"],
        quote_verification={"checked": 1, "approved": 1, "unverified": 0},
    )
    requirement_id = result.meta["research_requirements"][0]["id"]
    runtime = FakeRuntime(env[0], json.dumps({
        "requirement_support": {"supports": {requirement_id: [0]}},
    }, ensure_ascii=False))

    await review_requirement_support(result, ctx, runtime, deadline=10**12)

    assert result.meta["requirement_support_review"] == "completed"
    assert result.meta["requirement_support"]["supports"] == {requirement_id: [0]}
    assert runtime.calls[0][3] == "requirement_support_review"


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


def test_single_fenced_review_is_decoded_without_choosing_partial_objects():
    from guance_agent.qa_report_verification import _parse_review

    safe = json.dumps({
        "facts": [{"index": 0, "supported": True}],
        "blocks": [{"id": "f", "supported": True}],
    })
    assert _parse_review("```json\n" + safe + "\n```", {0}, {"f"}) == (
        {0: True}, {"f": True}, set(), set(), False,
    )
    assert _parse_review(safe + "\n" + safe, {0}, {"f"}) is None


@pytest.mark.asyncio
async def test_invalid_semantic_review_retries_once_and_only_publishes_valid_verdicts(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    facts = [Claim(text="甲市支持农业。", chunk_ids=[ref.chunk_id],
                   quotes=["甲市支持农业"])]
    draft = QAReportDraft(dimensions=["政策工具"], blocks=[
        QABlock(id="f", title_path=["政策工具"], dimension="政策工具",
                kind="fact", text="甲市支持农业。", supporting_claim_indexes=[0]),
    ])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="不能直接交付", claims=facts, report=draft), None), ctx, {})
    result.meta["quote_verification"] = {"checked": 1, "approved": 1, "unverified": 0}
    valid = json.dumps({"facts": [{"index": 0, "supported": True}],
                        "blocks": [{"id": "f", "supported": True}]})

    class SequentialRuntime(FakeRuntime):
        async def complete_text(self, messages, *, max_tokens, timeout, purpose="text_completion"):
            self.calls.append((messages, max_tokens, timeout, purpose))
            return "not JSON" if len(self.calls) == 1 else valid

    runtime = SequentialRuntime(env[0], None)
    await finalize_report(result, ctx, runtime, deadline=10**12)
    assert len(runtime.calls) == 2
    assert result.meta["qa_report"]["first_review_invalid"] is True
    assert result.meta["qa_report"]["review_model_calls"] == 2
    assert [block["id"] for block in result.structured["blocks"]] == ["f"]
    assert result.structured["verification"]["facts"] == "semantic_reviewed_against_quotes"


@pytest.mark.asyncio
async def test_two_invalid_review_outputs_never_publish_report_analysis(env):
    ctx = context(env)
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    facts = [Claim(text="甲市支持农业。", chunk_ids=[ref.chunk_id],
                   quotes=["甲市支持农业"])]
    draft = QAReportDraft(dimensions=["政策工具"], blocks=[
        QABlock(id="f", title_path=["政策工具"], dimension="政策工具",
                kind="fact", text="甲市支持农业。", supporting_claim_indexes=[0]),
    ])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="未经审核", claims=facts, report=draft), None), ctx, {})
    result.meta["quote_verification"] = {"checked": 1, "approved": 1, "unverified": 0}
    runtime = FakeRuntime(env[0], "not JSON")
    await finalize_report(result, ctx, runtime, deadline=10**12)
    assert len(runtime.calls) == 2
    assert result.structured["blocks"] == []
    assert result.meta["qa_report"]["reason"] == "semantic_review_invalid_response"
    assert result.structured["verification"]["facts"] == "not_reviewed"
