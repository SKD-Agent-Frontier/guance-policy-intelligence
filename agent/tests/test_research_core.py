"""Offline safety and protocol tests for the current research-core tools."""
import json
import time
from dataclasses import replace

import pytest
from agents.exceptions import ModelBehaviorError

import guance_agent.orchestrator as orchestrator
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


def _make_state(settings, task, question, *, explicit_targets=("TEST-001",)):
    route = decide(question)
    run = RunContext(
        settings, MockKB(settings.mock_kb_path), task.run_id, task.session_id,
        question=question, route=route, scope=explicit_targets,
    )
    runtime = Runtime(settings, task.model_copy(update={"question": question}))
    state = ResearchState.create(
        run, question, time.monotonic() + 60,
        explicit_targets=explicit_targets,
        allowed_planids=explicit_targets,
    )
    return run, runtime, state


def _seed_read_fact(state, quote="完善农业灌溉设施", *, source="kb_read", complete=True):
    chunk_id = "TEST-001__s01__p00"
    text = "完善农业灌溉设施，发展绿色农业。"
    state.run.evidence.register(EvidenceRef(
        planid="TEST-001", chunk_id=chunk_id, title="农业发展规划", area="甲市",
        fyp=None, admin_level=None, plantype_label=None, section_idx=1,
        section_path="农业发展", char_start=0, char_end=len(text),
        chunk_char_start=0, chunk_char_end=len(text), source=source,
        observed_ranges=[(0, len(text))], complete=complete,
    ))
    state.read_text[chunk_id] = [text]
    return chunk_id


@pytest.mark.asyncio
async def test_search_read_batch_and_structured_submit_deliver_verified_fact(env, monkeypatch):
    settings, base_task = env
    settings = replace(settings, llm_base_url="https://fake.invalid/v1",
                       llm_api_key="offline-test-key")
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    task = base_task.model_copy(update={"question": question, "targets": ["TEST-001"]})
    run, runtime, state = _make_state(settings, task, question)
    quote = "完善农业灌溉设施"
    monkeypatch.setattr(run.kb, "verify_quote",
                        lambda _chunk_id, value, *_a, **_k: value == quote,
                        raising=False)
    proposals = ["农业灌溉设施的原文事实。"]
    frozen = state.freeze_requirements(proposals)
    requirement_ids = [item.id for item in state.requirements]

    async def audit(current, _mapped):
        return json.dumps({
            "passed": True,
            "covered_requirement_ids": [item.id for item in current.requirements],
            "approved_block_ids": ["irrigation-fact"],
            "gaps": [], "unsupported_claims": [],
        }, ensure_ascii=False)

    model = FakeModel([
        [tool("search", query="农业灌溉设施", top_k=8, requirements=proposals)],
        [tool("read_batch", items=[{
            "planid": "TEST-001", "chunk_id": "TEST-001__s01__p00", "offset": 0,
        }])],
        [tool("submit_draft", evidence_candidates=[{
            "candidate_id": "irrigation", "claim": quote, "quote": quote,
            "chunk_id": "TEST-001__s01__p00", "requirement_ids": requirement_ids,
        }], blocks=[{
            "id": "irrigation-fact", "title": "农业灌溉设施", "kind": "fact",
            "text": "", "requirement_ids": requirement_ids,
            "evidence_ids": ["irrigation"],
        }])],
    ])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime, audit=audit), state, runtime)

    assert frozen["frozen"] is True
    assert model.calls == 3
    assert state.final_answer == answer
    assert quote in answer
    assert state.check_result["passed"] is True
    assert state.requirements[0].status == "delivered"


