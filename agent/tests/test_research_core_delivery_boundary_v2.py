"""Offline integration boundary for partially approved native research output."""
import json
import time
from dataclasses import replace

import pytest

import guance_agent.orchestrator as orchestrator
from guance_agent.context import RunContext
from guance_agent.kb import MockKB
from guance_agent.output import to_markdown
from guance_agent.research_core import ResearchState
from guance_agent.research_planner import ResearchPlanner
from guance_agent.routing import decide
from guance_agent.runtime import Runtime

from .fake_model import FakeModel, tool


class CapturedContextStore:
    def __init__(self):
        self.turns = []

    def load(self):
        return list(self.turns)

    def append(self, turn):
        self.turns.append(turn)


@pytest.mark.asyncio
async def test_run_sdk_delivery_exposes_only_claims_from_approved_blocks(env, monkeypatch):
    settings, base_task = env
    settings = replace(settings, llm_base_url="https://fake.invalid/v1",
                       llm_api_key="offline-test-key")
    question = "请查询planid TEST-001中的农业灌溉措施，并判断这些措施是否提高产量。"
    task = base_task.model_copy(update={"question": question, "targets": ["TEST-001"]})
    decision = decide(question)
    kb = MockKB(settings.mock_kb_path)
    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)

    supported_quote = "完善农业灌溉设施"
    unreferenced_quote = "发展绿色农业"
    chunk_id = "TEST-001__s01__p00"
    monkeypatch.setattr(
        kb, "verify_quote",
        lambda _chunk_id, quote, *_args, **_kwargs:
            quote in {supported_quote, unreferenced_quote},
        raising=False,
    )

    proposals = ["农业灌溉措施的原文事实。", "灌溉设施对农业产量的效果判断。"]
    planning_context = RunContext(
        settings, kb, task.run_id, task.session_id, question=question,
        route=decision, scope=("TEST-001",),
    )
    planned = ResearchState.create(
        planning_context, question, time.monotonic() + 60,
        explicit_targets=("TEST-001",), allowed_planids=("TEST-001",),
        approved_source_periods=(),
    )
    frozen = planned.freeze_requirements(proposals)
    requirement_ids = {item["label"]: item["id"] for item in frozen["requirements"]}
    irrigation_id = requirement_ids[proposals[0]]
    effect_id = requirement_ids[proposals[1]]
    model = FakeModel([
        [tool("search", query="农业灌溉", top_k=8, requirements=proposals)],
        [tool("read_batch", items=[{
            "planid": "TEST-001", "chunk_id": chunk_id, "offset": 0,
        }])],
        [tool("submit_draft", evidence_candidates=[
            {
                "candidate_id": "irrigation-fact",
                "claim": supported_quote,
                "quote": supported_quote,
                "chunk_id": chunk_id,
                "requirement_ids": [irrigation_id, effect_id],
            },
            {
                "candidate_id": "unreferenced-fact",
                "claim": unreferenced_quote,
                "quote": unreferenced_quote,
                "chunk_id": chunk_id,
                "requirement_ids": [irrigation_id],
            },
            {
                "candidate_id": "unsupported-effect",
                "claim": "灌溉设施显著提高农业产量。",
                "quote": supported_quote,
                "chunk_id": chunk_id,
                "requirement_ids": [effect_id],
            },
        ], blocks=[
            {
                "id": "supported-fact", "title": "灌溉措施", "kind": "fact",
                "text": "", "requirement_ids": [irrigation_id, effect_id],
                "evidence_ids": ["irrigation-fact"],
            },
            {
                "id": "rejected-analysis", "title": "增产效果", "kind": "analysis",
                "text": "灌溉设施显著提高农业产量。",
                "requirement_ids": [effect_id],
                "evidence_ids": ["irrigation-fact", "unsupported-effect"],
            },
        ], finish_if_partial=True)],
    ])
    monkeypatch.setattr(orchestrator, "chat_model", lambda *_args: model)

    runtime = Runtime(settings, task)
    store = CapturedContextStore()
    runtime.context_store = store

    async def discover(_task, contract, plan, _deadline):
        return ResearchPlanner.with_catalog_documents(contract, plan, [{
            "planid": "TEST-001", "title": "甲市农业发展规划",
            "area": "甲市", "fyp": None, "document_id": "doc-test",
            "generation_id": "generation-test", "source": "policy_catalog",
            "source_hash": "source-hash-test",
        }])

    async def partial_audit(messages, **_kwargs):
        payload = json.loads(messages[1]["content"])
        requirements = payload["frozen_requirements"]
        irrigation = next(item["id"] for item in requirements
                          if item["label"] == proposals[0])
        effect = next(item["id"] for item in requirements
                      if item["label"] == proposals[1])
        return json.dumps({
            "passed": False,
            "covered_requirement_ids": [irrigation],
            "approved_block_ids": ["supported-fact"],
            "gaps": [effect],
            "unsupported_claims": ["unsupported-effect"],
        }, ensure_ascii=False)

    monkeypatch.setattr(runtime, "complete_text", partial_audit)
    result = await orchestrator.run_sdk_task(
        task, settings, runtime, initial_decision=decision,
        research_discoverer=discover,
    )

    assert result.meta["research_core"]["status"] == "partial"
    assert result.answer.count(supported_quote) == 1
    assert unreferenced_quote not in result.answer
    assert "灌溉设施显著提高农业产量" not in result.answer
    assert result.structured["coverage"]["status"] == "partial"
    assert [block["id"] for block in result.structured["blocks"]] == ["supported-fact"]
    assert result.structured["coverage"]["covered"] == [irrigation_id]
    assert set(result.structured["coverage"]["missing"]) == {
        item.id for item in planned.requirements if item.id != irrigation_id
    }
    assert result.structured["blocks"][0]["requirement_ids"] == [irrigation_id]
    quality_by_requirement = {
        item["id"]: item for item in result.meta["research_quality"]["requirement_coverage"]
    }
    assert quality_by_requirement[effect_id]["status"] == "missing"
    assert quality_by_requirement[effect_id]["evidence_claim_indexes"] == []
    assert [claim.text for claim in result.claims] == [supported_quote]
    assert [ref.chunk_id for ref in result.evidence_refs] == [chunk_id]

    rendered = to_markdown(result)
    assert unreferenced_quote not in rendered
    assert "灌溉设施显著提高农业产量" not in rendered
    assert rendered.count(supported_quote) == 3

    assert len(store.turns) == 1
    saved = store.turns[0]
    assert [document["planid"] for document in saved["documents"]] == ["TEST-001"]
    assert [fact["text"] for fact in saved["verified_facts"]] == [supported_quote]
    assert saved["verified_chunk_ids"] == [chunk_id]
    assert saved["semantic_trust"] == "unreviewed"
