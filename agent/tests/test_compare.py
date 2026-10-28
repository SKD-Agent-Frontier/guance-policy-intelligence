"""比较功能的离线单元/集成回归，政策和模型均为明确的测试替身。

经真实 submit 路由、业务模块、证据登记和核验检查结果；FakeKB/ComparisonModel
只控制上游内容与故障。测试不能证明真实语料覆盖或模型语义正确性，真实验收报告
保存在 agent/reports/compare/，不与这里的合成结果混淆。
"""
import json
import runpy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from guance_agent import business
from guance_agent.business.compare import PolicyComparisonModule, SchemeComparisonModule
from guance_agent.business.compare.evidence import Collection, EvidenceUnit, _segments
from guance_agent.business.compare.models import (
    ComparisonPlan,
    GeneratedAnalysis,
    GeneratedComparison,
    parse_model,
    resolve_plan,
)
from guance_agent.business.compare.result import _stage, build_outcome
from guance_agent.business.contract import BusinessError, BusinessOutcome
from guance_agent.main import submit
from guance_agent.schemas import Claim, TaskRequest

from .test_business_integration import FakeKB

DIMENSIONS = ["发展目标", "政策工具", "适用对象"]
BODY_A = "规划目标是培养人才。政策通过培训支持高校毕业生，并安排资金100万元。"
BODY_B = "规划目标是引进人才。政策通过补贴支持企业技术人员，并安排资金200万元。"


class ComparisonKB(FakeKB):
    """可扩展到八对象的合成语料，并提供空检索、坏引文等可控故障。"""

    def __init__(self, count=2):
        super().__init__()
        self.documents = {f"T{i + 1:03}": {
            "title": ("杭州市十四五人才规划" if i == 0 else
                      "绍兴市十四五人才规划" if i == 1 else f"测试人才规划{i + 1}"),
            "body": BODY_A if i % 2 == 0 else BODY_B,
        } for i in range(count)}
        self.bad_quote = False
        self.empty = False
        self.title_filters = []

    def _hit(self, planid):
        hit = super()._hit(planid)
        return hit.model_copy(update={"area": "杭州市" if planid == "T001" else "绍兴市"})

    def find_by_title(self, title, *, area=None, fyp=None):
        self.title_filters.append((title, area, fyp))
        return super().find_by_title(title, area=area, fyp=fyp)

    def verify_quote(self, *args, **kwargs):
        return False if self.bad_quote else super().verify_quote(*args, **kwargs)


class ComparisonModel:
    """四阶段模型替身；mutate 钩子用于检验不合规输出是否真的被生产代码拒绝。

    calls 记录实际调用数，从而验证一次格式修正预算没有按步骤无限放大。
    不伪装模型的推理能力；复核阶段默认回传草稿，只有专门用例才提供修正结果。
    """

    def __init__(self):
        self.calls = []
        self.mutate = None
        self.mutate_analysis = None
        self.mutate_review = None
        self.plan = {"dimensions": DIMENSIONS, "queries": ["人才 培训 补贴"],
                     "evaluation_criteria": []}

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        messages = kwargs["messages"]
        if "你负责解析" in messages[0]["content"]:
            output = self.plan
        elif "本轮审查草稿" in messages[0]["content"]:
            payload = json.loads(messages[1]["content"])
            output = payload["draft"]
            if self.mutate_review:
                self.mutate_review(output, payload)
        elif "你负责比较分析" in messages[0]["content"]:
            payload = json.loads(messages[1]["content"])
            analyses = []
            for obj in payload["objects"]:
                own = [cell for row in payload["matrix"] for cell in row["cells"]
                       if cell["planid"] == obj["planid"] and cell["status"] == "found"]
                basis = [own[0]["cell_id"]] if own else []
                analyses.append({"planid": obj["planid"],
                    "advantages": [{"text": "在所列人才支持事项上提供了政策安排。", "supporting_cells": basis}],
                    "limitations": [{"text": "分析结论仅适用于所列政策范围。", "supporting_cells": basis}],
                    "applicability": [{"text": "可供与原文支持范围相符的对象进一步核对。", "supporting_cells": basis}]})
            differences = [{"dimension": row["dimension"],
                            "text": "两项政策均提出人才支持安排。",
                            "supporting_cells": [c["cell_id"] for c in row["cells"]
                                                 if c["status"] == "found"],
                            "comparable": True, "reason": ""}
                           for row in payload["matrix"]]
            output = {"differences": differences, "scheme_analysis": analyses
                      if payload["mode"] == "scheme_analysis" else []}
            if self.mutate_analysis:
                self.mutate_analysis(output, payload)
        else:
            payload = json.loads(messages[1]["content"])
            output = self.generate(payload)
            if self.mutate:
                self.mutate(output, payload)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(output, ensure_ascii=False)),
            finish_reason="stop")], usage=SimpleNamespace(prompt_tokens=20, completion_tokens=30))

    @staticmethod
    def generate(payload):
        # 故意在选择响应中夹带旧式 text 和分析，用集成断言确认生产流程会重新
        # 用原文填事实，并只接受后续专门分析/复核步骤产生的判断。
        facts, differences, schemes = [], [], []
        for index, dimension in enumerate(payload["dimensions"]):
            ids = []
            for obj in payload["objects"]:
                pid = obj["planid"]
                units = [u for u in payload["evidence"] if u["planid"] == pid]
                if units:
                    facts.append({"planid": pid, "dimension": dimension,
                                  "text": units[0]["text"],
                                  "evidence_ids": [units[0]["id"]]})
                    ids.append(f"d{index}:{pid}")
            differences.append({"dimension": dimension, "text": "两项政策均提出人才支持安排。",
                                "supporting_cells": ids, "comparable": True, "reason": ""})
        for obj in payload["objects"]:
            cid = f"d0:{obj['planid']}"
            schemes.append({"planid": obj["planid"],
                            "advantages": [{"text": "在所列人才支持事项上提供了政策安排。",
                                            "supporting_cells": [cid]}],
                            "limitations": [{"text": "分析结论仅适用于所列政策范围。",
                                            "supporting_cells": [cid]}],
                            "applicability": [{"text": "可供与原文支持范围相符的对象进一步核对。",
                                               "supporting_cells": [cid]}]})
        return {"facts": facts, "differences": differences, "scheme_analysis": schemes}


@pytest.fixture
def compare_setup(env, monkeypatch):
    """只替换外部 KB/模型连接；保留生产入口和业务注册，使范围与降级测试有意义。"""
    from guance_agent import orchestrator, runtime
    settings = replace(env[0], llm_base_url="https://example.invalid/v1", llm_api_key="test-key")
    kb, model = ComparisonKB(), ComparisonModel()
    client = SimpleNamespace(chat=SimpleNamespace(completions=model))
    monkeypatch.setattr(orchestrator, "get_kb", lambda _: kb)
    monkeypatch.setattr(runtime, "get_client", lambda _: client)
    monkeypatch.setattr(business, "_MODULES", {})
    business.register(PolicyComparisonModule())
    business.register(SchemeComparisonModule())
    return settings, kb, model