def test_evidence_rejects_fake_quote_wrong_source_and_wrong_time(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    run, _runtime, state = _make_state(settings, task, question)
    state.freeze_requirements(["农业灌溉设施的原文事实。"])
    requirement_id = state.requirements[0].id
    chunk_id = _seed_read_fact(state)

    fake_quote = _register_evidence(state, EvidenceCandidate(
        candidate_id="fake", claim="虚构事实", quote="原文里没有", chunk_id=chunk_id,
        requirement_ids=[requirement_id],
    ))
    wrong_time = _register_evidence(state, EvidenceCandidate(
        candidate_id="time", claim="事实", quote="完善农业灌溉设施", chunk_id=chunk_id,
        requirement_ids=[requirement_id], time_anchor="2035年",
    ))
    assert fake_quote["accepted"] is False
    assert fake_quote["error"] == "quote_not_in_read_text"
    assert wrong_time["accepted"] is False
    assert wrong_time["error"] == "time_anchor_not_in_read_context"

    state.run.evidence.register(EvidenceRef(
        planid="TEST-001", chunk_id="wrong-source", title="农业发展规划", area="甲市",
        fyp=None, admin_level=None, plantype_label=None, section_idx=1,
        section_path="农业发展", char_start=0, char_end=10,
        chunk_char_start=0, chunk_char_end=10, source="kb_search", complete=True,
    ))
    state.read_text["wrong-source"] = ["完善农业灌溉设施"]
    wrong_source = _register_evidence(state, EvidenceCandidate(
        candidate_id="source", claim="事实", quote="完善农业灌溉设施",
        chunk_id="wrong-source", requirement_ids=[requirement_id],
    ))
    assert wrong_source["accepted"] is False
    assert wrong_source["error"] == "source_not_complete"


@pytest.mark.asyncio
async def test_explicit_target_blocks_out_of_scope_read(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    run, runtime, state = _make_state(settings, task, question)
    state.freeze_requirements(["农业灌溉设施的原文事实。"])
    model = FakeModel([
        [tool("read_batch", items=[{
            "planid": "TEST-OTHER", "chunk_id": "TEST-OTHER__s01__p00", "offset": 0,
        }])],
        ModelBehaviorError("offline protocol-stop"),
    ])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime), state, runtime)
    assert state.authorized_scope == ("TEST-001",)
    assert state.final_answer is None
    assert "未取得" in answer or "未完成" in answer
    assert state.run.evidence.get("TEST-OTHER__s01__p00") is None


@pytest.mark.asyncio
async def test_review_failure_returns_feedback_and_allows_revised_submission(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    run, runtime, state = _make_state(settings, task, question)
    state.freeze_requirements(["农业灌溉设施的原文事实。"])
    chunk_id = _seed_read_fact(state)
    requirement_ids = [item.id for item in state.requirements]
    audit_calls = 0

    async def audit(current, _mapped):
        nonlocal audit_calls
        audit_calls += 1
        passed = audit_calls == 2
        return json.dumps({
            "passed": passed,
            "covered_requirement_ids": requirement_ids if passed else [],
            "approved_block_ids": ["fact"] if passed else [],
            "gaps": [] if passed else requirement_ids,
            "unsupported_claims": [] if passed else ["fact"],
        })

    candidate = {
        "candidate_id": "fact", "claim": "完善农业灌溉设施",
        "quote": "完善农业灌溉设施", "chunk_id": chunk_id,
        "requirement_ids": requirement_ids,
    }
    block = {
        "id": "fact", "title": "灌溉设施", "kind": "fact", "text": "",
        "requirement_ids": requirement_ids, "evidence_ids": ["fact"],
    }
    model = FakeModel([
        [tool("submit_draft", evidence_candidates=[candidate], blocks=[block])],
        [tool("submit_draft", evidence_candidates=[candidate], blocks=[block])],
    ])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime, audit=audit), state, runtime)
    assert audit_calls == 2
    assert model.calls == 2
    assert state.check_result["passed"] is True
    assert state.final_answer == answer
    assert "完善农业灌溉设施" in answer


