"""Offline tests for the web catalog-to-agent research plan adapter."""
import time
from dataclasses import replace

import pytest
from guance_agent.research_contract import ResearchPlan, TaskContract
from guance_agent.research_planner import ResearchPlanner
from guance_agent.schemas import TaskRequest

from app.policy_catalog import CatalogSnapshot, PolicyCatalogService, PolicyRecord
from app.research_runtime import discover_run_plan


def record(planid, area, fyp="第十三个五年规划", title=None, body="产业结构调整"):
    return PolicyRecord(
        planid=planid,
        title=title or f"{area}{fyp}国民经济和社会发展规划纲要",
        area=area, fyp=fyp, plantype_label="总体规划纲要", document_id=f"doc-{planid}",
        body=body, sections=(), admin_level="市",
    )


def catalog(*records):
    nodes = [{"code": "130000000000", "name": "河北省", "tier": "province",
              "children": [{"code": "131100000000", "name": "衡水市",
                            "tier": "prefecture", "children": []},
                           {"code": "130600000000", "name": "保定市",
                            "tier": "prefecture", "children": []},
                           {"code": "130200000000", "name": "唐山市",
                            "tier": "prefecture", "children": []},
                           {"code": "130400000000", "name": "邯郸市",
                            "tier": "prefecture", "children": []}]}]
    codes = {"衡水市": "131100000000", "保定市": "130600000000",
             "唐山市": "130200000000", "邯郸市": "130400000000"}
    return PolicyCatalogService(
        CatalogSnapshot(generation_id="fixture-generation", revision=1,
                        partial=False, records=tuple(records)),
        region_payload={"data": nodes},
        region_mapping={r.planid: {"region_status": "mapped", "region_code": codes[r.area]}
                        for r in records if r.area in codes},
    )


def task(mode, targets, question="河北省产业结构研究", dimensions=None):
    return TaskRequest(run_id="run", session_id="session", question=question,
                       task_type=mode, targets=targets, dimensions=dimensions or [],
                       created_at="now")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,targets", [
    ("qa", ["h1"]), ("extract", ["h1"]), ("organize", ["h1", "b1"]),
    ("compare", ["h1", "b1"]), ("compare_plans", ["h1", "b1"]),
])
async def test_explicit_catalog_targets_become_trusted_plan_documents(mode, targets):
    records = [record("h1", "衡水市"), record("b1", "保定市")]
    selected_catalog = catalog(*records)
    selected_task = task(mode, targets)
    contract = TaskContract.from_task(selected_task)
    plan = ResearchPlanner.initial(contract)

    discovery = await discover_run_plan(
        selected_task, contract, plan, time.monotonic() + 10, selected_catalog,
    )

    assert discovery.status == "ready"
    assert [doc.planid for doc in discovery.plan.documents] == targets
    assert all(doc.source_verified and doc.document_id == f"doc-{doc.planid}"
               and doc.generation_id == "fixture-generation" for doc in discovery.plan.documents)
    assert set(discovery.plan.coverage.discovered_planids) == set(targets)


@pytest.mark.asyncio
async def test_catalog_dimension_hints_cannot_drop_locked_contract_dimensions():
    question = "在临朐县第十一个五年规划中，产业布局这条主线如何体现？"
    selected_task = task(
        "qa", None, question, dimensions=["产业布局这条主线"],
    )
    contract = TaskContract.from_task(selected_task)
    plan = ResearchPlanner.initial(contract)
    selected_catalog = catalog(record(
        "linqu-11", "临朐县", fyp="第十一个五年规划",
        title="临朐县国民经济和社会发展第十一个五年规划纲要",
        body="围绕产业布局这条主线，优化产业结构并发展特色产业。",
    ))

    discovery = await discover_run_plan(
        selected_task, contract, plan, time.monotonic() + 10, selected_catalog,
    )

    assert discovery.status == "ready"
    assert discovery.plan.dimensions == ["产业布局这条主线"]


@pytest.mark.asyncio
async def test_missing_or_underfilled_catalog_targets_fail_closed():
    selected_catalog = catalog(record("h1", "衡水市"))
    qa = task("qa", ["absent"])
    result = await discover_run_plan(qa, TaskContract.from_task(qa),
                                     ResearchPlan.for_contract(TaskContract.from_task(qa)),
                                     time.monotonic() + 5, selected_catalog)
    assert result.status == "insufficient"
    assert result.reason_code == "requested_document_missing"
    assert not result.plan.documents

    compare = task("compare", ["h1"])
    compare_contract = TaskContract.from_task(compare)
    result = await discover_run_plan(compare, compare_contract,
                                     ResearchPlanner.initial(compare_contract),
                                     time.monotonic() + 5, selected_catalog)
    assert result.status == "insufficient"
    assert result.reason_code == "insufficient_document_coverage"


