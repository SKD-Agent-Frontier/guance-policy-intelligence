"""Focused offline regressions for research-core closeout fixes."""
import json
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from guance_agent.context import RunContext
from guance_agent.kb import MockKB
from guance_agent.research_core import (
    EvidenceCandidate,
    ResearchBlock,
    ResearchState,
    _register_evidence,
    build_research_core_agent,
    execute_research_core,
)
from guance_agent.runtime import Runtime
from guance_agent.schemas import EvidenceRef, TaskRequest

from .fake_model import FakeModel, tool
from .test_research_core import _seed_read_fact


def _make_state(settings, question, periods):
    task = TaskRequest(run_id="closeout", session_id="closeout-session",
                       question=question, created_at="2026-11-04T00:00:00Z")
    route = SimpleNamespace(areas=(), canonical_areas=(), fyps=periods, planids=())
    run = RunContext(settings, MockKB(settings.mock_kb_path), task.run_id,
                     task.session_id, question=question, route=route,
                     scope=("CLOSEOUT-DOC",))
    runtime = Runtime(settings, task)
    state = ResearchState.create(
        run, question, time.monotonic() + 60,
        explicit_targets=("CLOSEOUT-DOC",),
        approved_source_periods=periods,
    )
    return runtime, state


def test_freeze_keeps_source_period_distinct_from_fact_period(env):
    settings, _task = env
    source_period = "第十一个五年规划"
    runtime, state = _make_state(
        settings, "请依据十一五规划整理发展基础。", (source_period,))
    source_only = state.freeze_requirements(["十一五规划的发展基础"])
    source_requirement = next(
        item for item in state.requirements if item.label == "十一五规划的发展基础")

    assert source_only["frozen"] is True
    assert source_requirement.content_period is None
    assert source_requirement.source_period == source_period

    same_period_state = ResearchState.create(
        state.run, "请核对十一五规划中的目标。", time.monotonic() + 60,
        explicit_targets=("CLOSEOUT-DOC",), approved_source_periods=(source_period,),
    )
    same_period_state.freeze_requirements(["十一五规划中的十一五期间目标"])
    same_period_requirement = next(
        item for item in same_period_state.requirements
        if item.label == "十一五规划中的十一五期间目标")
    assert same_period_requirement.content_period == source_period

    canonical_state = ResearchState.create(
        state.run, "请核对十一五规划期间的经济增长。", time.monotonic() + 60,
        explicit_targets=("CLOSEOUT-DOC",), approved_source_periods=(source_period,),
    )
    canonical_state.freeze_requirements(["第十一个五年规划期间的经济增长"])
    canonical_requirement = next(
        item for item in canonical_state.requirements
        if item.label == "第十一个五年规划期间的经济增长")
    assert canonical_requirement.content_period == source_period

    cross_period_state = ResearchState.create(
        state.run,
        "比较十三五规划基础与十四五期间变化。",
        time.monotonic() + 60,
        explicit_targets=("CLOSEOUT-DOC",),
        approved_source_periods=("第十三个五年规划",),
    )
    cross_period_state.freeze_requirements([
        "十三五规划中的发展基础",
        "十四五期间的发展目标",
    ])
    cross_by_label = {item.label: item for item in cross_period_state.requirements}
    assert cross_by_label["十三五规划中的发展基础"].content_period is None
    assert cross_by_label["十四五期间的发展目标"].content_period == "第十四个五年规划"

    retrospective_quote = "十五时期，农业灌溉设施持续完善。"
    wrong_time_quote = "十二五期间，完成农业设施建设。"
    passage = retrospective_quote + wrong_time_quote
    chunk_id = "CLOSEOUT-DOC__s01__p00"
    state.run.evidence.register(EvidenceRef(
        planid="CLOSEOUT-DOC", chunk_id=chunk_id, title="十一五规划纲要",
        area="甲县", fyp=source_period, admin_level=None, plantype_label=None,
        section_idx=1, section_path="农业发展", char_start=0, char_end=len(passage),
        chunk_char_start=0, chunk_char_end=len(passage), source="kb_read",
        observed_ranges=[(0, len(passage))], complete=True,
    ))
    state.read_text[chunk_id] = [passage]
    retrospective = _register_evidence(state, EvidenceCandidate(
        candidate_id="retrospective", claim=retrospective_quote, quote=retrospective_quote,
        chunk_id=chunk_id, requirement_ids=[source_requirement.id],
        content_period="第十个五年计划", time_anchor="十五时期",
    ))
    assert retrospective["accepted"] is True
    wrong_time = _register_evidence(state, EvidenceCandidate(
        candidate_id="wrong-period", claim=wrong_time_quote, quote=wrong_time_quote,
        chunk_id=chunk_id,
        requirement_ids=[source_requirement.id], content_period=source_period,
    ))
    assert wrong_time["accepted"] is False
    assert wrong_time["error"] == "content_period_not_in_read_context"


