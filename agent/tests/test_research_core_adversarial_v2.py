"""Independent offline counterexamples for the native research core."""
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from guance_agent.context import RunContext
from guance_agent.kb import MockKB
from guance_agent.research_core import (
    EvidenceCandidate,
    ResearchState,
    _register_evidence,
    build_research_core_agent,
    execute_research_core,
)
from guance_agent.routing import decide
from guance_agent.runtime import Runtime
from guance_agent.schemas import EvidenceRef

from .fake_model import FakeModel, tool


def _run_context(env, question, route, *, scope=None):
    settings, task = env
    task = task.model_copy(update={"question": question})
    return RunContext(
        settings,
        MockKB(settings.mock_kb_path),
        task.run_id,
        task.session_id,
        question=question,
        route=route,
        scope=scope,
    )


def test_explicit_targets_survive_multi_area_multi_period_route(env):
    question = "甲市十三五规划与乙市十四五规划比较交通建设。"
    route = SimpleNamespace(
        areas=("甲市", "乙市"),
        canonical_areas=("甲市", "乙市"),
        fyps=("第十三个五年规划", "第十四个五年规划"),
        planids=(),
    )
    run = _run_context(env, question, route)

    state = ResearchState.create(
        run, question, time.monotonic() + 60,
        explicit_targets=("DOC-CITY-A", "DOC-CITY-B"),
    )

    assert state.authorized_scope == ("DOC-CITY-A", "DOC-CITY-B")
    assert state.authorized_areas == ("甲市", "乙市")
    assert set(state.authorized_periods) == {
        "第十三个五年规划", "第十四个五年规划",
    }


def test_historical_requirement_id_is_independent_of_new_turn_question(env):
    old_question = "上一轮要求核对公路与铁路的历史成果。"
    old_scope = {
        "id": "req_frozen_transport",
        "label": old_question,
        "origin_question": old_question,
        "content_period": "第十三个五年规划",
        "source_period": "第十三个五年规划",
    }
    route = SimpleNamespace(areas=(), canonical_areas=(), fyps=(), planids=())
    run = _run_context(env, "新一轮要求整理已讨论内容。", route)

    state = ResearchState.create(
        run, run.question, time.monotonic() + 60,
        history_requirements=[old_scope],
    )

    inherited = next(item for item in state.requirements if item.label == old_question)
    assert inherited.id == old_scope["id"]
    assert inherited.origin_question == old_question
    assert inherited.content_period == "第十三个五年规划"


def test_summary_keeps_requirements_from_every_prior_turn(env):
    questions = [
        "第一轮要求核对交通建设成果。",
        "第二轮要求核对公路和铁路里程。",
        "第三轮要求核对产业升级成果。",
    ]
    latest_only = {
        "id": "req_third_turn",
        "label": questions[2],
        "origin_question": questions[2],
        "content_period": None,
        "source_period": "第十三个五年规划",
    }
    route = SimpleNamespace(areas=(), canonical_areas=(), fyps=(), planids=())
    run = _run_context(env, "将前面讨论的内容整理成摘要。", route)
    state = ResearchState.create(
        run, run.question, time.monotonic() + 60,
        history_questions=questions,
        history_requirements=[latest_only],
    )

    frozen = state.freeze_requirements(["摘要需覆盖前面各轮的研究要求。"])
    labels = {item["label"] for item in frozen["requirements"]}

    assert set(questions) <= labels
    assert len({item["id"] for item in frozen["requirements"]}) == len(labels)


def test_same_document_comparison_can_support_both_content_periods(env):
    question = "比较同一规划中十五时期与十一五规划期间的生态发展变化。"
    route = SimpleNamespace(
        areas=("甲县",),
        canonical_areas=("甲县",),
        fyps=("第十一个五年规划",),
        planids=("DOC-ECOLOGY",),
    )
    run = _run_context(env, question, route, scope=("DOC-ECOLOGY",))
    state = ResearchState.create(
        run, question, time.monotonic() + 60,
        explicit_targets=("DOC-ECOLOGY",),
    )
    frozen = state.freeze_requirements([
        "十五时期的生态保护延续事实。",
        "十一五规划期间的生态建设新进展。",
    ])
    requirements = {
        item.label: item.id for item in state.requirements
        if item.content_period is not None
    }
    passage = "十五时期，水源涵养能力持续增强。十一五规划期间，启动生态县建设。"
    chunk_id = "shared-ecology-chunk"
    run.evidence.register(EvidenceRef(
        planid="DOC-ECOLOGY",
        chunk_id=chunk_id,
        title="甲县国民经济和社会发展第十一个五年规划纲要",
        area="甲县",
        fyp="第十一个五年规划",
        admin_level=None,
        plantype_label=None,
        section_idx=1,
        section_path="生态建设",
        char_start=0,
        char_end=len(passage),
        chunk_char_start=0,
        chunk_char_end=len(passage),
        source="kb_read",
    ))
    state.read_text[chunk_id] = [passage]

    assert frozen["frozen"] is True
    assert set(requirements) == {
        "十五时期的生态保护延续事实。",
        "十一五规划期间的生态建设新进展。",
    }
    assert {
        state.requirements[[item.id for item in state.requirements].index(req_id)].content_period
        for req_id in requirements.values()
    } == {"第十个五年计划", "第十一个五年规划"}

    first, second = _register_evidence(state, EvidenceCandidate(
        candidate_id="fifteen-period-fact",
        claim="十五时期水源涵养能力增强。",
        quote="十五时期，水源涵养能力持续增强。",
        chunk_id=chunk_id,
        requirement_ids=[requirements["十五时期的生态保护延续事实。"]],
        content_period="第十个五年计划",
        fact_status="unspecified",
        time_anchor="十五时期",
    )), _register_evidence(state, EvidenceCandidate(
        candidate_id="eleventh-plan-fact",
        claim="十一五规划期间启动生态县建设。",
        quote="十一五规划期间，启动生态县建设。",
        chunk_id=chunk_id,
        requirement_ids=[requirements["十一五规划期间的生态建设新进展。"]],
        content_period="第十一个五年规划",
        fact_status="unspecified",
        time_anchor="十一五规划期间",
    ))

    assert first["accepted"] is True
    assert second["accepted"] is True
    assert {claim.content_period for claim in state.claims} == {
        "第十个五年计划", "第十一个五年规划",
    }