@pytest.mark.parametrize("task_type", ["compare", "compare_plans"])
async def test_actual_submit_builds_two_sided_matrix(compare_setup, task_type):
    # “完整 JSON”还不够：同时核对单元格顺序、逐字引文、来源对象及统一核验状态。
    settings, kb, model = compare_setup
    result = await submit("比较两项人才政策", settings=settings, task_type=task_type,
                          targets=["T001", "T002"])
    assert result.status == "completed" and result.evidence_status == "verified"
    assert result.structured["dimensions"] == DIMENSIONS
    assert len(result.structured["matrix"]) == 3
    assert len(result.claims) == 6
    for row in result.structured["matrix"]:
        assert row["difference"]["status"] == "supported"
        assert [c["planid"] for c in row["cells"]] == ["T001", "T002"]
        for cell in row["cells"]:
            assert cell["status"] == "found"
            assert cell["quotes"] and all(q in kb.documents[cell["planid"]]["body"]
                                          for q in cell["quotes"])
            assert all(q.endswith(("。","；",";","！","!","？","?")) for q in cell["quotes"])
            assert all(cid.startswith(cell["planid"]) for cid in cell["chunk_ids"])
    assert len(model.calls) == 4
    assert all(call[-1] == ("T001", "T002") for call in kb.search_calls)
    assert result.route["targets"] == ["T001", "T002"]
    if task_type == "compare_plans":
        assert len(result.structured["scheme_analysis"]) == 2
        assert result.structured["evaluation_criteria"] == []


async def test_explicit_dimensions_override_model_plan(compare_setup):
    settings, _, model = compare_setup
    model.plan["dimensions"] = ["错误维度"]
    result = await submit("比较政策", settings=settings, task_type="compare",
                          targets=["T001", "T002"], dimensions=["资金支持", "资金支持", "执行条件"])
    assert result.structured["dimensions"] == ["资金支持", "执行条件"]


async def test_missing_fact_downgrades_difference_without_inventing_absence(compare_setup):
    settings, _, model = compare_setup
    model.mutate = lambda output, _: output["facts"].pop()
    result = await submit("比较政策", settings=settings, task_type="compare",
                          targets=["T001", "T002"])
    row = result.structured["matrix"][-1]
    assert row["cells"][-1]["status"] == "insufficient"
    assert row["difference"]["status"] == "insufficient"
    assert result.evidence_status == "insufficient"
    assert "政策没有" not in result.answer


@pytest.mark.parametrize("corruption", ["cross_object", "unknown_evidence", "new_number"])
async def test_invalid_fact_reference_cannot_enter_claims(compare_setup, corruption):
    settings, _, model = compare_setup
    def mutate(output, payload):
        fact = output["facts"][0]
        if corruption == "cross_object":
            fact["evidence_ids"] = [u["id"] for u in payload["evidence"] if u["planid"] == "T002"]
        elif corruption == "unknown_evidence":
            fact["evidence_ids"] = ["invented"]
        else:
            fact["text"] = "安排资金999万元。"
    model.mutate = mutate
    result = await submit("比较政策", settings=settings, task_type="compare", targets=["T001", "T002"])
    assert result.structured["matrix"][0]["cells"][0]["status"] == (
        "found" if corruption == "new_number" else "insufficient")
    assert all("999" not in claim.text for claim in result.claims)


async def test_base_quote_rejection_clears_matrix_and_dependent_analysis(compare_setup):
    # 用后端明确拒绝引句，验证基础层会同时撤回矩阵和派生分析，而非仅删正文引用。
    settings, kb, _ = compare_setup
    kb.bad_quote = True
    result = await submit("比较政策", settings=settings, task_type="compare_plans",
                          targets=["T001", "T002"])
    assert result.structured == {} and result.claims == []
    assert "两项政策均" not in result.answer
    assert result.meta["quote_verification"]["removed_claims"] == 6


async def test_empty_retrieval_does_not_call_analysis_or_claim_missing_policy(compare_setup):
    settings, kb, model = compare_setup
    kb.empty = True
    result = await submit("比较政策", settings=settings, task_type="compare", targets=["T001", "T002"])
    assert len(model.calls) == 1
    assert result.claims == [] and "matrix" not in result.structured
    assert result.evidence_status == "insufficient"


@pytest.mark.parametrize("count", [3, 8])
async def test_multi_object_coverage_and_quote_budget(compare_setup, monkeypatch, count):
    settings, _, _ = compare_setup
    kb = ComparisonKB(count=count)
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda _: kb)
    targets = list(kb.documents)
    result = await submit("比较人才政策", settings=settings, task_type="compare", targets=targets)
    assert result.status == "completed" and result.structured
    assert len(result.claims) == count * 3
    assert {ref.planid for ref in result.evidence_refs} == set(targets)
    assert len(kb.read_calls) + len(kb.search_calls) <= settings.max_tool_calls


@pytest.mark.parametrize("task_type", ["compare", "compare_plans"])
@pytest.mark.parametrize("quote_count", [120, 121])
async def test_comparison_quote_budget_boundary(compare_setup, monkeypatch, task_type, quote_count):
    settings, _, _ = compare_setup

    class QuoteBudgetModule:
        name = task_type

        async def __call__(self, task, ctx, services):
            found = await services.search("policy", top_k=1)
            assert found.status == "ok"
            hit = found.data[0]
            read = await services.read(hit.planid, chunk_id=hit.chunk_id)
            assert read.status == "ok"
            claims = [Claim(text="claim-" + "x" * index,
                            chunk_ids=[hit.chunk_id], quotes=[BODY_A])
                      for index in range(1, quote_count + 1)]
            return BusinessOutcome(
                answer="\n".join(claim.text for claim in claims),
                claims=claims,
                structured={"claim_count": quote_count},
            )

    monkeypatch.setitem(business._MODULES, task_type, QuoteBudgetModule())
    result = await submit("compare policies", settings=settings, task_type=task_type,
                          targets=["T001", "T002"])

    verification = result.meta["quote_verification"]
    assert verification["checked"] == 120
    assert verification["approved"] == 120
    if quote_count == 120:
        assert len(result.claims) == 120
        assert verification["unverified"] == 0
        assert result.structured == {"claim_count": 120}
        assert result.evidence_status == "verified"
    else:
        assert len(result.claims) == 120
        assert verification["unverified"] == 1
        assert verification["removed_claims"] == 1
        assert result.structured == {}
        assert "claim-" + "x" * 121 not in result.answer
        assert "claim-" + "x" * 120 in result.answer
        assert result.evidence_status == "insufficient"


