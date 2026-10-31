"""PR13 diagnosis: QA must expose retrieval faults as run failures."""
import time
from dataclasses import replace

import pytest

from guance_agent.agent import build_agent as real_build_agent
from guance_agent.context import RunContext
from guance_agent.kb import MockKB
from guance_agent.orchestrator import run_task
from guance_agent.output import build_result, finalize_qa_retrieval
from guance_agent.runner import ExecOutcome
from guance_agent.runtime import Runtime
from guance_agent.schemas import AgentOutput, Claim

from .fake_model import FakeModel, answer, tool


def _completed_claim_result(env, task_type, backend, question):
    settings, base_task = env
    task = base_task.model_copy(update={"task_type": task_type, "question": question})
    kb = MockKB(settings.mock_kb_path)
    ctx = RunContext(settings, kb, task.run_id, task.session_id)
    hit = kb.search("农业", None, None, 1)[0]
    ctx.evidence.add_result([hit], "kb_search")
    outcome = ExecOutcome(AgentOutput(answer="依据已读原文作答", claims=[
        Claim(text="农业相关结论", chunk_ids=[hit.chunk_id])]), None)
    return task, build_result(task, outcome, ctx, {"kb_backend": backend})


def _offline_model(monkeypatch, script):
    model = FakeModel(script)
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda settings: object())
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda settings, client: object())
    monkeypatch.setattr("guance_agent.orchestrator.build_agent",
                        lambda settings, unused_model: real_build_agent(settings, model))
    return model


async def _run(env, monkeypatch, script, *, fail=None, delay=0, timeout=1, kb=None):
    settings, task = env
    settings = replace(settings, llm_base_url="https://example.invalid/v1",
                       llm_api_key="offline-only", tool_timeout=timeout)
    kb = kb or MockKB(settings.mock_kb_path, fail=fail, delay=delay)
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda unused_settings: kb)
    model = _offline_model(monkeypatch, script)
    runtime = Runtime(settings, task)
    runtime.received()
    runtime.prepare()
    try:
        result = await run_task(task, settings, runtime)
    finally:
        runtime.close()
    return result, model


def _fault_call(operation):
    if operation == "search":
        return tool("kb_search", query="农业", area=None, fyp=None, top_k=8)
    return tool("kb_read", planid="TEST-001", section_idx=0)


@pytest.mark.parametrize("operation", ["search", "read"])
@pytest.mark.parametrize("fault,expected_code,expected_message", [
    ("timeout", "RAG_TIMEOUT", "检索服务超时，本次问答未能完成检索。"),
    ("backend_error", "RAG_BACKEND_ERROR", "检索服务发生错误，本次问答未能完成检索。"),
])
async def test_qa_retrieval_fault_without_claims_fails(env, monkeypatch, operation,
                                                       fault, expected_code, expected_message):
    delay = .08 if fault == "timeout" else 0
    result, model = await _run(
        env, monkeypatch, [[_fault_call(operation)], answer("无可用结论", insufficient=True)],
        fail=operation if fault == "backend_error" else None,
        delay=delay, timeout=.01 if fault == "timeout" else 1,
    )

    assert model.calls == 2
    assert result.meta["tool_calls"][0]["status"] == fault
    assert not result.claims and not result.evidence_refs
    assert result.status == "failed"
    assert result.error and result.error.code == expected_code
    assert result.error.message == expected_message
    assert "未检索到" not in result.error.message
    assert result.meta["retrieval_failure"] == {
        "code": expected_code,
        "timeout_count": int(fault == "timeout"),
        "backend_error_count": int(fault == "backend_error"),
        "tool_names": [f"kb_{operation}"],
    }


@pytest.mark.parametrize("operation,call,fault", [
    ("search", tool("kb_search", query="量子通信", area=None, fyp=None, top_k=8), "empty"),
    ("read", tool("kb_read", planid="TEST-003", section_idx=None), "no_text"),
    ("read", tool("kb_read", planid="missing", section_idx=None), "not_found"),
])
async def test_qa_expected_no_match_statuses_remain_completed(env, monkeypatch,
                                                               operation, call, fault):
    result, _ = await _run(env, monkeypatch, [[call], answer("没有找到", insufficient=True)])

    assert result.status == "completed"
    assert result.error is None
    assert result.meta["tool_calls"][0]["status"] == fault
    assert not result.claims and not result.evidence_refs