@pytest.mark.asyncio
async def test_search_freezes_requirements_and_obeys_io_budget(env):
    settings, task = env
    settings = replace(settings, max_tool_calls=1)
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    run, runtime, state = _make_state(settings, task, question)
    model = FakeModel([
        [tool("search", query="农业灌溉设施", top_k=8,
              requirements=["农业灌溉设施的原文事实。"])],
        [tool("search", query="改变要求", top_k=8,
              requirements=["改写后的新要求"])],
        ModelBehaviorError("offline protocol-stop"),
    ])
    await execute_research_core(
        build_research_core_agent(model, settings, runtime), state, runtime)
    assert state.requirements_frozen is True
    assert state.io_calls == 1
    assert state.remaining()["search_read"] == 0
    assert all(item.label != "改写后的新要求" for item in state.requirements)


@pytest.mark.asyncio
async def test_direct_submit_without_search_cannot_finalize(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    _run, runtime, state = _make_state(settings, task, question)
    model = FakeModel([
        [tool("submit_draft", evidence_candidates=[], blocks=[{
            "id": "unsupported", "title": "未检索结论", "kind": "fact",
            "text": "该规划完善了农业灌溉设施。", "requirement_ids": [],
            "evidence_ids": ["unknown-fact"],
        }])],
        ModelBehaviorError("offline protocol-stop"),
    ])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime), state, runtime)
    assert state.requirements_frozen is False
    assert state.final_answer is None
    assert "未检索结论" not in answer


@pytest.mark.asyncio
async def test_same_document_new_topic_is_searched_and_read_again(env, monkeypatch):
    settings, base_task = env
    settings = replace(settings, llm_base_url="https://fake.invalid/v1",
                       llm_api_key="offline-test-key")
    shared_kb = MockKB(settings.mock_kb_path)
    calls = {"search": [], "read": []}
    original_search = shared_kb.search
    original_read = shared_kb.read

    def tracked_search(query, *args, **kwargs):
        calls["search"].append(query)
        return original_search(query, *args, **kwargs)

    def tracked_read(planid, section_idx=None, *args, **kwargs):
        calls["read"].append((planid, section_idx, kwargs.get("chunk_id")))
        return original_read(planid, section_idx, *args, **kwargs)

    monkeypatch.setattr(shared_kb, "search", tracked_search)
    monkeypatch.setattr(shared_kb, "read", tracked_read)

    for question, query, requirement, section_idx in (
        ("请查询planid TEST-001中农业灌溉设施。", "农业灌溉设施",
         "农业灌溉设施的原文事实。", 1),
        ("请查询planid TEST-001中农业人才培养。", "农业人才培养",
         "农业人才培养的原文事实。", 2),
    ):
        task = base_task.model_copy(update={"question": question,
                                            "targets": ["TEST-001"]})
        route = decide(question)
        run = RunContext(settings, shared_kb, task.run_id, task.session_id,
                         question=question, route=route, scope=("TEST-001",))
        runtime = Runtime(settings, task)
        state = ResearchState.create(
            run, question, time.monotonic() + 60,
            explicit_targets=("TEST-001",), allowed_planids=("TEST-001",),
        )
        model = FakeModel([
            [tool("search", query=query, top_k=8, requirements=[requirement])],
            [tool("read_batch", items=[{
                "planid": "TEST-001", "section_idx": section_idx, "offset": 0,
            }])],
            ModelBehaviorError("offline protocol-stop"),
        ])
        await execute_research_core(
            build_research_core_agent(model, settings, runtime), state, runtime)
        assert state.requirements_frozen is True
        assert state.io_calls == 2

    assert calls["search"] == ["农业灌溉设施", "农业人才培养"]
    assert calls["read"] == [("TEST-001", 1, None), ("TEST-001", 2, None)]


@pytest.mark.asyncio
async def test_interrupted_runner_call_is_counted_once_and_marked_interrupted(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    _run, runtime, state = _make_state(settings, task, question)
    model = FakeModel([ModelBehaviorError("offline interrupted request")])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime), state, runtime)
    assert state.model_calls == 1
    assert state.model_usage == [{"call": 1, "status": "interrupted", "elapsed_ms": state.model_usage[0]["elapsed_ms"]}]
    assert state.final_answer is None
    assert answer


