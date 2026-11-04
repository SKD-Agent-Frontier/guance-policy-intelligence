"""Research-intent evaluation, audited report drafting, and bounded gap repair."""
import copy
import json
import time
from types import SimpleNamespace

import pytest

import guance_agent.research_supplement as supplement
from guance_agent.qa_report_verification import prune_report
from guance_agent.research_delivery import deliver_research, draft_verified_report
from guance_agent.research_quality import evaluate, followup_gap, requirements
from guance_agent.research_requirements import claim_fingerprint, requirements_fingerprint
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import Claim, EvidenceRef, RunResult


def _ref(cid="chunk-1"):
    return EvidenceRef(
        planid="2561", chunk_id=cid, title="某地规划纲要", area="某市",
        fyp="十四五", admin_level=None, plantype_label=None,
        section_idx=1, section_path="发展方向", char_start=0, char_end=50,
        source="kb_read",
    )


def _result():
    return RunResult(
        run_id="run", session_id="session", status="completed",
        evidence_status="verified", answer="模型未经审核的分析不得直接发表",
        claims=[Claim(text="政策提出建设基础设施", chunk_ids=["chunk-1"],
                      quotes=["建设基础设施"]),
                Claim(text="政策提出人才培养措施", chunk_ids=["chunk-2"],
                      quotes=["人才培养"])],
        evidence_refs=[_ref("chunk-1"), _ref("chunk-2")],
        dropped_chunk_ids=[], error=None, meta={"quote_verification": {"unverified": 0}},
        task_type="qa",
    )


def _set_support(result, question, supports):
    from guance_agent.research_quality import requirements

    units = requirements(question, result.meta.get("research_requirements"))["units"]
    if isinstance(result.meta.get("research_requirements"), list):
        result.meta["research_requirements"] = units
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(units),
        "supports": supports,
    }


def test_user_requirements_are_not_satisfied_by_model_declarations():
    result = _result()
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["政策目标"],
        "blocks": [{"dimension": "政策目标", "title_path": ["目标"],
                    "kind": "fact", "text": "基础设施建设",
                    "verification": "model_reviewed_evidence_linked"}],
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    quality = evaluate(result, "分析当地政策优势及问题挑战")
    assert quality["status"] == "partial"
    assert quality["missing_dimensions"] == ["政策优势", "问题挑战"]
    assert "audited_analysis_missing" in quality["gaps"]
    assert requirements("分析当地政策优势及问题挑战")["needs_report"]


def test_unknown_metric_support_must_match_subject_and_typed_value():
    question = "关于新材料产业投资强度目标是多少？"
    claim = Claim(text="新材料产业新建3座产业园", chunk_ids=["metric-claim"],
                  quotes=["新材料产业新建3座产业园"])
    result = RunResult(
        run_id="run-metric", session_id="session-metric", status="completed",
        evidence_status="verified", answer="", claims=[claim],
        evidence_refs=[_ref("metric-claim")], dropped_chunk_ids=[], error=None,
        meta={"quote_verification": {"unverified": 0}}, task_type="qa",
    )
    planned = [{"kind": "metric", "label": "投资强度", "subject": "新材料产业",
                "metric": "investment_intensity", "actionable": True}]
    units = requirements(question, planned)["units"]
    result.meta["research_requirements"] = units
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(units),
        "supports": {units[0]["id"]: [0]},
    }

    quality = evaluate(result, question)

    assert quality["status"] != "complete"
    assert quality["missing_requirements"]


def test_audited_analysis_can_satisfy_explicit_dimension():
    result = _result()
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["政策优势"],
        "blocks": [{"dimension": "政策优势", "title_path": ["政策优势"],
                    "kind": "analysis", "text": "两类支持措施具有互补作用",
                    "verification": "model_reviewed_evidence_linked",
                    "supporting_chunk_ids": ["chunk-1", "chunk-2"]}],
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    assert evaluate(result, "分析政策优势")["status"] == "complete"
    deliver_research(result, "分析政策优势")
    assert "政策优势" in result.answer
    assert "两类支持措施具有互补作用" in result.answer
    assert "## 研究问题" not in result.answer
    assert "## 参考文献" not in result.answer
    assert result.meta["research_delivery"]["audited_analysis"]