@pytest.mark.asyncio
async def test_repeated_invalid_empty_draft_terminates_without_semantic_approval(env):
    settings, _task = env
    question = "请查询某规划中的农业设施事实。"
    runtime, state = _make_state(settings, question, ())
    state.freeze_requirements(["农业设施的原文事实"])
    audit_calls = 0

    async def audit(_state, _mapped):
        nonlocal audit_calls
        audit_calls += 1
        return json.dumps({"passed": False, "covered_requirement_ids": [],
                           "approved_block_ids": [], "gaps": ["missing"],
                           "unsupported_claims": []})

    submission = tool("submit_draft", evidence_candidates=[], blocks=[{
        "id": "empty", "title": "空答案", "kind": "fact", "text": "",
        "requirement_ids": [], "evidence_ids": ["missing-fact"],
    }])
    model = FakeModel([[submission], [submission]])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime, audit=audit), state, runtime)

    assert model.calls == 2
    assert state.partial_finalized is True
    assert state.final_answer == answer
    assert state.checked_draft_hash is not None
    assert state.check_result["passed"] is False
    assert state.check_result["audit_performed"] is False
    assert state.approved_blocks == []
    assert audit_calls == 0
    assert "### 空答案" not in answer
    assert "尚未通过语义审查，不作为研究结论" not in answer


def test_research_block_schema_limits_kind_and_explains_period_fields():
    with pytest.raises(ValidationError):
        ResearchBlock(id="x", title="标题", kind="summary", text="",
                      requirement_ids=[], evidence_ids=["e"])

    schema = ResearchBlock.model_json_schema()
    assert schema["properties"]["kind"]["enum"] == ["fact", "analysis"]
    candidate_schema = EvidenceCandidate.model_json_schema()
    period_description = candidate_schema["properties"]["content_period"]["description"]
    assert "来源" in period_description
    assert "null" in period_description


@pytest.mark.asyncio
async def test_resubmitting_candidate_id_validates_corrected_evidence(env):
    settings, _task = env
    runtime, state = _make_state(settings, "请核对农业设施事实。", ())
    state.freeze_requirements(["农业设施的原文事实"])
    requirement_ids = [item.id for item in state.requirements]
    first_quote = "完善农业灌溉设施"
    corrected_quote = "完善农业灌溉设施，发展绿色农业。"
    chunk_id = _seed_read_fact(state)
    audited = []

    async def audit(_state, mapped):
        quotes = mapped[requirement_ids[0]][0]["quote"]
        audited.append(quotes)
        passed = quotes == [corrected_quote]
        return json.dumps({
            "passed": passed, "covered_requirement_ids": requirement_ids if passed else [],
            "approved_block_ids": ["agriculture"] if passed else [],
            "gaps": [] if passed else requirement_ids, "unsupported_claims": [],
        })

    def submit(quote):
        return tool("submit_draft", evidence_candidates=[{
            "candidate_id": "candidate", "claim": quote, "quote": quote,
            "chunk_id": chunk_id, "requirement_ids": requirement_ids,
        }], blocks=[{
            "id": "agriculture", "kind": "fact", "title": "农业设施",
            "requirement_ids": requirement_ids, "evidence_ids": ["candidate"],
        }])

    model = FakeModel([[submit(first_quote)], [submit(corrected_quote)]])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime, audit=audit), state, runtime)

    assert audited == [[first_quote], [corrected_quote]]
    assert state.check_result["passed"] is True
    assert corrected_quote in answer
