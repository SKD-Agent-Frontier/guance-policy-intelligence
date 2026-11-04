import json
import time
from dataclasses import replace

import pytest
from agents.exceptions import ModelBehaviorError

import guance_agent.orchestrator as orchestrator
from guance_agent.context import RunContext
from guance_agent.kb import MockKB
from guance_agent.output import to_markdown
from guance_agent.research_core import ResearchState
from guance_agent.research_planner import ResearchPlanner
from guance_agent.routing import decide
from guance_agent.runtime import Runtime
from guance_agent.schemas import Claim, EvidenceRef, RunResult
from guance_agent.verify import verify_quotes

from .fake_model import FakeModel, tool


@pytest.mark.asyncio
async def test_run_sdk_task_full_offline_native_research_path(env, monkeypatch):
    settings, base_task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    settings = replace(settings, llm_base_url="https://fake.invalid/v1",
                       llm_api_key="offline-test-key")
    task = base_task.model_copy(update={
        "question": question,
        "targets": ["TEST-001"],
        "created_at": "2026-10-23T00:00:00Z",
    })
    decision = decide(question)
    kb = MockKB(settings.mock_kb_path)
    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)

    run_context = orchestrator.RunContext(
        settings, kb, task.run_id, task.session_id, question=question,
        route=decision, scope=("TEST-001",),
    )
    proposals = ["农业灌溉设施的原文事实。"]
    planned_state = ResearchState.create(
        run_context, question, time.monotonic() + 60,
        explicit_targets=("TEST-001",), allowed_planids=("TEST-001",),
        approved_source_periods=(),
    )
    frozen = planned_state.freeze_requirements(proposals)
    requirement_ids = [item["id"] for item in frozen["requirements"]]
    source_quote = "完善农业灌溉设施"
    monkeypatch.setattr(kb, "verify_quote",
                        lambda _chunk_id, quote, *_args, **_kwargs: quote == source_quote,
                        raising=False)

    async def audit(state, _mapped):
        return json.dumps({
            "passed": True,
            "covered_requirement_ids": [item.id for item in state.requirements],
            "approved_block_ids": ["irrigation-fact"],
            "gaps": [], "unsupported_claims": [],
        }, ensure_ascii=False)

    model = FakeModel([
        [tool("search", query="农业灌溉设施", top_k=8, requirements=proposals)],
        [tool("read_batch", items=[{
            "planid": "TEST-001", "chunk_id": "TEST-001__s01__p00", "offset": 0,
        }])],
        [tool("submit_draft", evidence_candidates=[{
            "candidate_id": "irrigation", "claim": source_quote,
            "quote": source_quote, "chunk_id": "TEST-001__s01__p00",
            "requirement_ids": requirement_ids,
        }], blocks=[{
            "id": "irrigation-fact", "title": "农业灌溉设施", "kind": "fact",
            "text": "", "requirement_ids": requirement_ids,
            "evidence_ids": ["irrigation"],
        }])],
    ])
    monkeypatch.setattr(orchestrator, "chat_model", lambda *_args: model)

    discover_calls = []

    async def discover(discovery_task, contract, plan, _deadline):
        discover_calls.append((discovery_task.targets, contract.scope.target_planids))
        return ResearchPlanner.with_catalog_documents(contract, plan, [{
            "planid": "TEST-001", "title": "甲市农业发展规划",
            "area": "甲市", "fyp": None, "document_id": "doc-test",
            "generation_id": "generation-test", "source": "policy_catalog",
            "source_hash": "source-hash-test",
        }])

    runtime = Runtime(settings, task)
    async def fake_audit(messages, **_kwargs):
        payload = json.loads(messages[1]["content"])
        return json.dumps({
            "passed": True,
            "covered_requirement_ids": [item["id"]
                                         for item in payload["frozen_requirements"]],
            "approved_block_ids": [item["id"] for item in payload["blocks"]],
            "gaps": [], "unsupported_claims": [],
        }, ensure_ascii=False)

    monkeypatch.setattr(runtime, "complete_text", fake_audit)
    result = await orchestrator.run_sdk_task(
        task, settings, runtime, initial_decision=decision,
        research_discoverer=discover,
    )

    assert discover_calls == [(["TEST-001"], ["TEST-001"])]
    assert model.calls == 3
    assert result.meta["qa_execution_mode"] == "sdk_research"
    assert result.meta["research_discovery"]["documents"][0]["planid"] == "TEST-001"
    assert result.meta["research_core"]["audit"]["passed"] is True
    assert result.meta["research_quality"]["status"] == "complete"
    assert result.structured["schema_version"] == "qa-report/1"
    assert result.structured["blocks"][0]["text"] == source_quote
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_extended_scope_discovers_new_region_before_union(env, monkeypatch):
    settings, base_task = env
    settings = replace(settings, llm_base_url="https://fake.invalid/v1",
                       llm_api_key="offline-test-key")
    question = "与河北省相关规划相比，衡水市产业结构有什么特色？"
    task = base_task.model_copy(update={"question": question})
    previous = {
        "question": "衡水市产业结构的特色是什么？",
        "documents": [{"planid": "DOC-HS", "title": "衡水市产业规划",
                        "area": "衡水市", "fyp": "第十三个五年规划",
                        "document_id": "hs-document", "generation_id": "hs-generation",
                        "source": "policy_catalog"}],
        "resolution_status": "continued",
        "user_scope": {"areas": ["衡水市"], "periods": ["第十三个五年规划"],
                       "planids": ["DOC-HS"], "titles": [], "requirements": []},
        "verified_facts": [], "evidence_refs": [],
    }

    class ContextStore:
        def load(self):
            return [previous]

        def append(self, _turn):
            return None

    kb = MockKB(settings.mock_kb_path)
    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)
    model = FakeModel([
        [tool("search", query="产业结构", top_k=8,
              requirements=["衡水市产业结构特色", "河北省相关规划产业结构特征"])],
        ModelBehaviorError("offline protocol-stop"),
    ])
    monkeypatch.setattr(orchestrator, "chat_model", lambda *_args: model)
    runtime = Runtime(settings, task)
    runtime.context_store = ContextStore()
    discovered_calls = []

    async def discover(discovery_task, contract, plan, _deadline):
        discovered_calls.append((discovery_task.targets, tuple(contract.scope.areas)))
        return ResearchPlanner.with_catalog_documents(contract, plan, [{
            "planid": "DOC-HEBEI", "title": "河北省产业发展规划",
            "area": "河北省", "fyp": None, "document_id": "hebei-document",
            "generation_id": "hebei-generation", "source": "policy_catalog",
            "source_hash": "hebei-source-hash",
        }])

    result = await orchestrator.run_sdk_task(
        task, settings, runtime, initial_decision=decide(question),
        research_discoverer=discover,
    )

    assert result.meta["qa_context_resolution"]["status"] == "extended"
    assert discovered_calls[0][0] is None
    assert "河北省" in discovered_calls[0][1]
    assert set(result.meta["research_core"]["authorized_scope"]["planids"]) == {
        "DOC-HS", "DOC-HEBEI",
    }
    assert result.meta["research_core"]["authorized_scope"]["source_periods"] == []
    assert result.meta["research_core"]["protocol_error_type"] == "ModelBehaviorError"