@pytest.mark.asyncio
async def test_batch_evidence_rejects_bad_quote_without_discarding_valid_candidate(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    run, runtime, state = _make_state(settings, task, question)
    state.freeze_requirements(["农业灌溉设施的原文事实。"])
    chunk_id = _seed_read_fact(state)
    requirement_ids = [item.id for item in state.requirements]
    model = FakeModel([
        [tool("record_evidence_batch", items=[
            {"candidate_id": "bad", "claim": "虚构", "quote": "不存在的引文",
             "chunk_id": chunk_id, "requirement_ids": requirement_ids},
            {"candidate_id": "good", "claim": "完善农业灌溉设施",
             "quote": "完善农业灌溉设施", "chunk_id": chunk_id,
             "requirement_ids": requirement_ids},
        ])],
        ModelBehaviorError("offline protocol-stop"),
    ])
    await execute_research_core(
        build_research_core_agent(model, settings, runtime), state, runtime)
    assert len(state.claims) == 1
    assert state.claims[0].quotes == ["完善农业灌溉设施"]
    assert state.fact_handles == {state.claims[0].fact_ids[0]: "f1"}


@pytest.mark.asyncio
async def test_finalized_submit_blocks_later_search_in_same_model_reply(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    run, runtime, state = _make_state(settings, task, question)
    state.freeze_requirements(["农业灌溉设施的原文事实。"])
    chunk_id = _seed_read_fact(state)
    requirement_ids = [item.id for item in state.requirements]
    original_labels = [item.label for item in state.requirements]

    async def audit(current, _mapped):
        return json.dumps({
            "passed": True,
            "covered_requirement_ids": [item.id for item in current.requirements],
            "approved_block_ids": ["fact"], "gaps": [], "unsupported_claims": [],
        })

    model = FakeModel([[
        tool("submit_draft", evidence_candidates=[{
            "candidate_id": "fact", "claim": "完善农业灌溉设施",
            "quote": "完善农业灌溉设施", "chunk_id": chunk_id,
            "requirement_ids": requirement_ids,
        }], blocks=[{
            "id": "fact", "title": "灌溉设施", "kind": "fact", "text": "",
            "requirement_ids": requirement_ids, "evidence_ids": ["fact"],
        }]),
        tool("search", query="改变已审核状态", top_k=8,
             requirements=["应被拒绝的新要求"]),
    ]])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime, audit=audit), state, runtime)
    assert state.final_answer == answer
    assert state.io_calls == 0
    assert [item.label for item in state.requirements] == original_labels
    assert "应被拒绝的新要求" not in original_labels
    assert all(item.status == "delivered" for item in state.requirements)


@pytest.mark.asyncio
async def test_plain_text_response_cannot_bypass_audited_submission(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    _run, runtime, state = _make_state(settings, task, question)
    model = FakeModel([
        "未检索来源的自由文本结论。",
        ModelBehaviorError("offline stop after early text"),
    ])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime), state, runtime,
        max_cycles=2,
    )
    assert state.final_answer is None
    assert state.check_result["passed"] is False
    assert "自由文本结论" not in answer


@pytest.mark.asyncio
async def test_orphan_reference_does_not_attach_to_unknown_prior_source(env, monkeypatch):
    settings, base_task = env
    settings = replace(settings, llm_base_url="https://fake.invalid/v1",
                       llm_api_key="offline-test-key")
    question = "你上面提到的第①项具体是什么？"
    task = base_task.model_copy(update={"question": question})
    monkeypatch.setattr(orchestrator, "get_kb",
                        lambda _settings: MockKB(settings.mock_kb_path))
    runtime = Runtime(settings, task)
    result = await orchestrator.run_sdk_task(
        task, settings, runtime, initial_decision=decide(question),
    )
    assert result.meta["qa_context_resolution"]["status"] == "no_prior"
    assert result.claims == []
    assert result.evidence_refs == []
    assert result.meta["research_core"]["status"] == "partial"