@pytest.mark.parametrize(
    "report_text,visible_answer,expected_status",
    [("新材料产业年销售收入目标已明确。", "新材料产业年销售收入目标已明确。", "partial"),
     ("新材料产业年销售收入达到20亿元。", "新材料产业年销售收入达到20亿元。", "complete"),
     ("新材料产业年销售收入达到20亿元。", "报告只有产业发展方向概述。", "partial")],
)
def test_audited_report_must_render_the_verified_metric_value(
        report_text, visible_answer, expected_status):
    result = _result()
    claim = Claim(text="新材料产业年销售收入达到20亿元。", chunk_ids=["chunk-1"],
                  quotes=["新材料产业年销售收入达到20亿元。"])
    result.claims = [claim]
    result.evidence_refs = [_ref("chunk-1")]
    question = "请整理新材料产业年销售收入目标"
    units = requirements(question)["units"]
    metric = next(unit for unit in units if unit.get("kind") == "metric")
    result.meta["research_requirements"] = units
    _set_support(result, question, {metric["id"]: [0]})
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["产业目标"],
        "blocks": [{"id": "report", "dimension": "产业目标",
                    "title_path": ["产业目标"], "kind": "fact",
                    "text": report_text, "supporting_claim_indexes": [0],
                    "supporting_chunk_ids": ["chunk-1"],
                    "verification": "model_reviewed_evidence_linked"}],
        "coverage": {"status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    result.answer = visible_answer

    quality = evaluate(result, question)

    assert quality["status"] == expected_status
    if expected_status == "partial":
        item = next(unit for unit in quality["requirement_coverage"]
                    if unit["id"] == metric["id"])
        assert item["phase"] == "synthesis"
        assert item["actionable"] is False
        assert item["delivery_status"] == "missing"


def test_final_delivery_restores_approved_blocks_after_answer_truncation():
    result = _result()
    result.claims = [Claim(text="新材料产业年销售收入达到20亿元。",
                           chunk_ids=["chunk-1"],
                           quotes=["新材料产业年销售收入达到20亿元。"])]
    result.evidence_refs = [_ref("chunk-1")]
    question = "请整理新材料产业年销售收入目标"
    units = requirements(question)["units"]
    metric = next(unit for unit in units if unit.get("kind") == "metric")
    result.meta["research_requirements"] = units
    _set_support(result, question, {metric["id"]: [0]})
    result.structured = {
        "schema_version": "qa-report/1", "dimensions": ["产业目标"],
        "blocks": [
            {"id": "overview", "dimension": "产业目标", "title_path": ["产业目标"],
             "kind": "fact", "text": "新材料产业重点推进产业升级。",
             "supporting_claim_indexes": [], "supporting_chunk_ids": [],
             "verification": "model_reviewed_evidence_linked"},
            {"id": "revenue", "dimension": "年销售收入", "title_path": ["年销售收入"],
             "required_dimensions": ["年销售收入"], "kind": "fact",
             "text": "新材料产业年销售收入达到20亿元。",
             "supporting_claim_indexes": [0], "supporting_chunk_ids": ["chunk-1"],
             "verification": "model_reviewed_evidence_linked"},
        ],
        "coverage": {"status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    result.answer = "新材料产业重点推进产业升级。"

    deliver_research(result, question)

    assert "年销售收入达到20亿元" in result.answer
    assert result.meta["research_quality"]["status"] == "complete"


def test_audited_report_does_not_deliver_catalog_facets_without_visible_blocks():
    result = _result()
    result.claims = [
        Claim(text="规划提出推进协同发展。", chunk_ids=["chunk-1"],
              quotes=["推进协同发展"]),
        Claim(text="规划要求改善生态环境。", chunk_ids=["chunk-2"],
              quotes=["改善生态环境"]),
    ]
    result.evidence_refs = [
        EvidenceRef(
            planid="new-policy", chunk_id=f"chunk-{index}",
            title="某地规划纲要", area="某市", fyp="十四五",
            admin_level=None, plantype_label=None, section_idx=1,
            section_path="发展方向", char_start=0, char_end=50,
            source="kb_read",
        )
        for index in (1, 2)
    ]
    question = "请概括发展目标主要涉及哪些方面？"
    planned_units = [
        {"id": "planned:facet-coordination", "kind": "subject_fact",
         "label": "目录方面：协同发展", "subject": "协同发展"},
        {"id": "planned:facet-environment", "kind": "subject_fact",
         "label": "目录方面：生态环境", "subject": "生态环境"},
    ]
    units = requirements(question, planned_units)["units"]
    result.meta["research_requirements"] = units
    supports = {unit["id"]: [] for unit in units}
    coordination = next(unit for unit in units if unit["subject"] == "协同发展")
    environment = next(unit for unit in units if unit["subject"] == "生态环境")
    supports[coordination["id"]] = [0]
    supports[environment["id"]] = [1]
    _set_support(result, question, supports)
    result.meta["catalog_requirement_sources"] = {
        unit["id"]: {"planid": "new-policy", "section_path": "发展方向"}
        for unit in (coordination, environment)
    }
    result.structured = {
        "schema_version": "qa-report/1", "dimensions": ["发展目标"],
        "blocks": [{"id": "coordination", "dimension": "发展目标",
                    "title_path": ["协同发展"], "kind": "fact",
                    "text": "规划提出推进协同发展。", "supporting_claim_indexes": [0],
                    "supporting_chunk_ids": ["chunk-1"],
                    "verification": "model_reviewed_evidence_linked"}],
        "coverage": {"status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    result.answer = "规划提出推进协同发展。"

    quality = evaluate(result, question)

    missing = next(item for item in quality["requirement_coverage"]
                   if item["id"] == environment["id"])
    assert quality["status"] == "partial"
    assert missing["delivery_status"] == "missing"
    assert missing["phase"] == "synthesis"


def test_missing_semantic_review_never_publishes_unreviewed_prose():
    result = _result()
    deliver_research(result, "分析政策优势及问题挑战")
    assert "模型未经审核" not in result.answer
    assert "政策提出建设基础设施" in result.answer
    assert "非完整研究结论" not in result.answer
    assert "研究完成情况" not in result.answer
    assert result.meta["research_quality"]["status"] == "partial"
    assert result.evidence_status == "insufficient"


@pytest.mark.asyncio
async def test_report_synthesis_fails_closed_when_insufficient_time():
    result = _result()
    called = []

    async def complete_text(*args, **kwargs):
        called.append(1)
        raise AssertionError("no budget for another model call")

    runtime = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
        complete_text=complete_text,
    )
    assert not await draft_verified_report(
        result, "分析政策优势", runtime, time.monotonic() + 40)
    assert not called
    assert not result._qa_report_supplied


@pytest.mark.asyncio
async def test_model_report_is_only_pending_until_semantic_audit():
    result = _result()

    async def complete_text(*args, **kwargs):
        return json.dumps({
            "dimensions": ["政策优势"],
            "blocks": [{"id": "b1", "title_path": ["已知事实"],
                        "dimension": "政策优势", "kind": "fact",
                        "text": "政策涉及基础设施建设",
                        "supporting_claim_indexes": [0]}],
        })

    runtime = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
        complete_text=complete_text,
    )
    assert await draft_verified_report(
        result, "分析政策优势", runtime, time.monotonic() + 120)
    assert result._qa_report_supplied
    assert len(result._qa_report_candidates) == 1
    assert result.meta["research_report_draft"] == "pending_semantic_review"
    assert "qa-report" not in str(result.structured)
    assert result.answer == "模型未经审核的分析不得直接发表"


@pytest.mark.asyncio
async def test_report_draft_prioritizes_claims_mapped_to_frozen_requirements():
    result = _result()
    result.claims = [Claim(text=f"无关政策事实编号{i}", chunk_ids=[f"chunk-{i}"],
                           quotes=[f"无关政策事实编号{i}"]) for i in range(16)]
    result.claims[15] = Claim(
        text="新材料产业年销售收入达到20亿元", chunk_ids=["chunk-15"],
        quotes=["新材料产业年销售收入达到20亿元"])
    result.evidence_refs = [_ref(f"chunk-{i}") for i in range(16)]
    question = "分析新材料产业年销售收入目标"
    units = requirements(question)["units"]
    target = next(unit for unit in units if unit.get("kind") == "metric")
    result.meta["research_requirements"] = units
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(units),
        "supports": {target["id"]: [15]},
    }
    seen_payload = {}

    async def complete_text(messages, **kwargs):
        seen_payload.update(json.loads(messages[-1]["content"]))
        return json.dumps({
            "dimensions": ["年销售收入"],
            "blocks": [{"id": "revenue", "title_path": ["年销售收入"],
                        "dimension": "年销售收入", "kind": "fact",
                        "text": "新材料产业年销售收入达到20亿元",
                        "supporting_claim_indexes": [0]}],
        }, ensure_ascii=False)

    runtime = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
        complete_text=complete_text,
    )

    assert await draft_verified_report(
        result, question, runtime, time.monotonic() + 120, force=True)

    assert seen_payload["facts"][0]["claim_index"] == 15
    assert result._qa_report_candidates[0]["support_signatures"] == [
        (result.claims[15].text, tuple(result.claims[15].chunk_ids)),
    ]


@pytest.mark.asyncio
async def test_value_check_draft_accepts_one_cited_fact_then_uses_narrow_prune_gate():
    question = "新能源装备产业年销售收入是否不少于200万元？"
    claim = Claim(text="新能源装备产业年销售收入目标为300万元。",
                  chunk_ids=["chunk-1"], quotes=["新能源装备产业年销售收入目标为300万元。"])
    result = _result()
    result.claims = [claim]
    result.evidence_refs = [_ref("chunk-1")]
    result.meta["research_requirements"] = requirements(question)["units"]

    async def complete_text(messages, **kwargs):
        return json.dumps({
            "dimensions": ["数值核对"],
            "blocks": [{"id": "value-check", "title_path": ["阈值核验"],
                        "dimension": "数值核对", "kind": "analysis",
                        "text": "新能源装备产业年销售收入为300万元，高于用户给出的200万元门槛。",
                        "supporting_claim_indexes": [0]}],
        }, ensure_ascii=False)

    runtime = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
        complete_text=complete_text,
    )

    assert await draft_verified_report(
        result, question, runtime, time.monotonic() + 120, force=True)
    prune_report(result, None, "initial_filter")

    assert [candidate["id"] for candidate in result._qa_report_candidates] == ["value-check"]