@pytest.mark.asyncio
async def test_research_core_quote_budget_verifies_over_twenty_reread_facts(env, monkeypatch):
    settings, task = env
    kb = MockKB(settings.mock_kb_path)
    source = "完善农业灌溉设施。"
    quote = "完善农业灌溉设施"
    monkeypatch.setattr(kb, "verify_quote",
                        lambda _chunk_id, candidate, *_args, **_kwargs: candidate == quote,
                        raising=False)
    context = RunContext(settings, kb, task.run_id, task.session_id,
                         question=task.question, route=decide(task.question))
    chunk_id = "TEST-001__s01__p00"
    ref = EvidenceRef(
        planid="TEST-001", chunk_id=chunk_id, title="农业发展规划", area="甲市",
        fyp=None, admin_level=None, plantype_label=None, section_idx=1,
        section_path="农业发展", char_start=0, char_end=len(source),
        chunk_char_start=0, chunk_char_end=len(source), source="kb_read",
        observed_ranges=[(0, len(source))], complete=True,
    )
    context.evidence.register(ref)
    result = RunResult(
        run_id=task.run_id, session_id=task.session_id, status="completed",
        evidence_status="insufficient", answer=quote,
        claims=[Claim(text=quote, chunk_ids=[chunk_id], quotes=[quote],
                      fact_ids=[f"fact_{index:02d}"])
                for index in range(21)],
        evidence_refs=[ref], dropped_chunk_ids=[], error=None,
        meta={"research_core": {"facts_reread": 21}},
    )

    await verify_quotes(result, context)

    assert result.meta["quote_verification"]["checked"] == 21
    assert result.meta["quote_verification"]["approved"] == 21
    assert result.meta["quote_verification"]["unverified"] == 0
    assert len(result.claims) == 21


