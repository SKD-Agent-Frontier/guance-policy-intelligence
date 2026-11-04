"""QA report finalization stays on the ordinary, budgeted run path."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import guance_agent.orchestrator as orchestrator
from guance_agent.agent import build_agent as real_build_agent
from guance_agent.kb import MockKB
from guance_agent.runtime import Runtime
from guance_agent.schemas import RunResult

from .fake_model import FakeModel, tool


def _configured(env):
    return replace(env[0], llm_base_url="https://example.invalid/v1", llm_api_key="offline-key")


def _report_payload(hit):
    quote = hit.content[:24]
    claim = {"text": "政策条文提出农业发展任务。", "chunk_ids": [hit.chunk_id],
             "quotes": [quote]}
    return json.dumps({
        "answer": "本报告整理一个政策维度。",
        "claims": [claim],
        "insufficient": False,
        "report": {"dimensions": ["支持措施"], "blocks": [{
            "id": "fact-1", "title_path": ["支持措施"], "dimension": "支持措施",
            "kind": "fact", "text": "政策条文提出农业发展任务。",
            "supporting_claim_indexes": [0],
        }]},
    }, ensure_ascii=False)


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


def _install_model(monkeypatch, settings, script):
    model = FakeModel(script)
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda _settings: object())
    monkeypatch.setattr(orchestrator, "chat_model", lambda _settings, _client: object())
    monkeypatch.setattr(orchestrator, "build_agent",
                        lambda _settings, _model: real_build_agent(settings, model))


@pytest.mark.asyncio
async def test_submit_reviews_verified_report_once_and_publishes_qa_report_1(env, monkeypatch):
    settings, task = _configured(env), env[1]
    kb = VerifiableMockKB(settings.mock_kb_path)
    hit = kb.search("农业", None, None, 1)[0]
    _install_model(monkeypatch, settings, [
        [tool("kb_search", query="农业", area=None, fyp=None, top_k=1)],
        _report_payload(hit),
    ])
    reviewed = []

    async def review(self, messages, *, max_tokens, timeout, purpose="text_completion"):
        payload = json.loads(messages[1]["content"].split("\n", 1)[1])
        reviewed.append((messages, max_tokens, timeout))
        return json.dumps({
            "facts": [{"index": item["index"], "supported": True}
                      for item in payload["facts"]],
            "blocks": [{"id": item["id"], "supported": True}
                       for item in payload["blocks"]],
        }, ensure_ascii=False)

    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)
    monkeypatch.setattr(Runtime, "complete_text", review)
    result = await orchestrator.run_task(task, settings, Runtime(settings, task))

    assert result.status == "completed"
    assert result.meta["qa_report"]["review_model_calls"] == 1
    assert len(reviewed) == 1 and reviewed[0][1] > 0 and reviewed[0][2] > 0
    assert result.structured["schema_version"] == "qa-report/1"
    assert result.structured["blocks"][0]["verification"] == "model_reviewed_evidence_linked"
    assert "农业发展任务" in result.answer


@pytest.mark.asyncio
async def test_submit_report_review_timeout_fails_closed_without_raw_report(env, monkeypatch):
    settings, task = _configured(env), env[1]
    kb = VerifiableMockKB(settings.mock_kb_path)
    hit = kb.search("农业", None, None, 1)[0]
    _install_model(monkeypatch, settings, [
        [tool("kb_search", query="农业", area=None, fyp=None, top_k=1)],
        _report_payload(hit),
    ])

    async def timeout(self, messages, *, max_tokens, timeout, purpose="text_completion"):
        raise TimeoutError

    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)
    monkeypatch.setattr(Runtime, "complete_text", timeout)
    result = await orchestrator.run_task(task, settings, Runtime(settings, task))

    assert result.status == "completed"
    assert result.meta["qa_report"]["semantic_review"] == "timeout"
    assert result.structured["blocks"] == []
    assert "农业发展任务" not in result.answer


@pytest.mark.asyncio
async def test_multiobject_report_marks_object_without_evidence_as_missing(env, monkeypatch):
    settings, base_task = _configured(env), env[1]
    kb = VerifiableMockKB(settings.mock_kb_path)
    hit = kb.search("农业", None, None, 1)[0]
    task = base_task.model_copy(update={"targets": [hit.planid, "MISSING-POLICY"]})
    _install_model(monkeypatch, settings, [
        [tool("kb_search", query="农业", area=None, fyp=None, top_k=1)],
        _report_payload(hit),
        [tool("kb_search", query="农业", area=None, fyp=None, top_k=1)],
        json.dumps({"answer": "没有取得正文", "claims": [], "insufficient": True},
                   ensure_ascii=False),
    ])

    async def review(self, messages, *, max_tokens, timeout, purpose="text_completion"):
        payload = json.loads(messages[1]["content"].split("\n", 1)[1])
        return json.dumps({
            "facts": [{"index": item["index"], "supported": True}
                      for item in payload["facts"]],
            "blocks": [{"id": item["id"], "supported": True}
                       for item in payload["blocks"]],
        }, ensure_ascii=False)

    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)
    monkeypatch.setattr(Runtime, "complete_text", review)
    result = await orchestrator.run_task(task, settings, Runtime(settings, task))

    assert result.structured["coverage"]["status"] == "partial"
    assert "planid MISSING-POLICY（未取得证据）" in result.structured["coverage"]["missing"]
    assert len(result.structured["blocks"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["qa", "extract"])
async def test_no_report_or_non_qa_never_calls_report_finalizer(env, monkeypatch, task_type):
    settings = _configured(env)
    task = env[1].model_copy(update={"task_type": task_type})
    calls = []

    async def fake_single(task, _settings, _ctx, _rt, _deadline, _decision):
        return RunResult(run_id=task.run_id, session_id=task.session_id,
                         status="completed", evidence_status="none", answer="旧格式回答",
                         claims=[], evidence_refs=[], dropped_chunk_ids=[], error=None, meta={})

    async def no_verify(_result, _ctx):
        return 0

    async def should_not_finalize(*args):
        calls.append(args)
        raise AssertionError("未提供 report 或非 QA 任务不应进行语义审核")

    monkeypatch.setattr(orchestrator, "_run_single", fake_single)
    monkeypatch.setattr(orchestrator, "verify_quotes", no_verify)
    monkeypatch.setattr(orchestrator, "verify_numbers", no_verify)
    monkeypatch.setattr(orchestrator, "finalize_report", should_not_finalize)
    result = await orchestrator.run_task(task, settings, Runtime(settings, task))

    assert result.answer == "旧格式回答"
    assert calls == []


@pytest.mark.asyncio
async def test_qa_report_retractions_happen_before_research_contract_evaluation(env, monkeypatch):
    """A QA report must not remain contract-complete after semantic review withdraws its evidence."""
    settings, task = _configured(env), env[1]
    kb = VerifiableMockKB(settings.mock_kb_path)
    hit = kb.search("农业", None, None, 1)[0]
    _install_model(monkeypatch, settings, [
        [tool("kb_search", query="农业", area=None, fyp=None, top_k=1)],
        _report_payload(hit),
    ])

    async def reject(self, messages, *, max_tokens, timeout, purpose="text_completion"):
        payload = json.loads(messages[1]["content"].split("\n", 1)[1])
        return json.dumps({
            "facts": [{"index": item["index"], "supported": False}
                      for item in payload["facts"]],
            "blocks": [{"id": item["id"], "supported": False}
                       for item in payload["blocks"]],
        }, ensure_ascii=False)

    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)
    monkeypatch.setattr(Runtime, "complete_text", reject)
    result = await orchestrator.run_task(task, settings, Runtime(settings, task))

    assert result.meta["qa_report"]["semantic_review"] == "model_reviewed"
    assert result.structured["schema_version"] == "qa-report/1"
    assert result.structured["blocks"] == []
    assert result.claims == []
    assert result.meta["research"]["status"] == "insufficient"
    assert "verified_claims_missing" in result.meta["research"]["gaps"]
    assert "农业发展任务" not in result.answer


@pytest.mark.asyncio
async def test_truncated_claims_reach_readable_answer_after_actual_verification(env, monkeypatch):
    """The ordinary run_task pipeline must publish a verified partial report, not placeholder text."""
    from guance_agent.runner import ExecOutcome

    settings, task = _configured(env), env[1]
    settings = replace(settings, llm_max_output_tokens=8)
    kb = VerifiableMockKB(settings.mock_kb_path)
    hit = kb.search("农业", None, None, 1)[0]
    quote = hit.content[:24]
    raw = json.dumps({
        "answer": "未完成",
        "claims": [{"text": "农业发展任务", "chunk_ids": [hit.chunk_id],
                    "quotes": [quote]}],
    }, ensure_ascii=False)
    # The final claim must be fully closed, while the following JSON content
    # is truncated at the output cap; do not recover an unfinished claim.
    raw = raw[:-1] + ', "report": {"dimensions": ['
    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda _settings: object())

    async def limited_response(_task, _settings, ctx, rt, _deadline):
        ctx.evidence.add_result([hit], "kb_search")
        ctx.tool_calls.append({"name": "kb_search", "status": "ok", "message": ""})
        return ExecOutcome(raw, None, telemetry={
            "model_calls": 1, "input_tokens": 200, "output_tokens": 8,
            "per_call": [{"input": 200, "output": 8}],
        })

    monkeypatch.setattr(orchestrator, "_execute_once", limited_response)
    result = await orchestrator.run_task(task, settings, Runtime(settings, task))

    assert result.status == "completed"
    assert result.meta["output_parse"]["recovery"] == "complete_claims_only"
    assert result.claims
    assert result.evidence_refs
    assert "农业发展任务" in result.answer
    assert "planid=" in result.answer
    assert "仅保留已完整生成的政策事实" not in result.answer
    assert result.structured["qa_recovery"]["status"] == "partial"
    assert result.evidence_status == "insufficient"
    assert result.meta["research"]["status"] == "partial"