@pytest.mark.parametrize("task_type", ["compare", "compare_plans"])
async def test_two_unique_cross_region_titles_resolve_together(compare_setup, task_type):
    settings, kb, _ = compare_setup
    result = await submit("比较《杭州市十四五人才规划》和《绍兴市十四五人才规划》",
                          settings=settings, task_type=task_type)
    assert result.structured and result.route["targets"] == ["T001", "T002"]
    assert all(area is None and fyp is None for _, area, fyp in kb.title_filters)


@pytest.mark.parametrize("task_type", ["compare", "compare_plans"])
async def test_single_target_rejected_at_entry(compare_setup, task_type):
    settings, kb, model = compare_setup
    result = await submit("比较", settings=settings, task_type=task_type, targets=["T001"])
    assert result.error.code == "INVALID_INPUT"
    assert "至少需要两个" in result.error.message
    assert model.calls == [] and kb.read_calls == []


async def test_malformed_model_result_is_output_parse(compare_setup, monkeypatch):
    settings, _, model = compare_setup
    async def malformed(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{broken"))])
    monkeypatch.setattr(model, "create", malformed)
    result = await submit("比较", settings=settings, task_type="compare", targets=["T001", "T002"])
    assert result.error.code == "OUTPUT_PARSE" and result.structured == {}


def test_unique_json_and_criteria_must_come_from_question():
    task = TaskRequest(run_id="r", session_id="s", created_at="", question="优先考虑覆盖范围广")
    plan = resolve_plan(ComparisonPlan(evaluation_criteria=["覆盖范围广", "成本最低"]), task, scheme=True)
    assert plan.evaluation_criteria == ["覆盖范围广"]
    assert plan.dimensions == ["适用对象", "支持措施", "申请或执行条件"]
    assert parse_model('```json\n{"dimensions":["目标"]}\n```', ComparisonPlan).dimensions == ["目标"]
    with pytest.raises(BusinessError):
        parse_model('{} {}', ComparisonPlan)


def test_cross_period_and_nature_cannot_be_silently_treated_as_comparable():
    task = TaskRequest(run_id="r", session_id="s", created_at="", question="比较",
                       targets=["A", "B"])
    plan = ComparisonPlan(dimensions=["目标"])
    collection = Collection(objects=[
        {"planid": "A", "title": "某市十四五规划", "fyp": "十四五"},
        {"planid": "B", "title": "某市政府工作报告", "fyp": "政府工作报告"}], units=[
            EvidenceUnit("e0", "A", "a", "目标为新增人才100人。"),
            EvidenceUnit("e1", "B", "b", "已完成人才引进200人。")])
    generated = GeneratedComparison.model_validate({"facts": [
        {"planid": "A", "dimension": "目标", "text": "目标为新增人才100人。", "evidence_ids": ["e0"]},
        {"planid": "B", "dimension": "目标", "text": "已完成人才引进200人。", "evidence_ids": ["e1"]}],
        "differences": [{"dimension": "目标", "text": "乙的目标高于甲。",
                         "supporting_cells": ["d0:A", "d0:B"]}]})
    outcome = build_outcome(task, plan, collection, generated, scheme=False)
    assert outcome.structured["comparability"]["comparable"] is False
    assert "文件性质不同" in outcome.structured["comparability"]["reason"]
    assert "规划期" in outcome.structured["comparability"]["reason"]
    assert outcome.structured["matrix"][0]["difference"]["status"] == "insufficient"


async def test_absolute_winner_and_unfounded_absence_are_not_retained(compare_setup):
    settings, _, model = compare_setup
    def mutate(output, _):
        output["scheme_analysis"][0]["advantages"][0]["text"] = "甲是最佳方案。"
        output["scheme_analysis"][0]["limitations"][0]["text"] = "甲没有支持措施。"
    model.mutate_analysis = mutate
    result = await submit("分析两个方案", settings=settings, task_type="compare_plans",
                          targets=["T001", "T002"])
    first = result.structured["scheme_analysis"][0]
    assert first["advantages"] == [] and first["limitations"] == []
    assert "最佳" not in result.answer and "没有支持措施" not in result.answer


async def test_candidates_from_later_queries_are_not_drowned_by_first_query(compare_setup, monkeypatch):
    settings, _, model = compare_setup
    model.plan["queries"] = ["发展目标", "申请条件"]
    class ManyHitsKB(ComparisonKB):
        def search(self, query, area, fyp, top_k, *, planids=None):
            self.search_calls.append((query, area, fyp, top_k, planids))
            return [self._hit(pid).model_copy(update={"chunk_id": f"{pid}-{query}-{i}"})
                    for pid in planids for i in range(4)]
    kb = ManyHitsKB()
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda _: kb)
    result = await submit("比较政策", settings=settings, task_type="compare", targets=["T001", "T002"])
    assert result.structured
    for pid in ("T001", "T002"):
        reads = [chunk for target, _, chunk, _ in kb.read_calls if target == pid and chunk]
        assert any("申请条件" in chunk for chunk in reads)


@pytest.mark.parametrize("raw", ['{"facts":[{"text":"partial"}', '{"unexpected":[]}'])
def test_incomplete_or_wrong_model_object_is_rejected(raw):
    with pytest.raises(BusinessError):
        parse_model(raw, GeneratedComparison)


async def test_evaluation_criteria_are_user_supplied_without_automatic_ranking(compare_setup):
    settings, _, model = compare_setup
    model.plan["evaluation_criteria"] = ["覆盖范围", "成本最低"]
    result = await submit("按覆盖范围分析方案适用条件", settings=settings,
                          task_type="compare_plans", targets=["T001", "T002"])
    assert result.structured["evaluation_criteria"] == ["覆盖范围"]
    assert "排名" not in result.answer


async def test_relative_advantage_requires_evidence_for_other_scheme_too(compare_setup):
    settings, _, model = compare_setup
    def mutate(output, _):
        output["scheme_analysis"][0]["advantages"][0]["text"] = "相比乙，甲更适合人才发展。"
    model.mutate_analysis = mutate
    result = await submit("分析两个方案", settings=settings, task_type="compare_plans",
                          targets=["T001", "T002"])
    assert result.structured["scheme_analysis"][0]["advantages"] == []


async def test_acquisition_respects_small_tool_budget(compare_setup):
    settings, kb, _ = compare_setup
    settings = replace(settings, max_tool_calls=5)
    result = await submit("比较政策", settings=settings, task_type="compare",
                          targets=["T001", "T002"])
    assert result.status == "completed" and result.structured
    assert len(kb.read_calls) + len(kb.search_calls) <= 5


async def test_multi_period_unique_titles_keep_correct_document_identity(compare_setup):
    settings, kb, _ = compare_setup
    kb.documents["T001"]["title"] = "杭州市十三五人才规划"
    result = await submit("比较《杭州市十三五人才规划》和《绍兴市十四五人才规划》",
                          settings=settings, task_type="compare")
    assert result.route["targets"] == ["T001", "T002"]
    assert result.structured["objects"][0]["title"] == "杭州市十三五人才规划"
    assert "元数据不一致" in result.structured["comparability"]["reason"]


async def test_ambiguous_title_does_not_execute_partial_comparison(compare_setup):
    settings, kb, model = compare_setup
    kb.ambiguous = True
    result = await submit("比较《杭州市十四五人才规划》和《绍兴市十四五人才规划》",
                          settings=settings, task_type="compare")
    assert not result.structured and model.calls == []
    assert "无法唯一定位" in result.answer


async def test_acceptance_validator_checks_actual_claim_cell_binding(compare_setup):
    settings, _, _ = compare_setup
    audit = runpy.run_path(str(Path(__file__).parents[1] / "scripts/live_compare_acceptance.py"))["audit_result"]
    result = await submit("比较政策", settings=settings, task_type="compare", targets=["T001", "T002"])
    assert audit(result, ["T001", "T002"]) == []
    result.structured["matrix"][0]["cells"][0]["text"] = "与Claim不一致的事实"
    assert "cell_without_matching_claim" in audit(result, ["T001", "T002"])


def test_production_registry_has_both_comparison_modules():
    assert isinstance(business.get_module("compare"), PolicyComparisonModule)
    assert isinstance(business.get_module("compare_plans"), SchemeComparisonModule)


async def test_hit_metadata_none_is_not_backfilled_from_catalog(compare_setup, monkeypatch):
    settings, _, _ = compare_setup
    class CatalogMetadataKB(ComparisonKB):
        def _hit(self, planid):
            return super()._hit(planid).model_copy(update={"fyp": None})
        def read(self, planid, section_idx, max_chars, **kwargs):
            passage = super().read(planid, section_idx, max_chars, **kwargs)
            if section_idx is None and kwargs.get("chunk_id") is None:
                return passage.model_copy(update={"fyp": "第十四个五年规划"})
            return passage
    kb = CatalogMetadataKB()
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda _: kb)
    result = await submit("比较", settings=settings, task_type="compare", targets=["T001", "T002"])
    assert all(obj["fyp"] is None for obj in result.structured["objects"])
    assert "时间口径" in result.structured["comparability"]["reason"]