@pytest.mark.asyncio
async def test_semantically_rejected_block_claims_do_not_escape_delivery(env, monkeypatch):
    settings, base_task = env
    settings = replace(settings, max_tool_calls=2,
                       llm_base_url="https://fake.invalid/v1", llm_api_key="offline-test-key")
    question = "請查planid TEST-001中的灌溉事實和灌溉作用分析。"
    task = base_task.model_copy(update={"question": question, "targets": ["TEST-001"]})
    decision = decide(question)
    kb = MockKB(settings.mock_kb_path)
    source = "完善农业灌溉设施，发展绿色农业。"
    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)
    monkeypatch.setattr(kb, "verify_quote",
                        lambda _chunk_id, quote, *_args, **_kwargs: quote in source,
                        raising=False)

    planned = ResearchState.create(
        orchestrator.RunContext(settings, kb, task.run_id, task.session_id,
                                 question=question, route=decision,
                                 scope=("TEST-001",)),
        question, time.monotonic() + 60, explicit_targets=("TEST-001",),
        allowed_planids=("TEST-001",), approved_source_periods=(),
    )
    frozen = planned.freeze_requirements([
        "农业灌溉措施的原文事实。", "灌溉措施的作用分析。",
    ])
    requirements = {item["label"]: item["id"] for item in frozen["requirements"]}
    fact_requirement = requirements["农业灌溉措施的原文事实。"]
    analysis_requirement = requirements["灌溉措施的作用分析。"]
    root_requirement = next(item.id for item in planned.requirements
                            if item.label == question)
    first_quote = "完善农业灌溉设施"
    rejected_quote = "发展绿色农业"

    model = FakeModel([
        [tool("search", query="农业灌溉", top_k=8,
              requirements=["农业灌溉措施的原文事实。", "灌溉措施的作用分析。"])],
        [tool("read_batch", items=[{
            "planid": "TEST-001", "chunk_id": "TEST-001__s01__p00", "offset": 0,
        }])],
        [tool("submit_draft", evidence_candidates=[
            {"candidate_id": "supported", "claim": first_quote, "quote": first_quote,
             "chunk_id": "TEST-001__s01__p00",
             "requirement_ids": [root_requirement, fact_requirement]},
            {"candidate_id": "rejected", "claim": rejected_quote, "quote": rejected_quote,
             "chunk_id": "TEST-001__s01__p00",
             "requirement_ids": [analysis_requirement]},
        ], blocks=[
            {"id": "supported-fact", "title": "灌溉措施", "kind": "fact",
             "text": "", "requirement_ids": [root_requirement, fact_requirement],
             "evidence_ids": ["supported"]},
            {"id": "rejected-analysis", "title": "作用判断", "kind": "analysis",
             "text": "灌溉设施显著提高农业产量。",
             "requirement_ids": [analysis_requirement],
             "evidence_ids": ["supported", "rejected"]},
        ], finish_if_partial=True)],
    ])
    monkeypatch.setattr(orchestrator, "chat_model", lambda *_args: model)

    async def discover(_task, contract, plan, _deadline):
        return ResearchPlanner.with_catalog_documents(contract, plan, [{
            "planid": "TEST-001", "title": "甲市农业发展规划", "area": "甲市",
            "fyp": None, "document_id": "doc-test", "generation_id": "generation-test",
            "source": "policy_catalog", "source_hash": "source-hash-test",
        }])

    runtime = Runtime(settings, task)

    async def partial_audit(messages, **_kwargs):
        return json.dumps({
            "passed": False,
            "covered_requirement_ids": [root_requirement, fact_requirement],
            "approved_block_ids": ["supported-fact"],
            "gaps": [analysis_requirement],
            "unsupported_claims": ["rejected-analysis"],
        }, ensure_ascii=False)

    monkeypatch.setattr(runtime, "complete_text", partial_audit)
    result = await orchestrator.run_sdk_task(
        task, settings, runtime, initial_decision=decision, research_discoverer=discover,
    )

    assert result.meta["research_core"]["status"] == "partial"
    assert [claim.text for claim in result.claims] == [first_quote]
    assert [block["id"] for block in result.structured["blocks"]] == ["supported-fact"]
    assert rejected_quote not in result.answer
    assert rejected_quote not in to_markdown(result)