@pytest.mark.asyncio
async def test_deadline_expires_before_catalog_discovery():
    selected_task = task("qa", ["h1"])
    selected_contract = TaskContract.from_task(selected_task)
    with pytest.raises(TimeoutError):
        await discover_run_plan(selected_task, selected_contract,
                                ResearchPlanner.initial(selected_contract),
                                time.monotonic() - 1, catalog(record("h1", "衡水市")))


@pytest.mark.asyncio
async def test_actual_internal_resolver_distinguishes_historical_shorthand_and_title_topic():
    from app.research_planning import resolve_research_internal

    records = [
        record("historic", "衡水市", "第十个五年规划", "衡水市国民经济和社会发展第十个五年计划纲要"),
        record("newer", "衡水市", "第十五个五年规划", "衡水市国民经济和社会发展第十五个五年规划纲要"),
        record("ai-incidental", "衡水市", "第十三个五年规划",
               "衡水市国民经济和社会发展第十三个五年规划纲要",
               body="正文提到人工智能产业发展。"),
    ]
    selected_catalog = catalog(*records)
    historic = resolve_research_internal("qa", "衡水市十五规划产业结构", selected_catalog)
    assert historic["status"] == "ready"
    assert [item["planid"] for item in historic["policies"]] == ["historic"]
    ai = resolve_research_internal("qa", "衡水市人工智能产业政策", selected_catalog)
    assert ai["status"] != "ready"
    assert "ai-incidental" not in [item["planid"] for item in ai["policies"]]


@pytest.mark.asyncio
async def test_adapter_preserves_full_requested_set_and_separates_missing_from_capped(monkeypatch):
    from app.research_planning import resolve_research_internal

    selected_catalog = catalog(record("h1", "衡水市"), record("b1", "保定市"))
    resolved = resolve_research_internal(
        "compare", "与河北省其他地市相比，衡水市十三五产业结构目标有什么特点？",
        selected_catalog,
    )
    assert resolved["status"] == "ready"
    assert set(resolved["requested_subjects"]) == {"衡水市", "保定市", "唐山市", "邯郸市"}

    selected_task = task(
        "compare", None,
        "与河北省其他地市相比，衡水市十三五产业结构目标有什么特点？",
    )
    selected_contract = TaskContract.from_task(selected_task)
    # The adapter must retain absent and capped labels even when only known documents
    # can be attached to the executable plan.
    import app.research_runtime as runtime_adapter

    monkeypatch.setattr(runtime_adapter, "resolve_research_internal", lambda *_: {
        **resolved,
        "requested_subjects": ["衡水市", "保定市", "唐山市", "邯郸市"],
        "subject_gaps": ["唐山市"],
        "coverage_limited_subjects": ["邯郸市"],
    })
    discovery = await discover_run_plan(
        selected_task, selected_contract, ResearchPlanner.initial(selected_contract),
        time.monotonic() + 10, selected_catalog,
    )

    assert discovery.plan.requested_subjects == ["衡水市", "保定市", "唐山市", "邯郸市"]
    assert discovery.plan.coverage.subject_gaps == ["唐山市"]
    assert discovery.plan.coverage.coverage_limited_subjects == ["邯郸市"]


@pytest.mark.asyncio
async def test_prefecture_discovery_uses_catalog_parent_and_limits_claimed_scope():
    from app.research_planning import resolve_research_internal

    selected_catalog = catalog(record("h1", "衡水市"), record("b1", "保定市"))
    result = resolve_research_internal(
        "compare", "与河北省其他地市相比，衡水市十三五产业结构目标有什么特点？",
        selected_catalog,
    )
    assert result["status"] == "ready"
    selected_ids = {item["planid"] for item in result["policies"]}
    assert selected_ids == {"h1", "b1"}
    assert set(result["requested_subjects"]) == {"衡水市", "保定市", "唐山市", "邯郸市"}
    assert set(result["subject_gaps"]) == {"唐山市", "邯郸市"}
    assert result["coverage_limited_subjects"] == []