@pytest.mark.asyncio
async def test_partial_delivery_keeps_supported_block_and_rejects_unsupported_block(
        env):
    question = "请整理planid TEST-001中的农业灌溉事实及其作用分析。"
    settings, task = env
    settings = replace(settings, max_tool_calls=2, llm_max_output_tokens=1200)
    task = task.model_copy(update={"question": question})
    route = decide(question)
    run = RunContext(
        settings, MockKB(settings.mock_kb_path), task.run_id, task.session_id,
        question=question, route=route, scope=("TEST-001",),
    )
    runtime = Runtime(settings, task)
    state = ResearchState.create(
        run, question, time.monotonic() + 60,
        explicit_targets=("TEST-001",),
    )
    frozen = state.freeze_requirements([
        "农业灌溉措施的原文事实。",
        "灌溉措施的作用分析。",
    ])
    ids = {item.label: item.id for item in state.requirements}
    fact_requirement = ids["农业灌溉措施的原文事实。"]
    analysis_requirement = ids["灌溉措施的作用分析。"]
    source_sentence = "完善农业灌溉设施，发展绿色农业。"
    first_quote = "完善农业灌溉设施"
    second_quote = "发展绿色农业"
    chunk_id = "TEST-001__s01__p00"
    run.evidence.register(EvidenceRef(
        planid="TEST-001", chunk_id=chunk_id, title="农业", area="甲县",
        fyp=None, admin_level=None, plantype_label=None, section_idx=1,
        section_path="农业发展", char_start=0, char_end=len(source_sentence),
        chunk_char_start=0, chunk_char_end=len(source_sentence), source="kb_read",
    ))
    state.read_text[chunk_id] = [source_sentence]
    state.io_calls = settings.max_tool_calls

    async def audit(current, _mapped):
        approved_block = next(
            block.id for block in current.draft_blocks if block.id == "supported-fact"
        )
        return json.dumps({
            "passed": False,
            "covered_requirement_ids": [fact_requirement],
            "approved_block_ids": [approved_block],
            "gaps": [analysis_requirement],
            "unsupported_claims": ["unsupported-analysis"],
        }, ensure_ascii=False)

    model = FakeModel([
        [tool("submit_draft", evidence_candidates=[
            {
                "candidate_id": "irrigation-fact",
                "claim": first_quote,
                "quote": first_quote,
                "chunk_id": chunk_id,
                "requirement_ids": [fact_requirement, analysis_requirement],
            },
            {
                "candidate_id": "green-agriculture-fact",
                "claim": second_quote,
                "quote": second_quote,
                "chunk_id": chunk_id,
                "requirement_ids": [analysis_requirement],
            },
        ], blocks=[
            {
                "id": "supported-fact", "title": "灌溉措施", "kind": "fact",
                "text": "", "requirement_ids": [fact_requirement],
                "evidence_ids": ["irrigation-fact"],
            },
            {
                "id": "unsupported-analysis", "title": "作用判断",
                "kind": "analysis", "text": "这些措施已经显著提高农业产量。",
                "requirement_ids": [analysis_requirement],
                "evidence_ids": ["irrigation-fact", "green-agriculture-fact"],
            },
        ], finish_if_partial=True)],
    ])
    agent = build_research_core_agent(model, settings, runtime, audit=audit)

    answer = await execute_research_core(agent, state, runtime)

    assert frozen["frozen"] is True
    assert state.partial_finalized is True
    assert state.check_result["passed"] is False
    assert [block.id for block in state.approved_blocks] == ["supported-fact"]
    assert first_quote in answer
    assert second_quote not in answer
    assert "作用判断" not in answer
    assert len(state.claims) == 2
    assert state.claims[0].chunk_ids == state.claims[1].chunk_ids
    assert state.claims[0].fact_ids != state.claims[1].fact_ids
    assert state.approved_blocks[0].evidence_ids == state.claims[0].fact_ids
    assert run.evidence.get("TEST-001__s01__p00").source == "kb_read"
    assert state.claims[0].quotes[0] in source_sentence
    assert state.claims[1].quotes[0] in source_sentence