@pytest.mark.parametrize("question",["比较本期发展目标","不要把历史成绩当成本期发展目标"])
def test_historical_background_cannot_be_used_as_current_plan_objective(question):
    task = TaskRequest(run_id="r", session_id="s", created_at="", question=question, targets=["A", "B"])
    plan = ComparisonPlan(dimensions=["发展目标"])
    collection = Collection(objects=[{"planid":"A","title":"甲市十四五规划","fyp":"十四五"},
                                     {"planid":"B","title":"乙市十四五规划","fyp":"十四五"}],units=[
        EvidenceUnit("e0","A","a","十三五人才总量已达到10万人。","一、发展背景 / 现实基础"),
        EvidenceUnit("e1","B","b","十四五目标新增人才20万人。","二、主要目标")])
    generated=GeneratedComparison.model_validate({"facts":[
        {"planid":"A","dimension":"发展目标","text":"十三五人才总量已达到10万人。","evidence_ids":["e0"]},
        {"planid":"B","dimension":"发展目标","text":"十四五目标新增人才20万人。","evidence_ids":["e1"]}]})
    outcome=build_outcome(task,plan,collection,generated,scheme=False)
    assert outcome.structured["matrix"][0]["cells"][0]["status"] == "insufficient"
    assert outcome.structured["matrix"][0]["cells"][1]["status"] == "found"
    assert all("10万人" not in claim.text for claim in outcome.claims)


def test_basic_research_section_is_not_historical_background():
    unit=EvidenceUnit("e0","A","a","培养基础研究人才。","三、主要任务 / 基础研究人才队伍")
    assert not unit.historical


async def test_catalog_goal_and_implementation_sections_are_prioritized(compare_setup, monkeypatch):
    settings, _, _ = compare_setup
    class CatalogKB(ComparisonKB):
        def read(self,planid,section_idx,max_chars,**kwargs):
            passage=super().read(planid,section_idx,max_chars,**kwargs)
            if section_idx is None and kwargs.get("chunk_id") is None:
                return passage.model_copy(update={"is_index":True,"sections":[
                    {"idx":1,"path":"二、主要目标"},{"idx":2,"path":"六、实施保障"}]})
            return passage
    kb=CatalogKB()
    monkeypatch.setattr("guance_agent.orchestrator.get_kb",lambda _:kb)
    result=await submit("比较目标及执行条件",settings=settings,task_type="compare",
                        targets=["T001","T002"],dimensions=["发展目标","执行条件"])
    assert result.status == "completed"
    for pid in ["T001","T002"]:
        sections=[idx for target,idx,_,_ in kb.read_calls if target==pid and idx is not None]
        assert sections[:2] == [1,2]


@pytest.mark.parametrize("task_type",["compare","compare_plans"])
async def test_one_bounded_format_repair_keeps_evidence_checks(compare_setup, monkeypatch, task_type):
    # 在证据选择阶段制造截断，只有语法修正响应回传；之后仍走完整事实与依赖校验。
    settings,_,model=compare_setup
    original=model.create
    saved=None
    async def broken_once(**kwargs):
        nonlocal saved
        if "只修正" in kwargs['messages'][0]['content']:
            model.calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=saved),finish_reason='stop')],usage=None)
        reply=await original(**kwargs)
        if saved is None and '本轮只生成事实' in kwargs['messages'][0]['content']:
            saved=reply.choices[0].message.content
            reply.choices[0].message.content='{broken'
        return reply
    monkeypatch.setattr(model,'create',broken_once)
    result=await submit('比较人才政策',settings=settings,task_type=task_type,targets=['T001','T002'])
    assert result.structured and len(result.claims)==6
    assert len(model.calls)==5


async def test_format_repair_is_not_repeated_across_stages(compare_setup, monkeypatch):
    settings,_,model=compare_setup
    calls=[]
    async def reply(**kwargs):
        calls.append(kwargs)
        raw=json.dumps(model.plan) if len(calls)==2 else '{broken'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw),finish_reason='stop')],usage=None)
    monkeypatch.setattr(model,'create',reply)
    result=await submit('比较人才政策',settings=settings,task_type='compare',targets=['T001','T002'])
    assert result.error.code=='OUTPUT_PARSE' and len(calls)==3