def test_beijing_tianjin_hebei_ai_research_requires_title_linked_documents():
    from app.research_planning import resolve_research_internal

    question = "分析京津冀三地人工智能产业政策的发展特点及差异。"
    initial = resolve_research_internal("compare", question, catalog())
    assert initial["status"] == "insufficient"
    assert "北京市" in initial["message"]
    assert "天津市" in initial["message"]
    assert initial["requested_subjects"] == ["北京市", "天津市", "河北省"]
    assert not initial["policies"]

    question_with_period = question.replace("三地", "三地十四五")
    period = "第十四个五年规划"
    real_documents = [
        record("ai-bj", "北京市", period, "北京市人工智能产业发展第十四五专项规划"),
        record("ai-tj", "天津市", period, "天津市人工智能产业发展第十四五专项规划"),
        record("ai-hebei", "河北省", period, "河北省人工智能产业发展第十四五专项规划"),
    ]
    real_documents = [replace(doc, admin_level="省级") for doc in real_documents]
    selected = resolve_research_internal(
        "compare", question_with_period, catalog(*real_documents),
    )
    assert selected["status"] == "ready"
    assert {item["planid"] for item in selected["policies"]} == {"ai-bj", "ai-tj", "ai-hebei"}

    body_only_documents = [
        record("fire-bj", "北京市", period, "北京市消防安全第十四五专项规划",
               body="正文偶然提及人工智能产业。"),
        real_documents[1],
        real_documents[2],
    ]
    body_only_documents = [replace(doc, admin_level="省级") for doc in body_only_documents]
    missing_topic = resolve_research_internal(
        "compare", question_with_period, catalog(*body_only_documents),
    )
    assert missing_topic["status"] == "insufficient"
    assert "fire-bj" not in [item["planid"] for item in missing_topic["policies"]]
    assert "policy" in missing_topic["reason"] or "catalog" in missing_topic["reason"]


@pytest.mark.asyncio
async def test_hengshui_collective_comparison_validates_full_catalog_lineage():
    """The user's province bound covers verified prefecture peers, not arbitrary places."""
    from guance_agent.research_runtime import ResearchRuntime

    question = "与河北省其他地市相比，衡水市在十三五规划中提出的产业结构优化目标有什么特点？"
    selected = catalog(record("h1", "衡水市"), record("b1", "保定市"))
    request = task("compare", None, question, dimensions=["产业结构"])
    contract = TaskContract.from_task(request)
    initial = ResearchPlanner.initial(contract)

    # Crucially the scope retains the user's 河北省/衡水市 boundary, rather
    # than substituting 保定市 into it and failing as an artificial expansion.
    assert "河北省" in contract.scope.areas
    discovery = await discover_run_plan(
        request, contract, initial, time.monotonic() + 10, selected,
    )
    assert discovery.status == "ready"
    assert [d.planid for d in discovery.plan.documents] == ["h1", "b1"]
    assert "河北省" in discovery.plan.scope.areas
    assert "保定市" not in discovery.plan.scope.areas
    assert len(discovery.plan.subjects) == 2
    runtime = ResearchRuntime(contract, initial, time.monotonic() + 10)
    runtime.transition("planning")
    runtime.transition("discovering")
    assert runtime.accept_discovery(discovery.plan).documents[1].area == "保定市"


@pytest.mark.asyncio
async def test_catalog_region_scope_fails_closed_for_unknown_or_mismapped_peer():
    """An out-of-province record is never justified by substring or a fake parent."""
    selected = catalog(record("h1", "衡水市"), record("b1", "保定市"),
                       record("bj", "北京市"))
    request = task("compare", ["h1", "bj"], "比较河北省的地区产业政策")
    contract = TaskContract.from_task(request)
    discovery = await discover_run_plan(
        request, contract, ResearchPlanner.initial(contract),
        time.monotonic() + 10, selected,
    )
    assert discovery.status == "insufficient"
    assert discovery.reason_code == "catalog_evidence_missing"
    assert not discovery.plan.documents

    selected.region_mapping["b1"] = {
        "region_status": "mapped", "region_code": "131100000000",
    }
    mismatched = task("compare", ["h1", "b1"], "比较河北省各市十三五产业结构目标")
    wrong_contract = TaskContract.from_task(mismatched)
    rejected = await discover_run_plan(
        mismatched, wrong_contract, ResearchPlanner.initial(wrong_contract),
        time.monotonic() + 10, selected,
    )
    assert rejected.status == "insufficient"
    assert rejected.reason_code == "catalog_evidence_missing"


@pytest.mark.asyncio
async def test_collective_peer_gap_keeps_verified_anchor_and_reports_no_comparison():
    """One real city plan may be identified without inventing missing peers."""
    selected = catalog(record("h1", "衡水市"))
    request = task("compare", None,
                   "与河北省其他地市相比，衡水市在十三五规划的产业结构目标有什么特点？")
    contract = TaskContract.from_task(request)
    discovery = await discover_run_plan(
        request, contract, ResearchPlanner.initial(contract),
        time.monotonic() + 10, selected,
    )
    assert discovery.status == "insufficient"
    assert discovery.reason_code == "insufficient_document_coverage"
    assert [doc.planid for doc in discovery.plan.documents] == ["h1"]
    assert discovery.plan.documents[0].source_verified
    assert discovery.plan.coverage.subject_gaps
    assert "不能据此得出跨地市比较结论" in discovery.message