async def test_extract_retrieval_timeout_semantics_are_unchanged(env, monkeypatch):
    settings, qa_task = env
    task = qa_task.model_copy(update={"task_type": "extract"})
    settings = replace(settings, llm_base_url="https://example.invalid/v1",
                       llm_api_key="offline-only", tool_timeout=.01)
    kb = MockKB(settings.mock_kb_path, delay=.08)
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda unused_settings: kb)
    runtime = Runtime(settings, task)
    runtime.received()
    runtime.prepare()
    try:
        result = await run_task(task, settings, runtime)
    finally:
        runtime.close()

    assert result.task_type == "extract"
    assert result.status == "completed" and result.error is None
    assert result.meta["tool_calls"][0]["status"] == "timeout"
    assert "retrieval_failure" not in result.meta


@pytest.mark.parametrize("operation", ["search", "read"])
async def test_qa_keeps_valid_claim_when_another_retrieval_fails(env, monkeypatch, operation):
    settings, task = env
    hit = MockKB(settings.mock_kb_path).search("农业", None, None, 1)[0]
    good_search = tool("kb_search", query="农业", area=None, fyp=None, top_k=1)
    failed_call = _fault_call(operation)
    class FaultAfterEvidenceKB(MockKB):
        def search(self, query, area, fyp, top_k, *, planids=None):
            if operation == "search" and query == "政策":
                time.sleep(.2)
            return super().search(query, area, fyp, top_k, planids=planids)

        def read(self, *args, **kwargs):
            if operation == "read":
                time.sleep(.2)
            return super().read(*args, **kwargs)

        def verify_quote(self, chunk_id, quote, observed_ranges, **kwargs):
            for document in self.docs:
                chunk = next((item for item in document["chunks"]
                              if item["chunk_id"] == chunk_id), None)
                if chunk is None:
                    continue
                for start, end in observed_ranges:
                    seen = document["body"][max(start, chunk["char_start"]):
                                            min(end, chunk["char_end"])]
                    if quote and quote in seen:
                        return True
            return False

    if operation == "search":
        failed_call = tool("kb_search", query="政策", area=None, fyp=None, top_k=8)
    payload = answer("保留已核验结论", claims=[{
        "text": "农业相关结论", "chunk_ids": [hit.chunk_id],
        "quotes": [hit.content[:20]],
    }], insufficient=True)
    script = [[good_search], [failed_call], payload]
    kb = FaultAfterEvidenceKB(settings.mock_kb_path)
    result, _ = await _run(env, monkeypatch, script, timeout=.05, kb=kb)

    assert result.status == "completed"
    assert result.error is None
    assert [claim.text for claim in result.claims] == ["农业相关结论"], result.model_dump_json(indent=2)
    assert [ref.chunk_id for ref in result.evidence_refs] == [hit.chunk_id]
    assert any(call["status"] == "timeout" for call in result.meta["tool_calls"])
    assert any("本次证据不足" in warning for warning in result.warnings)
    assert "部分检索失败；已保留有依据的结论，但结果可能不完整。" in result.warnings
    assert result.meta["retrieval_failure"]["timeout_count"] == 1
    assert result.meta["retrieval_failure"]["tool_names"] == [f"kb_{operation}"]


def test_latest_frozen_rag_qa_gets_scope_warning_without_result_changes(env):
    task, result = _completed_claim_result(env, "qa", "rag", "请介绍最新农业政策")
    before = (result.status, result.evidence_status, result.answer,
              list(result.claims), list(result.evidence_refs))

    finalized = finalize_qa_retrieval(result, task)

    assert (finalized.status, finalized.evidence_status, finalized.answer,
            finalized.claims, finalized.evidence_refs) == before
    assert "本次仅依据当前冻结知识库收录的政策，不能确认实时最新政策或现行有效性。" in finalized.warnings


@pytest.mark.parametrize("task_type,backend,question", [
    ("qa", "mock", "请介绍最新农业政策"),
    ("qa", "rag", "请介绍农业政策"),
    ("extract", "rag", "请介绍最新农业政策"),
])
def test_latest_scope_warning_is_limited_to_latest_rag_qa(env, task_type, backend, question):
    task, result = _completed_claim_result(env, task_type, backend, question)

    finalized = finalize_qa_retrieval(result, task)

    assert finalized.status == "completed"
    assert finalized.answer == result.answer
    assert "本次仅依据当前冻结知识库收录的政策，不能确认实时最新政策或现行有效性。" not in finalized.warnings
