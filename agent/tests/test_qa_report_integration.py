"""QA report finalization stays on the ordinary, budgeted run path."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import guance_agent.orchestrator as orchestrator
from guance_agent.kb import MockKB
from guance_agent.runtime import Runtime

from .fake_model import FakeModel, tool


def _configured(env):
    return replace(env[0], llm_base_url="https://example.invalid/v1", llm_api_key="offline-key")


class VerifiableMockKB(MockKB):
    def verify_quote(self, chunk_id, quote, observed_ranges, **_kwargs):
        for document in self.docs:
            chunk = next((item for item in document["chunks"]
                          if item["chunk_id"] == chunk_id), None)
            if chunk is None:
                continue
            return any(quote in document["body"][max(start, chunk["char_start"]):
                                                  min(end, chunk["char_end"])]
                       for start, end in observed_ranges)
        return False

    def get_chunk_source(self, chunk_id, **_kwargs):
        for document in self.docs:
            if any(item["chunk_id"] == chunk_id for item in document["chunks"]):
                return SimpleNamespace(content=document["body"], chunk_char_start=0)
        raise KeyError(chunk_id)


async def _run_report(env, monkeypatch, *, approve=True, targets=None, text=None,
                      truncate=False):
    from agents.exceptions import ModelBehaviorError

    from .fake_model import fact_submission, install_audit, sdk_read, sdk_search

    settings, task = _configured(env), env[1]
    kb = VerifiableMockKB(settings.mock_kb_path)
    hit = kb.search("农业", None, None, 1)[0]
    task = task.model_copy(update={"targets": targets, "question": "农业"})
    script = [[sdk_search()], [sdk_read(hit)], fact_submission(hit, text=text)]
    if truncate:
        def record(instructions):
            submission = fact_submission(hit)(instructions)[0]
            candidates = json.loads(submission.arguments)["evidence_candidates"]
            return [tool("record_evidence_batch", items=candidates)]
        script[2:] = [record, ModelBehaviorError("truncated draft")]
    model = FakeModel(script)
    reviews = install_audit(monkeypatch, approve=approve)
    monkeypatch.setattr(orchestrator, "get_kb", lambda _: kb)
    monkeypatch.setattr(orchestrator, "chat_model", lambda *_: model)
    result = await orchestrator.run_task(task, settings, Runtime(settings, task))
    return result, hit, reviews


async def test_report_review_once_on_the_sdk_path(env, monkeypatch):
    result, hit, reviews = await _run_report(env, monkeypatch)
    assert len(reviews) == 1 and reviews[0][1] > 0 and reviews[0][2] > 0
    assert result.structured["schema_version"] == "qa-report/1"
    assert result.structured["blocks"][0]["supporting_chunk_ids"] == [hit.chunk_id]
    assert result.evidence_status == "verified" and hit.content[:24].strip() in result.answer


async def test_review_timeout_keeps_only_the_verified_excerpt(env, monkeypatch):
    result, hit, reviews = await _run_report(env, monkeypatch, approve=TimeoutError())
    assert reviews and result.claims and result.claims[0].text == hit.content[:24].strip()
    assert [r.chunk_id for r in result.evidence_refs] == [hit.chunk_id]
    assert hit.content[:24].strip() in result.answer
    assert result.meta["research_delivery"]["status"] == "partial"
    assert not result.meta["research_delivery"]["audited_analysis"]


async def test_object_without_evidence_is_a_report_gap(env, monkeypatch):
    result, hit, _ = await _run_report(env, monkeypatch, targets=["TEST-001", "MISSING-POLICY"])
    assert hit.planid == "TEST-001" and result.claims
    assert result.route["objects"][1]["status"] == "insufficient"
    assert result.structured["coverage"]["status"] == "partial"
    assert len(result.structured["blocks"]) == 1


@pytest.mark.parametrize("task_type", ["qa", "extract"])
async def test_unstructured_model_prose_does_not_trigger_review_or_delivery(env, monkeypatch, task_type):
    from .fake_model import install_audit

    settings, task = _configured(env), env[1].model_copy(update={"task_type": task_type})
    model = FakeModel(["旧格式回答"])
    monkeypatch.setattr(orchestrator, "chat_model", lambda *_: model)
    reviews = install_audit(monkeypatch)
    result = await orchestrator.run_task(task, settings, Runtime(settings, task))
    assert "旧格式回答" not in result.answer and not result.claims
    assert reviews == []


async def test_rejected_paraphrase_is_removed_before_contract_evaluation(env, monkeypatch):
    result, _, reviews = await _run_report(env, monkeypatch, text="农业发展任务", approve=False)
    assert reviews and not result.claims and not result.evidence_refs
    assert not result.structured and "农业发展任务" not in result.answer
    assert result.meta["research"]["status"] == "insufficient"
    assert "verified_claims_missing" in result.meta["research"]["gaps"]


async def test_truncated_draft_keeps_only_previously_recorded_verbatim_facts(env, monkeypatch):
    result, hit, reviews = await _run_report(env, monkeypatch, truncate=True)
    assert not reviews and result.claims and result.evidence_refs
    assert hit.content[:24].strip() in result.answer
    assert result.meta["research_core"]["protocol_error_type"] == "ModelBehaviorError"
    assert result.meta["research_delivery"]["status"] == "partial"
    assert not result.meta["research_delivery"]["audited_analysis"]
