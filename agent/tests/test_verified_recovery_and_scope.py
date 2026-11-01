"""QA last-mile: user-readable verified recovery and trusted plan-bound retrieval."""
import json
import time
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext

import guance_agent.orchestrator as orchestrator
from guance_agent.config import Settings
from guance_agent.context import RunContext
from guance_agent.output import finalize_recovered_qa_answer
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import Claim, EvidenceRef, RunResult, ToolResult
from guance_agent.tools import kb_read, kb_search


def _source():
    return EvidenceRef(
        planid="2561", chunk_id="chunk-1", title="沧州市第十个五年计划纲要",
        area="沧州市", fyp="第十个五年计划", admin_level=None, plantype_label=None,
        section_idx=1, section_path="经济结构调整", char_start=0, char_end=70,
        source="kb_read",
    )


def _result(recovery="complete_claims_only"):
    return RunResult(
        run_id="r", session_id="s", status="completed",
        evidence_status="insufficient",
        answer="本轮模型输出被截断，仅保留已完整生成的政策事实；其引文仍须通过逐字核验。",
        claims=[Claim(text="推进工业结构调整", chunk_ids=["chunk-1"],
                      quotes=["推进工业结构调整"])],
        evidence_refs=[_source()], dropped_chunk_ids=[], error=None,
        meta={"output_parse": {"classification": "incomplete_json",
                               "recovery": recovery}}, task_type="qa",
    )


def test_truncated_verified_claims_become_readable_partial_research():
    result = _result()
    finalize_recovered_qa_answer(result, "沧州市十五计划产业结构调整有哪些重点部署？")
    assert "推进工业结构调整" in result.answer
    assert "经济结构调整" in result.answer
    assert "planid=2561" in result.answer
    assert "仅保留已完整生成的政策事实" not in result.answer
    assert result.meta["answer_rebuilt"] == "truncated_verified_claims"
    assert result.structured["qa_recovery"] == {
        "source": "verified_claims", "status": "partial", "claim_count": 1,
        "source_planids": ["2561"], "unverified_analysis_included": False,
    }
    assert result.evidence_status == "insufficient"
    assert result.status == "completed"
    assert result.claims[0].quotes == ["推进工业结构调整"]


def test_ordinary_qa_and_unverified_truncation_are_not_rewritten():
    for mode in ("normal", "bad", "incomplete"):
        result = _result(recovery="other" if mode == "normal" else "complete_claims_only")
        if mode == "bad":
            result.evidence_refs = []
        elif mode == "incomplete":
            result.meta["verification_incomplete"] = True
        previous = result.answer
        finalize_recovered_qa_answer(result, "产业结构调整")
        assert result.answer == previous
        assert result.structured is None


def test_aggregated_recovered_objects_use_only_existing_evidence():
    result = _result(recovery="ignored")
    result.meta["output_parse"] = {
        "classification": "incomplete_json",
        "objects": [{"classification": "incomplete_json",
                     "recovery": "complete_claims_only"}],
    }
    finalize_recovered_qa_answer(result, "产业结构调整")
    assert "推进工业结构调整" in result.answer


class CapturingKB:
    def __init__(self):
        self.search_calls = []

    def search(self, query, area, fyp, top_k, *, planids=None):
        self.search_calls.append((query, area, fyp, top_k, planids))
        return []

    def read(self, *args, **kwargs):
        raise AssertionError("out-of-scope read must never reach the knowledge base")


async def _invoke(tool, ctx, **args):
    payload = json.dumps(args)
    raw = await tool.on_invoke_tool(
        ToolContext(context=ctx, tool_name=tool.name,
                    tool_call_id="scope-test", tool_arguments=payload), payload,
    )
    return ToolResult.model_validate_json(raw)


def _ctx(scope, verified=True):
    kb = CapturingKB()
    ctx = RunContext(Settings(), kb, "r", "s")
    ctx.scope = scope
    documents = [SimpleNamespace(
        planid=pid, title="沧州市第十个五年计划纲要",
        fyp="第十个五年计划", area="沧州市",
        source_verified=verified,
    ) for pid in (scope or ())]
    ctx.research = SimpleNamespace(plan=SimpleNamespace(documents=documents))
    return ctx


@pytest.mark.asyncio
async def test_verified_single_planid_overrides_mistaken_model_period_and_area():
    ctx = _ctx(("2561",))
    result = await _invoke(kb_search, ctx, query="产业结构调整",
                           area="北京市", fyp="十五五", top_k=5)
    assert result.status == "empty"
    assert ctx.kb.search_calls == [("产业结构调整", None, None, 5, ("2561",))]
    rejected = await _invoke(kb_read, ctx, planid="WRONG", section_idx=1)
    assert rejected.status == "invalid_argument"


@pytest.mark.asyncio
async def test_unverified_or_multiple_document_plans_preserve_requested_filters():
    for ctx in (_ctx(("2561",), verified=False), _ctx(("2561", "another"))):
        await _invoke(kb_search, ctx, query="产业结构调整",
                      area="北京市", fyp="十五五", top_k=5)
        assert ctx.kb.search_calls[0][1:3] == ("北京市", "第十五个五年规划")
    ctx = _ctx(None)
    await _invoke(kb_search, ctx, query="产业结构调整",
                  area="北京市", fyp="十五五", top_k=5)
    assert ctx.kb.search_calls[0][-1] is None


@pytest.mark.asyncio
async def test_qa_model_receives_only_trusted_planner_identity(monkeypatch, env):
    seen = {}

    class AgentStub:
        instructions = "保持证据核验"

        def clone(self, **kwargs):
            child = AgentStub()
            child.instructions = kwargs["instructions"]
            return child

    class ClientStub:
        def with_options(self, **kwargs):
            return self

    monkeypatch.setattr(orchestrator, "build_agent", lambda *_: AgentStub())
    monkeypatch.setattr(orchestrator, "chat_model", lambda *_: object())

    async def fake_execute(agent, task, ctx, rt, deadline):
        seen["instructions"] = agent.instructions
        return ExecOutcome(None, "MODEL_TIMEOUT")

    monkeypatch.setattr(orchestrator, "execute", fake_execute)
    ctx = _ctx(("2561",))
    rt = SimpleNamespace(client=ClientStub(), meta={})
    await orchestrator._execute_once(
        env[1], env[0], ctx, rt, time.monotonic() + 90,
    )
    assert "planid" in seen["instructions"]
    assert "2561" in seen["instructions"]
    assert "第十个五年计划" in seen["instructions"]
    assert "十五五" in seen["instructions"]  # explicit prohibition, not a new target
    assert rt.meta["qa_trusted_plan_scope"]["planid"] == "2561"