def test_department_reporting_cannot_be_rewritten_as_personal_eligibility():
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='比较执行条件',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['执行条件'])
    text='各地各部门各单位重点工作事项，及时按程序报人才工作领导小组研究审议。'
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','A','a',text)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'A','dimension':'执行条件',
        'text':'人才个人必须报人才工作领导小组审议后才能享受支持。','evidence_ids':['e0']}]})
    result=build_outcome(task,plan,collection,generated,scheme=False)
    assert result.claims[0].text == text
    assert result.structured['matrix'][0]['cells'][0]['status']=='found'
    assert '人才个人必须' not in result.answer


async def test_completed_chunk_is_not_reread_after_its_section(compare_setup,monkeypatch):
    settings,_,_=compare_setup
    class SectionKB(ComparisonKB):
        def read(self,planid,section_idx,max_chars,**kwargs):
            passage=super().read(planid,section_idx,max_chars,**kwargs)
            if section_idx is None and kwargs.get('chunk_id') is None:
                return passage.model_copy(update={'is_index':True,'sections':[{'idx':1,'path':'主要目标'}]})
            return passage
    kb=SectionKB()
    monkeypatch.setattr('guance_agent.orchestrator.get_kb',lambda _:kb)
    result=await submit('比较目标',settings=settings,task_type='compare',targets=['T001','T002'],dimensions=['发展目标'])
    assert result.structured
    assert len(kb.read_calls)==4
    assert not any(chunk for _,_,chunk,_ in kb.read_calls)


def test_scheme_analysis_cannot_turn_department_reporting_into_personal_approval():
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='比较执行条件',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['执行条件'])
    text='各地各部门各单位重点工作事项，及时按程序报人才工作领导小组研究审议。'
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','A','a',text)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'A','dimension':'执行条件',
        'text':text,'evidence_ids':['e0']}],'scheme_analysis':[{'planid':'A','limitations':[
        {'text':'申请人必须报人才工作领导小组审议后才能享受支持。','supporting_cells':['d0:A']}]}]})
    outcome=build_outcome(task,plan,collection,generated,scheme=True)
    assert outcome.structured['scheme_analysis'][0]['limitations']==[]


def test_evidence_keeps_cross_line_sentences_and_drops_partial_chunk_edges():
    text='上一句残尾。各地各部门重点工作事项，\n及时按程序报领导小组研究审议。后句未完'
    units=_segments(text,['工作事项'],1800,start_complete=False)
    assert units==['各地各部门重点工作事项，\n及时按程序报领导小组研究审议。']
    assert not _segments('超过预算的完整原句不得截成假句子。',['原句'],8)


def test_plan_context_does_not_mislabel_unmarked_clauses_as_completed_results():
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='比较目标',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['目标'])
    collection=Collection(objects=[{'planid':pid,'title':f'{pid}市十四五规划','fyp':'十四五'}
                                   for pid in ['A','B']],units=[
        EvidenceUnit('e0','A','a','目标是培养技术人才。'),
        EvidenceUnit('e1','B','b','支持人才创业团队。')])
    generated=GeneratedComparison.model_validate({'facts':[
        {'planid':pid,'dimension':'目标','evidence_ids':[f'e{i}']}
        for i,pid in enumerate(['A','B'])]})
    outcome=build_outcome(task,plan,collection,generated,scheme=False)
    reason=outcome.structured['matrix'][0]['comparability']['reason']
    assert '完成情况存在差别' not in reason


def test_cultivation_period_sentence_keeps_its_own_target_context():
    text='围绕民营企业遴选培养优秀青年企业家。培养期内在信贷和培训方面给予支持。'
    units=_segments(text,['培养期内'],1800)
    assert units[0] == text
    assert not _segments('培养期内在信贷和培训方面给予支持。',['培养期内'],1800)


@pytest.mark.parametrize('unsupported', ['尚未到考核节点。','尚未完全落地。','已全面建成人才平台。'])
async def test_analysis_cannot_invent_current_implementation_status(compare_setup,unsupported):
    settings,_,model=compare_setup
    def mutate(output,_):
        output['differences'][0]['text']=unsupported
        output['differences'][0].update(comparable=False,reason='两地经济发展阶段不同。')
    model.mutate_analysis=mutate
    result=await submit('比较政策',settings=settings,task_type='compare',targets=['T001','T002'])
    assert result.structured['matrix'][0]['difference']['status']=='insufficient'
    assert unsupported not in result.answer
    assert '经济发展阶段不同' not in result.answer


async def test_analysis_review_uses_the_same_canonical_facts_without_rewriting_them(compare_setup):
    settings,_,model=compare_setup
    def initial(output,_):
        output['scheme_analysis'][0]['advantages'][0]['text']='住房补贴与人才培养相挂钩。'
    def review(output,payload):
        assert payload['matrix'][0]['cells'][0]['text'] in BODY_A
        output['scheme_analysis'][0]['advantages'][0]['text']='所列条款提出人才培养支持安排。'
    model.mutate_analysis=initial
    model.mutate_review=review
    result=await submit('分析方案',settings=settings,task_type='compare_plans',targets=['T001','T002'])
    assert result.structured['scheme_analysis'][0]['advantages'][0]['text']=='就本次所列条款而言，所列条款提出人才培养支持安排。'
    assert '相挂钩' not in result.answer
    assert len(model.calls)==4


async def test_unsupported_linking_of_separate_measures_is_withdrawn(compare_setup):
    settings,_,model=compare_setup
    def mutate(output,_):
        output['scheme_analysis'][0]['advantages'][0]['text']='住房补贴与人才培养相挂钩。'
    model.mutate_analysis=mutate
    result=await submit('分析方案',settings=settings,task_type='compare_plans',targets=['T001','T002'])
    assert result.structured['scheme_analysis'][0]['advantages']==[]


async def test_acceptance_reports_and_private_session_paths_are_separate(env,tmp_path,monkeypatch):
    script=runpy.run_path(str(Path(__file__).parents[1] / 'scripts/live_compare_acceptance.py'))
    run=script['run']
    settings=replace(env[0],llm_base_url='https://example.invalid/v1',llm_api_key='test-key')
    env_file=tmp_path/'model.env'
    env_file.write_text('',encoding='utf-8')
    args=SimpleNamespace(env_file=env_file,output_dir=Path('reports'),private_dir=Path('private'),
                         store_project=tmp_path/'store')
    captured=[]
    class StopBeforeRag(RuntimeError):
        pass
    def stop(settings):
        captured.append(settings)
        raise StopBeforeRag()
    async def close():
        pass
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(run.__globals__,'load_settings',lambda _:settings)
    monkeypatch.setitem(run.__globals__,'warm_up',stop)
    monkeypatch.setitem(run.__globals__,'close_kb',lambda:None)
    monkeypatch.setitem(run.__globals__,'aclose_client',close)
    with pytest.raises(StopBeforeRag):
        await run(args)
    assert args.output_dir == tmp_path/'reports'
    assert captured[0].session_path == tmp_path/'private/sessions.sqlite'
    assert captured[0].run_log == tmp_path/'private/runs.jsonl'
    assert captured[0].log_content is False


