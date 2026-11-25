"""SDK 问答生产路径和兼容格式恢复的离线回归。"""
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from agents.exceptions import ModelBehaviorError

from guance_agent.kb import MockKB
from guance_agent.orchestrator import _recover_qa_output, run_task
from guance_agent.runner import ExecOutcome
from guance_agent.runtime import Runtime

from .fake_model import FakeModel, tool
from .sdk_fact_fakes import FACT, SOURCE, install_fact_model


def _configured(env, **changes):
    return replace(env[0], llm_base_url="https://example.invalid/v1",
                   llm_api_key="offline-key", **changes)


def _stub_model(monkeypatch, script):
    model = FakeModel(script)
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda *_args: model)
    return model


async def test_search_metadata_cannot_be_published_as_qa_facts(env, monkeypatch):
    s, task = _configured(env, max_turns=3), env[1]
    hits = MockKB(s.mock_kb_path).search("农业", "甲市", None, 2)
    assert len(hits) == 2
    candidate = "、".join(f"《{hit.title}》（{hit.planid}）" for hit in hits)
    model = _stub_model(monkeypatch, [
        [tool("search", query="农业", top_k=2, requirements=["农业相关文献"])],
        candidate,
    ])
    result = await run_task(task, s, Runtime(s, task))
    assert [call["name"] for call in result.meta["tool_calls"] if call["name"].startswith("kb_")] == ["kb_search"]
    assert model.calls == s.max_turns
    assert not result.claims and not result.evidence_refs
    assert result.evidence_status != "verified"
    assert candidate not in result.answer


@pytest.mark.parametrize(("reply", "expected_classification"), [
    ('{"answer":"已恢复","claims":[],"insufficient":true}', "ok"),
    ('{"answer":"还是坏的"', "incomplete_json"),
])
async def test_legacy_format_repair_uses_only_one_model_call(
        env, monkeypatch, reply, expected_classification):
    s, task = _configured(env), env[1]
    rt = Runtime(s, task)
    complete = AsyncMock(return_value=reply)
    monkeypatch.setattr(rt, "complete_text", complete)
    original = ExecOutcome('{"answer":"已恢复", "claims": }', None)
    budget = {"remaining": 1}
    outcome, meta = await _recover_qa_output(original, s, rt, time.monotonic() + 60,
                                             repair_budget=budget)
    assert meta["output_parse"]["repair"] == "attempted"
    assert meta["output_parse"]["repair_result"] == expected_classification
    if expected_classification == "ok":
        assert outcome.final_output.answer == "已恢复"
    else:
        assert outcome is original
    second, skipped = await _recover_qa_output(original, s, rt, time.monotonic() + 60,
                                               repair_budget=budget)
    assert second is original
    assert skipped["output_parse"]["repair"] == "skipped_budget"
    complete.assert_awaited_once()
    assert rt.hooks.calls == 0


async def test_sdk_model_protocol_failure_closes_without_repair_or_facts(env, monkeypatch):
    s, task = _configured(env), env[1]
    model = _stub_model(monkeypatch, [ModelBehaviorError("bad tool arguments")])
    rt = Runtime(s, task)
    complete = AsyncMock()
    monkeypatch.setattr(rt, "complete_text", complete)
    result = await run_task(task, s, rt)
    assert result.meta["research_core"]["protocol_error_type"] == "ModelBehaviorError"
    assert model.calls == 1
    complete.assert_not_awaited()
    assert not result.claims and not result.evidence_refs
    assert result.evidence_status != "verified"


async def test_objects_share_a_single_search_and_model_call_budget(env, monkeypatch):
    s = _configured(env, max_tool_calls=2, max_turns=2)
    task = env[1].model_copy(update={"question": "农业重点", "targets": ["TEST-001", "TEST-002"]})
    model = _stub_model(monkeypatch, [
        [tool("search", query="农业", requirements=["农业重点"])],
        [tool("read_batch", items=[{"planid": "TEST-001", "chunk_id": SOURCE}])],
    ])
    result = await run_task(task, s, Runtime(s, task))
    assert model.calls <= s.max_turns
    assert [call["name"] for call in result.meta["tool_calls"] if call["name"].startswith("kb_")] == ["kb_search", "kb_search"]
    assert result.meta["research_core"]["remaining"]["search_read"] == 0
    assert not result.evidence_refs and not result.claims
    assert [obj["planid"] for obj in result.route["objects"]] == task.targets


async def test_each_requested_object_keeps_its_own_verified_result(env, monkeypatch):
    s = _configured(env)
    task = env[1].model_copy(update={"question": "农业重点", "targets": ["TEST-001", "TEST-002"]})
    install_fact_model(monkeypatch, s, borrowed_quote=True)
    result = await run_task(task, s, Runtime(s, task))
    assert [claim.text for claim in result.claims] == [FACT]
    first, second = result.route["objects"]
    assert (first["planid"], first["status"], first["evidence_ids"]) == (
        "TEST-001", "ok", [SOURCE])
    assert (second["planid"], second["status"], second["evidence_ids"]) == (
        "TEST-002", "insufficient", [])
    assert all(ref.source == "kb_read" and ref.observed_ranges for ref in result.evidence_refs)