@pytest.mark.asyncio
async def test_gap_followup_respects_budget_and_original_scope():
    result = _result()
    called = []

    async def never_execute(*args):
        called.append(1)

    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0))
    question = "关于新能源装备产业的年销售收入目标是多少？"
    task = SimpleNamespace(task_type="qa", question=question,
                           model_copy=lambda update: SimpleNamespace(
                               task_type="qa", question=update["question"]))
    s = SimpleNamespace(max_turns=8, max_input_chars=4000)
    ctx = SimpleNamespace(route=SimpleNamespace(kind="open"))
    await supplement.supplement_one(result, task, s, ctx, rt,
                                    time.monotonic() + 90, never_execute, None)
    assert not called
    assert result.meta["research_supplement"]["status"] == "skipped_budget"
    assert "年销售收入" in (followup_gap(result, task.question) or "")


@pytest.mark.asyncio
async def test_followup_adds_only_fresh_verified_facts(monkeypatch):
    result = _result()
    part = _result()
    part.claims = [Claim(text="新能源装备产业年营业收入达到20亿元", chunk_ids=["chunk-3"],
                         quotes=["新能源装备产业年营业收入达到20亿元"])]
    part.evidence_refs = [_ref("chunk-3")]
    calls = []

    async def execute_once(task, s, ctx, rt, deadline):
        calls.append(task.question)
        return ExecOutcome("{}", None)

    async def recover(outcome, *_args, **_kwargs):
        return outcome, None

    async def noop(*_args):
        return None

    monkeypatch.setattr(supplement, "build_result", lambda *_args: part)
    monkeypatch.setattr(supplement, "verify_quotes", noop)
    monkeypatch.setattr(supplement, "verify_numbers", noop)
    monkeypatch.setattr(supplement, "enforce_literal_result", lambda *_args: None)
    question = "关于新能源装备产业的年销售收入目标是多少？"
    task = SimpleNamespace(task_type="qa", question=question,
                           model_copy=lambda update: SimpleNamespace(
                               task_type="qa", question=update["question"]))
    s = SimpleNamespace(max_turns=8, max_input_chars=4000)
    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0), meta={})
    ctx = SimpleNamespace(route=SimpleNamespace(kind="open"))
    await supplement.supplement_one(result, task, s, ctx, rt,
                                    time.monotonic() + 120, execute_once, recover)
    assert len(calls) == 1
    assert "年销售收入" in calls[0]
    assert "新能源装备产业年营业收入达到20亿元" in [c.text for c in result.claims]
    assert "chunk-3" in {r.chunk_id for r in result.evidence_refs}
    assert result.meta["research_supplement"]["added_claims"] == 1


