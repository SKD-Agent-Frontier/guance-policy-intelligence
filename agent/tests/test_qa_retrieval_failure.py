"""PR13 diagnosis: QA must expose retrieval faults as run failures."""
from dataclasses import replace

import pytest

from guance_agent.context import RunContext
from guance_agent.kb import MockKB
from guance_agent.orchestrator import run_task
from guance_agent.output import build_result, finalize_qa_retrieval
from guance_agent.runner import ExecOutcome
from guance_agent.runtime import Runtime
from guance_agent.schemas import AgentOutput, Claim

from .fake_model import FakeModel, tool


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


async def _run(env, monkeypatch, kb, script, targets=None):
    from .fake_model import install_audit

    settings, task = env
    settings = replace(settings, llm_base_url="https://example.invalid/v1",
                       llm_api_key="offline-only", tool_timeout=.15)
    task = task.model_copy(update={"question": "农业", "targets": targets})
    model = FakeModel(script)
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda _: kb)
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda *_: model)
    install_audit(monkeypatch)
    return await run_task(task, settings, Runtime(settings, task)), model


def _empty_draft():
    return [tool("submit_draft", evidence_candidates=[], blocks=[], finish_if_partial=True)]


@pytest.mark.parametrize("operation", ["search", "read"])
@pytest.mark.parametrize("fault,code", [("timeout", "RAG_TIMEOUT"), ("backend_error", "RAG_BACKEND_ERROR")])
async def test_qa_retrieval_fault_without_claims_fails(env, monkeypatch, operation, fault, code):
    from .fake_model import sdk_search

    kb = MockKB(env[0].mock_kb_path)
    def broken(*args, **kwargs):
        if fault == "timeout":
            raise TimeoutError
        raise RuntimeError("offline fault")
    monkeypatch.setattr(kb, operation, broken)
    script = [[sdk_search()]]
    if operation == "read":
        script.append([tool("read_batch", items=[{"planid": "TEST-001", "section_idx": 0}])])
    result, model = await _run(env, monkeypatch, kb, [*script, _empty_draft()], ["TEST-001"])
    assert model.calls == len(script) + 1 and not result.claims and not result.evidence_refs
    assert result.status == "failed" and result.error.code == code
    assert "未检索到" not in result.answer
    assert result.meta["retrieval_failure"] == {
        "code": code, "timeout_count": int(fault == "timeout"),
        "backend_error_count": int(fault == "backend_error"), "tool_names": [f"kb_{operation}"],
    }


@pytest.mark.parametrize("operation,planid,expected", [
    ("search", "missing", "empty"), ("read", "TEST-003", "no_text"), ("read", "missing", "not_found"),
])
async def test_qa_expected_no_match_statuses_remain_completed(env, monkeypatch, operation, planid, expected):
    from .fake_model import sdk_search

    kb = MockKB(env[0].mock_kb_path)
    script = [[sdk_search()]]
    if operation == "read":
        script.append([tool("read_batch", items=[{"planid": planid}])])
    result, _ = await _run(env, monkeypatch, kb, [*script, _empty_draft()], [planid])
    assert result.status == "completed" and result.error is None
    assert any(c["status"] == expected for c in result.meta["tool_calls"])
    assert not result.claims and not result.evidence_refs


async def test_extract_retrieval_timeout_preserves_gap_reason(env, monkeypatch):
    from .business_sdk_fakes import install_business_sdk

    settings, task = env
    settings = replace(settings, llm_base_url="https://example.invalid/v1", llm_api_key="offline")
    kb = MockKB(settings.mock_kb_path)
    def timed_out(*args, **kwargs):
        raise TimeoutError
    monkeypatch.setattr(kb, "search", timed_out)
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda _: kb)
    install_business_sdk(monkeypatch)
    task = task.model_copy(update={"task_type": "extract"})
    result = await run_task(task, settings, Runtime(settings, task))
    assert result.task_type == "extract" and result.status == "completed" and not result.error
    assert result.meta["tool_calls"][0]["status"] == "timeout"
    assert all(m["reason"] == "retrieval_unavailable" for m in result.structured["missing"])


@pytest.mark.parametrize("operation", ["search", "read"])
async def test_qa_keeps_valid_claim_when_another_retrieval_fails(env, monkeypatch, operation):
    from .fake_model import fact_submission, sdk_read, sdk_search

    kb = MockKB(env[0].mock_kb_path)
    hit = kb.search("农业", None, None, 1)[0]
    original = getattr(kb, operation)
    attempts = 0
    def fail_second(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise TimeoutError
        return original(*args, **kwargs)
    monkeypatch.setattr(kb, operation, fail_second)
    fault = sdk_search("政策") if operation == "search" else sdk_read(hit)
    result, _ = await _run(env, monkeypatch, kb,
                           [[sdk_search()], [sdk_read(hit)], [fault], fact_submission(hit)])
    assert result.status == "completed" and not result.error
    assert [claim.text for claim in result.claims] == [hit.content[:24].strip()]
    assert [ref.chunk_id for ref in result.evidence_refs] == [hit.chunk_id]
    assert any("部分检索失败" in msg for msg in result.warnings)
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