@pytest.mark.parametrize('raw', ['{}', '{"facts":[]}'])
def test_analysis_response_requires_its_own_stage_field(raw):
    # 语法合法但属于错误阶段的对象不能利用默认空数组静默变成“证据不足”。
    with pytest.raises(BusinessError) as exc:
        parse_model(raw, GeneratedAnalysis)
    assert exc.value.code == 'OUTPUT_PARSE'


def test_analysis_explicitly_empty_differences_remain_valid():
    # 单侧缺证时明确返回空差异是正常降级，不应触发格式修正或伪造配对结论。
    assert parse_model('{"differences":[]}', GeneratedAnalysis).differences == []


@pytest.mark.parametrize('review', [False, True])
async def test_wrong_stage_analysis_json_uses_one_shared_format_repair(compare_setup,monkeypatch,review):
    settings,_,model=compare_setup
    original=model.create
    saved=None

    async def misplaced_once(**kwargs):
        nonlocal saved
        system=kwargs['messages'][0]['content']
        if '只修正' in system:
            model.calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=saved),finish_reason='stop')],usage=None)
        reply=await original(**kwargs)
        if (saved is None and '你负责比较分析' in system
                and ('本轮审查草稿' in system) == review):
            saved=reply.choices[0].message.content
            reply.choices[0].message.content='{"facts":[]}'
        return reply

    monkeypatch.setattr(model,'create',misplaced_once)
    result=await submit('比较人才政策',settings=settings,task_type='compare',targets=['T001','T002'])
    assert result.evidence_status == 'verified'
    assert all(row['difference']['status']=='supported' for row in result.structured['matrix'])
    assert len(model.calls) == 5


@pytest.mark.parametrize('text', [
    '在建筑、黄酒等传统行业开展职业职称评定。',
    '在建材行业引进技术人才。',
    '在建设人才队伍时完善政策。',
    '在建立制度的基础上提供支持。',
    '在建成的平台上开展创新。',
])
def test_industry_and_action_words_do_not_imply_in_progress_projects(text):
    assert '推进过程' not in _stage(text)


@pytest.mark.parametrize('text', [
    '加快在建项目的施工。', '推进在建工程。', '该项目目前在建。',
    '该平台正在建设。', '持续推进人才服务建设。',
])
def test_explicit_in_progress_project_status_is_retained(text):
    assert '推进过程' in _stage(text)


def test_government_work_assessment_is_not_a_personal_eligibility_condition():
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='分析执行条件',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['执行条件'])
    text='建立考核评估机制，每年年底组织实施年度人才工作考核。支持高校及科研院所“一事一议”引进培养高端人才。'
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','B','b',text)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'B','dimension':'执行条件','evidence_ids':['e0']}],
        'scheme_analysis':[{'planid':'B','limitations':[{
            'text':'高端人才引进培养需通过年度考核评估，支持取决于绩效结果。','supporting_cells':['d0:B']}]}]})
    result=build_outcome(task,plan,collection,generated,scheme=True)
    assert result.structured['scheme_analysis'][1]['limitations']==[]
    assert any(gap.get('planid')=='B' and gap.get('field')=='limitations'
               and gap['reason']=='unsupported_personal_condition' for gap in result.structured['analysis_gaps'])


async def test_reviewer_receives_validation_feedback_without_changing_facts(compare_setup):
    settings,_,model=compare_setup
    def initial(output,_):
        output['scheme_analysis'][1]['limitations'][0]['supporting_cells']=['missing']
    def review(output,payload):
        assert any(gap.get('planid')=='T002' and gap.get('field')=='limitations'
                   and gap['reason']=='invalid_dependencies' for gap in payload['validation_feedback'])
        output['scheme_analysis'][1]['limitations'][0]['supporting_cells']=['d0:T002']
    model.mutate_analysis=initial
    model.mutate_review=review
    result=await submit('分析方案',settings=settings,task_type='compare_plans',targets=['T001','T002'])
    assert result.evidence_status=='verified' and len(result.claims)==6
    assert result.structured['scheme_analysis'][1]['limitations']
    assert len(model.calls)==4


def test_explicit_personal_assessment_condition_remains_usable():
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='分析执行条件',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['执行条件'])
    text='责任单位开展人才工作考核。申请人须通过年度考核方可继续享受本项补贴。'
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','B','b',text)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'B','dimension':'执行条件','evidence_ids':['e0']}],
        'scheme_analysis':[{'planid':'B','limitations':[{
            'text':'申请人须通过年度考核方可继续享受本项补贴。','supporting_cells':['d0:B']}]}]})
    result=build_outcome(task,plan,collection,generated,scheme=True)
    assert result.structured['scheme_analysis'][1]['limitations']


async def test_scheme_missing_body_explains_gaps_and_keeps_other_policy_facts(compare_setup,monkeypatch):
    settings,kb,_=compare_setup
    from guance_agent.kb import KBNoText
    read=kb.read
    def missing(planid,*args,**kwargs):
        if planid=='T002':
            raise KBNoText(planid,'受控正文缺证')
        return read(planid,*args,**kwargs)
    monkeypatch.setattr(kb,'read',missing)
    result=await submit('分析方案',settings=settings,task_type='compare_plans',targets=['T001','T002'])
    assert result.evidence_status=='insufficient'
    assert result.claims and all(ref.planid=='T001' for ref in result.evidence_refs)
    assert all(row['difference']['status']=='insufficient' for row in result.structured['matrix'])
    analysis=result.structured['scheme_analysis'][1]
    for field in ('advantages','limitations','applicability'):
        assert analysis[field]==[]
        assert any(gap.get('planid')=='T002' and gap['field']==field
                   and gap['reason']=='source_evidence_missing' for gap in result.structured['analysis_gaps'])
    assert '相关原文证据不足' in result.answer


async def test_analysis_gaps_distinguish_model_omission_from_missing_source(compare_setup):
    settings,_,model=compare_setup
    def omit(output,_):
        output['scheme_analysis'][1]['limitations']=[]
    model.mutate_analysis=omit
    result=await submit('分析方案',settings=settings,task_type='compare_plans',targets=['T001','T002'])
    assert result.evidence_status=='insufficient'
    assert any(gap.get('planid')=='T002' and gap['field']=='limitations'
               and gap['reason']=='analysis_not_generated' for gap in result.structured['analysis_gaps'])