def test_simple_single_facet_qa_rejects_a_verified_fact_for_the_wrong_requirement():
    result = _result()
    result.claims = [Claim(text="新能源装备产业有32家企业", chunk_ids=["chunk-1"],
                           quotes=["新能源装备产业有32家企业"])]
    result.evidence_refs = [_ref("chunk-1")]
    question = "这些政策的主要措施有哪些？"
    quality = evaluate(result, question)
    assert quality["needs_report"] is False
    assert quality["missing_dimensions"] == ["重点措施"]
    assert quality["missing_requirements"] == ["重点措施"]
    assert quality["factual_answer_coverage"] is False
    assert quality["status"] == "partial"


def test_simple_single_facet_qa_still_accepts_a_matching_fact_without_a_report():
    result = _result()
    result.claims = [Claim(text="主要措施包括建设产业基地", chunk_ids=["chunk-1"],
                           quotes=["主要措施包括建设产业基地"])]
    result.evidence_refs = [_ref("chunk-1")]

    quality = evaluate(result, "这些政策的主要措施有哪些？")

    assert quality["missing_requirements"] == []
    assert quality["factual_answer_coverage"] is True
    assert quality["status"] == "complete", quality["gaps"]


def test_semantically_reviewed_report_covers_paraphrased_analysis_requirement():
    question = "请说明两个支持方向如何互补"
    result = _result()
    unit = {"id": "relationship", "kind": "analysis", "label": "建设与人才投入的互补关系",
            "subject": None, "metric": None, "expected_value": None,
            "phase": "synthesis", "actionable": False}
    units = requirements(question, [unit])["units"]
    result.meta["research_requirements"] = units
    result.structured = {
        "schema_version": "qa-report/1",
        "blocks": [{"kind": "analysis", "text": "基础设施与人才形成相互支持的条件",
                    "title_path": ["综合判断"], "dimension": "综合判断",
                    "supporting_chunk_ids": ["chunk-1", "chunk-2"],
                    "verification": "model_reviewed_evidence_linked"}],
        "coverage": {"status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    _set_support(result, question, {units[0]["id"]: [0, 1]})
    assert evaluate(result, question)["status"] == "partial"

    result.meta["requirement_support"]["basis"] = "audited_report"
    assert evaluate(result, question)["status"] == "complete"


def test_audited_comparison_maps_abstract_sides_to_concrete_verified_claims():
    question = "请比较两类产业支持策略有何不同？"
    result = _result()
    result.claims = [
        Claim(text="重点培植铝型材、不锈钢等支柱产业和骨干企业",
              chunk_ids=["shared"],
              quotes=["重点培植铝型材、不锈钢等支柱产业和骨干企业"]),
        Claim(text="农业以基地建设、规模扩张和标准化养殖为重点",
              chunk_ids=["shared"],
              quotes=["农业以基地建设、规模扩张和标准化养殖为重点"]),
    ]
    result.evidence_refs = [_ref("shared")]
    relation = {
        "kind": "comparison", "label": "工业发展目标与农业领域特色产业比较",
        "left": "工业发展目标", "right": "农业领域特色产业",
        "phase": "synthesis", "actionable": False,
    }
    units = requirements(question, [relation])["units"]
    relation_unit = next(unit for unit in units if unit["kind"] == "comparison")
    left_unit = next(unit for unit in units
                     if unit["id"] == relation_unit["left_requirement_id"])
    right_unit = next(unit for unit in units
                      if unit["id"] == relation_unit["right_requirement_id"])
    result.meta["research_requirements"] = units
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "basis": "audited_report",
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(units),
        "supports": {
            left_unit["id"]: [0], right_unit["id"]: [1],
            relation_unit["id"]: [0, 1],
        },
    }
    answer = ("工业重点扶持支柱产业和骨干企业；农业侧重基地建设、规模扩张与标准化养殖。"
              "因此两者分别依靠产业集聚和生产体系建设推动发展。")
    result.answer = answer
    result.structured = {
        "schema_version": "qa-report/1",
        "blocks": [
            {"kind": "fact", "text": "重点培植铝型材、不锈钢等支柱产业和骨干企业",
             "title_path": ["工业发展目标"], "dimension": "政策目标",
             "supporting_claim_indexes": [0], "supporting_chunk_ids": ["shared"],
             "verification": "model_reviewed_evidence_linked"},
            {"kind": "fact", "text": "农业以基地建设、规模扩张和标准化养殖为重点",
             "title_path": ["农业领域特色产业"], "dimension": "政策目标",
             "supporting_claim_indexes": [1], "supporting_chunk_ids": ["shared"],
             "verification": "model_reviewed_evidence_linked"},
            {"kind": "analysis", "text": answer,
             "title_path": ["综合判断"], "dimension": "地区差异",
             "supporting_claim_indexes": [0, 1],
             "supporting_chunk_ids": ["shared"],
             "verification": "model_reviewed_evidence_linked"},
        ],
        "coverage": {"status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }

    quality = evaluate(result, question)

    comparison = next(unit for unit in quality["requirement_coverage"]
                      if unit["kind"] == "comparison")
    assert comparison["status"] == "covered", quality["gaps"]
    assert comparison["evidence_claim_indexes"] == [0, 1]

    analysis = result.structured["blocks"][-1]
    analysis["supporting_claim_indexes"] = [0]
    quality = evaluate(result, question)
    comparison = next(unit for unit in quality["requirement_coverage"]
                      if unit["kind"] == "comparison")
    assert comparison["status"] == "missing"


def test_unrelated_verified_claims_do_not_complete_a_named_metric_request():
    result = _result()
    result.claims = [Claim(text="新能源装备产业有32家企业", chunk_ids=["chunk-1"],
                           quotes=["新能源装备产业有32家企业"])]
    result.evidence_refs = [_ref("chunk-1")]

    quality = evaluate(
        result,
        "关于新能源装备产业的企业数量和年销售收入目标分别是多少？",
    )

    assert quality["status"] == "partial"
    assert "企业数量" in quality["covered_requirements"]
    assert "年销售收入" in quality["missing_requirements"]


def test_year_and_unrelated_count_do_not_satisfy_a_numeric_output_target():
    result = _result()
    result.claims = [Claim(
        text="规划于2025年实施，数字农业经营主体达到100家",
        chunk_ids=["chunk-1"],
        quotes=["规划于2025年实施，数字农业经营主体达到100家"],
    )]
    result.evidence_refs = [_ref("chunk-1")]

    quality = evaluate(
        result,
        "关于数字农业的目标产值和规划年份分别是什么？请给出量化指标。",
    )

    assert quality["status"] == "partial"
    assert "目标产值" in quality["missing_requirements"]


def test_revenue_synonym_is_accepted_when_all_requested_metrics_are_supported():
    result = _result()
    result.claims = [
        Claim(text="新能源装备产业有32家企业", chunk_ids=["chunk-1"],
              quotes=["新能源装备产业有32家企业"]),
        Claim(text="新能源装备产业年营业收入达到20亿元", chunk_ids=["chunk-2"],
              quotes=["新能源装备产业年营业收入达到20亿元"]),
    ]
    result.evidence_refs = [_ref("chunk-1"), _ref("chunk-2")]

    quality = evaluate(
        result,
        "关于新能源装备产业的企业数量和年销售收入目标分别是多少？",
    )

    assert quality["status"] == "complete"
    assert set(quality["covered_requirements"]) >= {"企业数量", "年销售收入"}


def test_unparsed_question_cannot_become_an_empty_complete_checklist():
    result = _result()
    quality = evaluate(result, "请梳理新材料产业投资强度、人均床位与服务覆盖率")

    assert quality["requirement_coverage"]
    assert any(unit["kind"] == "unresolved_requirement"
               for unit in quality["requirement_coverage"])
    assert quality["status"] == "partial"


def test_preplanned_unknown_metric_uses_semantic_quote_mapping():
    result = _result()
    result.claims[0] = Claim(
        text="新材料产业固定资产投资强度达到每亩1.2万元",
        chunk_ids=["chunk-1"],
        quotes=["新材料产业固定资产投资强度达到每亩1.2万元"],
    )
    unit = {
        "kind": "metric", "label": "投资强度",
        "subject": "新材料产业", "metric": "investment_intensity",
        "phase": "evidence", "actionable": True,
    }
    result.meta["research_requirements"] = [unit]
    from guance_agent.research_quality import requirements
    requirement_id = next(
        item["id"] for item in requirements(
            "新材料产业投资强度是多少？", result.meta["research_requirements"]
        )["units"] if item["kind"] == "metric"
    )
    _set_support(result, "新材料产业投资强度是多少？", {requirement_id: [0]})

    quality = evaluate(result, "新材料产业投资强度是多少？")

    assert "投资强度" in quality["covered_requirements"]
    assert quality["status"] == "complete"


def test_claim_mapping_alone_cannot_satisfy_a_planned_analysis_requirement():
    result = _result()
    result.meta["research_requirements"] = [{
        "kind": "analysis",
        "label": "服务业发展差异", "phase": "synthesis", "actionable": False,
    }]
    from guance_agent.research_quality import requirements
    requirement_id = next(
        item["id"] for item in requirements(
            "比较服务业发展差异", result.meta["research_requirements"]
        )["units"] if item["kind"] == "analysis"
    )
    _set_support(result, "比较服务业发展差异", {requirement_id: [0]})

    quality = evaluate(result, "比较服务业发展差异")

    assert quality["needs_report"]
    assert quality["status"] == "partial"


def test_reused_planner_ids_cannot_support_two_distinct_metric_requirements():
    result = _result()
    result.meta["research_requirements"] = [{
        "id": "collision", "kind": "metric", "label": "投资强度",
        "subject": "新材料产业", "metric": "investment_intensity",
    }, {
        "id": "collision", "kind": "metric", "label": "覆盖率",
        "subject": "新材料产业", "metric": "coverage_rate",
    }]
    _set_support(result, "新材料产业投资强度和覆盖率分别是多少？", {"collision": [0]})

    quality = evaluate(result, "新材料产业投资强度和覆盖率分别是多少？")

    assert len({unit["id"] for unit in quality["requirement_coverage"]}) == 2
    assert quality["covered_requirements"] == []
    assert set(quality["missing_requirements"]) >= {"投资强度", "覆盖率"}


def test_stale_support_index_is_rejected_after_supported_claim_is_pruned():
    result = _result()
    result.claims = [
        Claim(text="新材料产业投资强度为每亩1万元", chunk_ids=["chunk-1"],
              quotes=["新材料产业投资强度为每亩1万元"]),
        Claim(text="另一项无关政策内容", chunk_ids=["chunk-2"],
              quotes=["另一项无关政策内容"]),
    ]
    result.evidence_refs = [_ref("chunk-1"), _ref("chunk-2")]
    result.meta["research_requirements"] = [{
        "kind": "metric", "label": "投资强度", "subject": "新材料产业",
        "metric": "investment_intensity",
    }]
    from guance_agent.research_quality import requirements
    requirement_id = next(
        unit["id"] for unit in requirements(
            "新材料产业投资强度是多少？", result.meta["research_requirements"]
        )["units"] if unit["kind"] == "metric"
    )
    _set_support(result, "新材料产业投资强度是多少？", {requirement_id: [0]})

    result.claims = result.claims[1:]
    result.evidence_refs = [_ref("chunk-2")]
    quality = evaluate(result, "新材料产业投资强度是多少？")

    assert quality["status"] == "partial"
    assert "投资强度" in quality["missing_requirements"]


def test_frozen_requirement_plan_is_not_mutated_during_quality_evaluation():
    result = _result()
    plan = [{"id": "fixed:1", "kind": "metric", "label": "投资强度",
             "subject": "新材料产业", "metric": "investment_intensity",
             "phase": "evidence", "actionable": True}]
    result.meta["research_requirements"] = plan
    before = copy.deepcopy(plan)

    evaluate(result, "新材料产业投资强度是多少？")

    assert result.meta["research_requirements"] == before


def test_missing_numeric_requirement_without_subject_remains_partial():
    result = _result()
    result.meta["research_requirements"] = [{
        "id": "planned:numeric-without-subject", "kind": "numeric_fact",
        "label": "量化指标", "subject": None, "metric": None,
        "phase": "evidence", "actionable": True,
    }]

    quality = evaluate(result, "这项政策的量化指标是什么？")

    assert quality["status"] == "partial"
    assert any(item["kind"] == "numeric_fact" and item["status"] == "missing"
               and item["gap_label"] == "量化指标"
               for item in quality["requirement_coverage"])


def test_verified_numeric_requirement_without_subject_can_be_covered():
    question = "这项政策的量化指标是什么？"
    claim = Claim(text="项目投入资金达到100万元", chunk_ids=["chunk-1"],
                  quotes=["项目投入资金达到100万元"])
    result = _result()
    result.answer = claim.text
    result.claims = [claim]
    result.evidence_refs = [_ref("chunk-1")]
    result.meta["research_requirements"] = [{
        "id": "planned:numeric-without-subject", "kind": "numeric_fact",
        "label": "投入指标", "subject": None, "metric": None,
        "phase": "evidence", "actionable": True,
    }]
    normalized_unit = next(item for item in requirements(
        question, result.meta["research_requirements"]
    )["units"] if item["kind"] == "numeric_fact")
    _set_support(result, question, {normalized_unit["id"]: [0]})

    quality = evaluate(result, question)

    requirement = next(item for item in quality["requirement_coverage"]
                       if item["kind"] == "numeric_fact"
                       and item["gap_label"] == "投入指标")
    assert requirement["status"] == "covered"


@pytest.mark.parametrize(
    "question,claim_text,analysis_text,metric_label",
    [
        ("新材料产业年销售收入是否不少于200万元？",
         "新材料产业年销售收入目标为300万元",
            "新材料产业年销售收入目标为300万元，高于提出的200万元门槛。",
         "年销售收入"),
        ("新材料产业产能是否写了50万吨？",
         "新材料产业规划产能为40万吨",
             "新材料产业规划产能为40万吨，并未达到提出的50万吨门槛。",
         "产能"),
    ],
)
def test_threshold_and_verification_questions_compare_against_cited_value(
        question, claim_text, analysis_text, metric_label):
    result = _result()
    result.claims = [Claim(text=claim_text, chunk_ids=["chunk-1"], quotes=[claim_text])]
    result.evidence_refs = [_ref("chunk-1")]
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": [],
        "blocks": [{"dimension": "数值核对", "title_path": [metric_label],
                    "kind": "analysis", "text": analysis_text,
                    "verification": "model_reviewed_evidence_linked",
                    "supporting_chunk_ids": ["chunk-1"],
                    "supporting_claim_indexes": [0]}],
        "coverage": {"status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    result.answer = analysis_text

    quality = evaluate(result, question)

    assert quality["status"] == "complete"
    assert any(unit["kind"] == "value_check" and unit["status"] == "covered"
               for unit in quality["requirement_coverage"])


def test_unrelated_year_does_not_satisfy_threshold_value_check():
    result = _result()
    result.claims = [Claim(text="规划于2025年实施", chunk_ids=["chunk-1"],
                           quotes=["规划于2025年实施"])]
    result.evidence_refs = [_ref("chunk-1")]

    quality = evaluate(result, "新材料产业产能是否写了50万吨？")

    assert quality["status"] == "partial"
    assert any(unit["kind"] == "value_check" and unit["status"] == "missing"
               for unit in quality["requirement_coverage"])


def test_same_plan_industry_comparison_does_not_require_two_policy_documents():
    from guance_agent.evidence_coverage import multi_document_required

    question = "某县规划中工业发展目标与特色农业相比，政策支持方式有何不同？"

    assert not multi_document_required(question)
    assert multi_document_required("比较甲市与乙市规划的扶持措施有何差异？")


@pytest.mark.asyncio
async def test_real_rag_supplement_uses_fresh_bounded_reads_not_legacy_agent(monkeypatch):
    result = _result()
    part = _result()
    part.claims = [Claim(text="新能源装备产业年营业收入达到20亿元", chunk_ids=["fresh"],
                         quotes=["新能源装备产业年营业收入达到20亿元"])]
    part.evidence_refs = [_ref("fresh")]
    seen = []

    async def controlled(task, settings, ctx, rt, deadline, *, focus, exclude_chunks):
        seen.append((focus, exclude_chunks, task.question))
        rt.meta["qa_supplement_retrieval"] = {"read_chunks": ["fresh"]}
        return ExecOutcome("{}", None)

    async def should_not_run(*args):
        raise AssertionError("Old agent tool loop must not run for real-RAG supplement")

    async def recover(outcome, *args, **kwargs):
        return outcome, None

    async def noop(*args):
        return None

    monkeypatch.setattr(supplement, "execute_controlled", controlled)
    monkeypatch.setattr(supplement, "build_result", lambda *args: part)
    monkeypatch.setattr(supplement, "verify_quotes", noop)
    monkeypatch.setattr(supplement, "verify_numbers", noop)
    monkeypatch.setattr(supplement, "enforce_literal_result", lambda *args: None)
    monkeypatch.setattr(supplement, "finalize_controlled_facts", lambda *args: None)

    original_question = "关于新能源装备产业的年销售收入目标是多少？"
    task = SimpleNamespace(task_type="qa", question=original_question,
                           model_copy=lambda update: SimpleNamespace(
                               task_type="qa", question=update["question"]))
    ctx = SimpleNamespace(route=SimpleNamespace(kind="open"), scope=None)
    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0),
                         meta={"qa_execution_mode": "bounded_retrieval"})
    s = SimpleNamespace(kb_backend="rag", max_turns=8, max_input_chars=4000)
    await supplement.supplement_one(
        result, task, s, ctx, rt, time.monotonic() + 160,
        should_not_run, recover,
    )
    assert len(seen) == 1
    assert "年销售收入" in seen[0][0]
    assert seen[0][1] == frozenset()
    assert seen[0][2] == original_question
    assert result.meta["research_supplement"]["status"] == "verified_facts_added"
    assert [c.text for c in result.claims][-1] == "新能源装备产业年营业收入达到20亿元"
    assert ctx.scope is None


@pytest.mark.asyncio
async def test_invalid_report_draft_becomes_only_pending_verified_fact_blocks():
    result = _result()
    result.meta["quote_verification"]["approved"] = 2
    calls = []

    async def invalid_draft(*args, **kwargs):
        calls.append(1)
        return "{not a valid report"

    runtime = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
        complete_text=invalid_draft,
    )
    assert await draft_verified_report(
        result, "分析政策优势与挑战", runtime, time.monotonic() + 130)
    assert len(calls) == 1
    assert result.meta["research_report_draft_reason"] == "invalid_json"
    assert result.meta["research_report_draft"] == "fact_fallback_pending_semantic_review"
    assert result._qa_report_supplied
    assert len(result._qa_report_candidates) == 2
    assert all(b["kind"] == "fact" for b in result._qa_report_candidates)
    assert all(b["dimension"] == "已核验政策事实"
               for b in result._qa_report_candidates)
    assert result.structured is None  # no analysis or fact block published yet
    assert "模型未经审核" in result.answer


@pytest.mark.asyncio
async def test_invalid_draft_with_unverified_quotes_never_gets_fact_fallback():
    result = _result()
    result.meta["quote_verification"]["approved"] = 0

    async def invalid(*args, **kwargs):
        return "not JSON"

    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0),
                         settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
                         complete_text=invalid)
    assert not await draft_verified_report(
        result, "分析政策优势与挑战", rt, time.monotonic() + 130)
    assert result.meta["research_report_draft"] == "unavailable_or_invalid"
    assert not result._qa_report_supplied


@pytest.mark.asyncio
async def test_draft_failure_reason_distinguishes_schema_and_json():
    result = _result()
    result.meta["quote_verification"]["approved"] = 2

    async def malformed_schema(*args, **kwargs):
        return json.dumps({"dimensions": ["政策措施"], "blocks": [{"id": "x"}]})

    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0),
                         settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
                         complete_text=malformed_schema)
    assert await draft_verified_report(
        result, "分析政策优势与挑战", rt, time.monotonic() + 130)
    assert result.meta["research_report_draft_reason"] == "schema_invalid"


def test_gap_followup_chooses_retrievable_policy_topic_before_abstract_difference():
    result = _result()
    question = "分析不同数字经济政策在产业扶持、基础设施建设和人才培养方面的异同"
    assert followup_gap(result, question) == "产业扶持"