@pytest.mark.parametrize('source,accepted',[
    ('突出用好人才创业投资基金，为创业不同阶段提供精准融资支持。',False),
    ('设立人才创业投资基金，为创业不同阶段提供精准融资支持。',True),
])
def test_using_a_fund_cannot_be_rewritten_as_establishing_it(source,accepted):
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='比较工具',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['政策工具'])
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[
        EvidenceUnit('e0','A','a','规划提出优化政务服务流程。'),EvidenceUnit('e1','B','b',source)])
    generated=GeneratedComparison.model_validate({'facts':[
        {'planid':pid,'dimension':'政策工具','evidence_ids':[f'e{i}']} for i,pid in enumerate(['A','B'])],
        'differences':[{'dimension':'政策工具','text':'甲规划优化政务服务，乙规划设立人才创业投资基金。',
                        'supporting_cells':['d0:A','d0:B']}]})
    result=build_outcome(task,plan,collection,generated,scheme=False)
    assert (result.structured['matrix'][0]['difference']['status']=='supported') is accepted
    assert any(gap['reason']=='unsupported_fund_creation' for gap in result.structured['analysis_gaps']) is not accepted


@pytest.mark.parametrize('text,accepted',[
    ('两份规划均提出人才支持安排，不能据此认定已完成。',True),
    ('两份规划均提出人才支持安排，不代表已完成。',True),
    ('两份规划均提出人才支持安排，但尚未完成。',False),
    ('两份规划的人才支持已经完成。',False),
])
async def test_progress_guard_distinguishes_epistemic_caution_from_status_claim(compare_setup,text,accepted):
    settings,_,model=compare_setup
    def mutate(output,_):
        output['differences'][0]['text']=text
    model.mutate_analysis=mutate
    result=await submit('比较政策',settings=settings,task_type='compare',targets=['T001','T002'])
    assert (result.structured['matrix'][0]['difference']['status']=='supported') is accepted


async def test_persistently_empty_selection_stays_insufficient_after_one_guided_review(compare_setup):
    settings,_,model=compare_setup
    model.mutate=lambda output,_:output.update(facts=[])
    result=await submit('比较政策',settings=settings,task_type='compare',targets=['T001','T002'])
    assert len(model.calls)==3
    assert result.evidence_status=='insufficient' and result.claims==[]
    assert 'matrix' not in result.structured


async def test_guided_empty_selection_review_keeps_canonical_facts_and_call_budget(compare_setup):
    settings,_,model=compare_setup
    attempts=[]
    def empty_once(output,payload):
        attempts.append(payload)
        if len(attempts)==1:
            output['facts']=[]
        else:
            assert payload['selection_feedback']['reason']=='empty_selection_with_read_candidates'
    model.mutate=empty_once
    result=await submit('分析方案',settings=settings,task_type='compare_plans',targets=['T001','T002'])
    assert result.evidence_status=='verified' and len(result.claims)==6
    assert len(model.calls)==5 and len(attempts)==2
    assert all(any(claim.text in body for body in (BODY_A,BODY_B)) for claim in result.claims)


async def test_empty_selection_review_and_json_repair_share_one_correction(compare_setup,monkeypatch):
    settings,_,model=compare_setup
    model.mutate=lambda output,_:output.update(facts=[])
    create=model.create
    async def malformed_review(**kwargs):
        reply=await create(**kwargs)
        if '本轮仅复核上次空facts' in kwargs['messages'][0]['content']:
            reply.choices[0].message.content='{broken'
        return reply
    monkeypatch.setattr(model,'create',malformed_review)
    result=await submit('比较政策',settings=settings,task_type='compare',targets=['T001','T002'])
    assert result.error.code=='OUTPUT_PARSE' and len(model.calls)==3


@pytest.mark.parametrize('analysis,accepted',[
    ('为创业团队提供全周期服务及一人一策扶持。',False),
    ('为创业团队提供全周期服务，对重大产业化人才项目给予一人一策扶持。',True),
])
def test_one_person_one_policy_keeps_major_project_threshold(analysis,accepted):
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='分析对象',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['适用对象'])
    text='为高层次人才创业团队提供全周期服务，对重大产业化人才项目给予一人一策扶持。'
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','B','b',text)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'B','dimension':'适用对象','evidence_ids':['e0']}],
        'scheme_analysis':[{'planid':'B','applicability':[{'text':analysis,'supporting_cells':['d0:B']}]}]})
    result=build_outcome(task,plan,collection,generated,scheme=True)
    assert bool(result.structured['scheme_analysis'][1]['applicability']) is accepted


@pytest.mark.parametrize('source,accepted',[
    ('对重大产业化人才项目给予政策扶持。',False),
    ('对重大产业化人才项目负责人给予政策扶持。',True),
])
def test_project_support_does_not_imply_personal_eligibility(source,accepted):
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='分析对象',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['适用对象'])
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','B','b',source)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'B','dimension':'适用对象','evidence_ids':['e0']}],
        'scheme_analysis':[{'planid':'B','applicability':[{
            'text':'本条扶持适用于重大产业化人才项目负责人。','supporting_cells':['d0:B']}]}]})
    result=build_outcome(task,plan,collection,generated,scheme=True)
    assert bool(result.structured['scheme_analysis'][1]['applicability']) is accepted


@pytest.mark.parametrize('analysis,accepted',[
    ('对高层次人才、高技能人才及生产一线岗位人员进行收入分配倾斜和单独制定奖励政策。',False),
    ('对高技能人才进行收入分配倾斜；对急需紧缺的高层次人才，可单独制定个人贡献奖励等政策。',True),
    ('针对急需紧缺的高层次人才可单独制定政策，并规定收入分配向高技能人才倾斜。',True),
])
def test_separate_reward_rule_cannot_inherit_other_clauses_target_groups(analysis,accepted):
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='分析激励',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['人才激励'])
    text=('收入分配向生产一线岗位和紧缺急需岗位的高层次人才、高技能人才倾斜。'
          '对急需紧缺的高层次人才，可单独制定收入分配倾斜、个人贡献奖励等政策。')
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','B','b',text)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'B','dimension':'人才激励','evidence_ids':['e0']}],
        'scheme_analysis':[{'planid':'B','advantages':[{'text':analysis,'supporting_cells':['d0:B']}]}]})
    result=build_outcome(task,plan,collection,generated,scheme=True)
    assert bool(result.structured['scheme_analysis'][1]['advantages']) is accepted


@pytest.mark.parametrize('source,accepted',[
    ('实行专家举荐制，提高青年人才入选比例。',False),
    ('申请人须通过专家举荐方可申请本项支持。',True),
])
def test_recommendation_system_is_not_a_mandatory_individual_path(source,accepted):
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='分析条件',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['执行条件'])
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','B','b',source)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'B','dimension':'执行条件','evidence_ids':['e0']}],
        'scheme_analysis':[{'planid':'B','applicability':[{
            'text':'人才需通过专家举荐入选人才计划。','supporting_cells':['d0:B']}]}]})
    result=build_outcome(task,plan,collection,generated,scheme=True)
    assert bool(result.structured['scheme_analysis'][1]['applicability']) is accepted


@pytest.mark.parametrize('dimension,expected',[('适用对象','insufficient'),('执行主体','found')])
def test_department_work_assignment_is_not_a_beneficiary_fact(dimension,expected):
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='比较对象',targets=['A','B'])
    plan=ComparisonPlan(dimensions=[dimension])
    source='完善任务清单制度，市委组织部形成工作台账，明确责任单位和完成时限。'
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','B','b',source)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'B','dimension':dimension,'evidence_ids':['e0']}]})
    result=build_outcome(task,plan,collection,generated,scheme=False)
    assert result.structured['matrix'][0]['cells'][1]['status']==expected


@pytest.mark.parametrize('subject,accepted',[('高层次创新创业项目',False),('高层次人才创业团队',True)])
def test_full_cycle_team_service_keeps_its_original_support_subject(subject,accepted):
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='分析服务',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['支持措施'])
    text='为高层次人才创业团队提供全周期服务，对重大产业化人才项目给予一人一策扶持。'
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','B','b',text)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'B','dimension':'支持措施','evidence_ids':['e0']}],
        'scheme_analysis':[{'planid':'B','advantages':[{
            'text':f'为{subject}提供启动、融资等全周期服务。','supporting_cells':['d0:B']}]}]})
    result=build_outcome(task,plan,collection,generated,scheme=True)
    items=result.structured['scheme_analysis'][1]['advantages']
    if accepted:
        assert items and 'derivation' not in items[0]
    else:
        assert items[0]['derivation']=='extractive_support_features'
        assert text in items[0]['text']
        assert f'为{subject}提供启动' not in items[0]['text']


@pytest.mark.parametrize('text,accepted',[
    ('政策甲仅关注人才支持，政策乙侧重人才引进。',True),
    ('整份政策仅支持人才，全部受益对象都可申请。',False),
])
async def test_analysis_scope_is_explicit_and_whole_document_inference_is_rejected(compare_setup,text,accepted):
    settings,_,model=compare_setup
    model.mutate_analysis=lambda output,_:output['differences'][0].update(text=text)
    result=await submit('比较政策',settings=settings,task_type='compare',targets=['T001','T002'])
    judgment=result.structured['matrix'][0]['difference']
    assert (judgment['status']=='supported') is accepted
    if accepted:
        assert judgment['text'].startswith('就本次所列条款而言，')
        assert judgment['scope']=='supporting_cells_only'
    else:
        assert any(gap['reason']=='full_document_inference' for gap in result.structured['analysis_gaps'])


async def test_applicability_keeps_complete_original_conditions_instead_of_lossy_paraphrase(compare_setup):
    settings,kb,model=compare_setup
    source='对来我区工作后首次购房且在我区未享受过住房优惠政策或房改政策的人才给予购房补助。'
    kb.documents['T001']['body']=source
    def omit_conditions(output,_):
        output['scheme_analysis'][0]['applicability'][0]['text']='适用于计划购房且未享受住房优惠的人才。'
    model.mutate_analysis=omit_conditions
    result=await submit('分析方案',settings=settings,task_type='compare_plans',targets=['T001','T002'])
    item=result.structured['scheme_analysis'][0]['applicability'][0]
    assert source in item['text'] and item['derivation']=='extractive_conditions'
    assert '分别' in item['text'] and '不合并为共同资格' in item['text']


@pytest.mark.parametrize('support,accepted',[(['d0:T001'],True),(['d0:T001','d1:T001'],False)])
async def test_each_limitation_uses_a_single_policy_clause(compare_setup,support,accepted):
    settings,_,model=compare_setup
    model.mutate_analysis=lambda output,_:output['scheme_analysis'][0]['limitations'][0].update(supporting_cells=support)
    result=await submit('分析方案',settings=settings,task_type='compare_plans',targets=['T001','T002'])
    assert bool(result.structured['scheme_analysis'][0]['limitations']) is accepted


@pytest.mark.parametrize('duplicate_fact',[False,True])
def test_one_cell_keeps_one_complete_evidence_unit_without_merging_conditions(duplicate_fact):
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='分析支持',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['支持措施'])
    first='对高层次人才给予培养期支持。'
    second='对高技能人才给予收入倾斜。'
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[
        EvidenceUnit('e0','B','b',first),EvidenceUnit('e1','B','c',second)])
    facts=([{'planid':'B','dimension':'支持措施','evidence_ids':[eid]} for eid in ['e0','e1']]
           if duplicate_fact else [{'planid':'B','dimension':'支持措施','evidence_ids':['e0','e1']}])
    result=build_outcome(task,plan,collection,GeneratedComparison.model_validate({'facts':facts}),scheme=False)
    cell=result.structured['matrix'][0]['cells'][1]
    assert cell['quotes']==[first] and cell['text']==first


async def test_missing_advantage_can_keep_explicit_support_features_without_an_extra_call(compare_setup):
    settings,_,model=compare_setup
    model.mutate_analysis=lambda output,_:output['scheme_analysis'][0].update(advantages=[])
    result=await submit('分析方案',settings=settings,task_type='compare_plans',targets=['T001','T002'])
    item=result.structured['scheme_analysis'][0]['advantages'][0]
    assert item['derivation']=='extractive_support_features' and len(model.calls)==4
    basis={c['cell_id']:c for row in result.structured['matrix'] for c in row['cells']}
    assert all(q in item['text'] for cid in item['supporting_cells'] for q in basis[cid]['quotes'])


@pytest.mark.parametrize('source,accepted',[
    ('特设岗位不受岗位总量限制。',False),('特设岗位允许突破编制总量限制。',True),
])
def test_post_count_cannot_be_rewritten_as_staffing_quota(source,accepted):
    task=TaskRequest(run_id='r',session_id='s',created_at='',question='比较条件',targets=['A','B'])
    plan=ComparisonPlan(dimensions=['执行条件'])
    collection=Collection(objects=[{'planid':'A'},{'planid':'B'}],units=[EvidenceUnit('e0','B','b',source)])
    generated=GeneratedComparison.model_validate({'facts':[{'planid':'B','dimension':'执行条件','evidence_ids':['e0']}],
        'scheme_analysis':[{'planid':'B','limitations':[{
            'text':'本条允许编制突破。','supporting_cells':['d0:B']}]}]})
    result=build_outcome(task,plan,collection,generated,scheme=True)
    assert bool(result.structured['scheme_analysis'][1]['limitations']) is accepted
