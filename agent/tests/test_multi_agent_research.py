"""Offline multi-agent safety tests; do not confuse these with real Qwen evaluation."""
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from guance_agent.config import Settings
from guance_agent.multi_agent import critic, lead, scheduler
from guance_agent.multi_agent.contracts import Assignment, Synthesis, WorkerOutcome
from guance_agent.multi_agent.workspace import EvidenceLedger, Workspace
from guance_agent.schemas import Claim, EvidenceRef


def assignment(name="W1"):
    return Assignment(
        id=name, focus="研究政策", requirement_ids=("R1",),
        original_question="分析数字经济政策", targets=("TEST-001",),
    )


def verified_result(version="v1", good=True):
    claim = Claim(
        text="完善农业灌溉设施", quotes=["完善农业灌溉设施"],
        chunk_ids=["TEST-001__s01__p00"], fact_ids=["f1"],
    )
    reference = EvidenceRef(
        planid="TEST-001", chunk_id="TEST-001__s01__p00",
        title="甲市农业发展规划", area="甲市", fyp=None,
        admin_level=None, plantype_label=None, section_idx=1,
        section_path="农业发展", char_start=0, char_end=22,
        document_version_id=version, source="kb_read",
        observed_ranges=[(0, 22)],
    )
    return SimpleNamespace(
        status="completed", evidence_status="verified" if good else "insufficient",
        meta={"research_quality": {"status": "complete" if good else "partial"}},
        structured={"blocks": [
            {"id": "f", "kind": "fact", "title_path": ["农业设施"],
             "text": "完善农业灌溉设施", "supporting_claim_indexes": [0]},
            {"id": "a", "kind": "analysis", "title_path": ["政策方向"],
             "text": "农业基础设施建设得到政策支持。",
             "supporting_claim_indexes": [0]},
        ]},
        claims=[claim], evidence_refs=[reference],
    )


def test_engine_opt_in_only():
    assert Settings().qa_engine == "controlled"
    assert Settings(qa_engine="multi").qa_engine == "multi"


def test_ledger_accepts_only_verified_claims_and_approved_blocks():
    ledger = EvidenceLedger()
    assert ledger.ingest(WorkerOutcome(assignment(), "completed", verified_result())) == 2
    assert len(ledger.claims) == 1
    assert len(ledger.refs) == 1
    assert ledger.findings[1].kind == "analysis"
    assert ledger.facts()[0]["planids"] == ["TEST-001"]
    assert ledger.finding_inputs()[1]["fact_ids"] == ["F1"]
    assert ledger.ingest(
        WorkerOutcome(assignment("W2"), "completed", verified_result(good=False))
    ) == 0


def test_ledger_refuses_conflicting_document_generation():
    ledger = EvidenceLedger()
    ledger.ingest(WorkerOutcome(assignment(), "completed", verified_result("v1")))
    assert ledger.ingest(WorkerOutcome(
        assignment("W2"), "completed", verified_result("v2")
    )) == 0
    assert ledger.conflicts == 2
    assert len(ledger.refs) == 1


def test_ledger_refuses_unbound_indexes_and_bool_index():
    r = verified_result()
    r.structured["blocks"] = [
        {"id": "x", "kind": "fact", "title_path": ["失效"], "text": "xxx",
         "supporting_claim_indexes": [4]},
        {"id": "y", "kind": "fact", "title_path": ["失效"], "text": "xxx",
         "supporting_claim_indexes": [True]},
    ]
    ledger = EvidenceLedger()
    assert ledger.ingest(WorkerOutcome(assignment(), "completed", r)) == 0
    assert ledger.rejected_blocks == 2
    assert ledger.claims == []


def test_synthesis_with_unknown_number_is_never_approved():
    facts = {"F1": {"text": "农业设施提升5%", "quotes": ["提升5%"]}}
    yes = Synthesis("S1", "农业", "提升5%", ("F1",))
    no = Synthesis("S2", "农业", "提升90%", ("F1",))
    assert critic.deterministic_filter([yes, no], facts) == [yes]


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.meta = {"model": "fake"}
        self.finished = None

    async def complete_text(self, messages, *, max_tokens, timeout, purpose):
        self.calls.append(purpose)
        if purpose == "multi_lead_plan":
            return '{"groups":[["R1"],["R2"]]}'
        if purpose == "multi_lead_synthesis":
            return json.dumps({"blocks": [{
                "title": "政策研究", "text": "农业灌溉设施获得支持。",
                "fact_ids": ["F1"],
            }]}, ensure_ascii=False)
        if purpose == "multi_critic_review":
            return '{"approved_ids":["S1"],"gaps":[],"comments":"supported"}'
        raise AssertionError(purpose)

    def finish(self, result, ctx):
        self.finished = result

    def save_trusted_turn(self, turn):
        pass


@pytest.mark.asyncio
async def test_lead_plan_covers_all_requirements():
    rt = FakeRuntime()
    plan = await lead.build_plan(
        "研究数字经济政策；比较江苏和浙江的重点", None, rt,
        time.monotonic() + 90,
    )
    assert len(plan.assignments) == 2
    ids = [rid for a in plan.assignments for rid in a.requirement_ids]
    assert sorted(ids) == [f"R{i + 1}" for i in range(len(plan.requirements))]
    assert all(a.original_question.startswith("研究数字经济") for a in plan.assignments)


@pytest.mark.asyncio
async def test_critic_requires_source_anchored_approval():
    rt = FakeRuntime()
    res = await critic.review(
        [Synthesis("S1", "农业", "发展农业灌溉设施", ("F1",))],
        {"F1": {"text": "农业灌溉设施", "quotes": ["完善农业灌溉设施"]}},
        rt, time.monotonic() + 90,
    )
    assert res.performed and res.approved_ids == ("S1",)


@pytest.mark.asyncio
async def test_multi_scheduler_end_to_end_without_real_endpoint(env, monkeypatch, tmp_path):
    s, task = env
    s = replace(s, qa_engine="multi", run_timeout=110, llm_api_key="offline-test",
                llm_base_url="https://fake.invalid/v1", run_log=tmp_path / "runs.jsonl")
    rt = FakeRuntime()

    async def fake_worker(a, _task, _settings, *, seconds, discoverer=None):
        assert seconds > 2
        return WorkerOutcome(a, "completed", result=verified_result())

    monkeypatch.setattr(scheduler.worker, "run_worker", fake_worker)
    monkeypatch.setattr(scheduler, "get_kb", lambda _: object())
    monkeypatch.setattr(scheduler, "build_trusted_turn", lambda *_: {"verified": True})
    monkeypatch.setattr(scheduler, "RunContext", lambda *args, **kwargs: SimpleNamespace(
        evidence=SimpleNamespace(register=lambda _: None), tool_calls=[],
    ))
    result = await scheduler.run_multi_task(task, s, rt)
    assert result.status == "completed" and result.evidence_status == "verified"
    assert result.meta["research_engine"] == "multi"
    assert len(result.claims) == 1 and len(result.evidence_refs) == 1
    assert "完善农业灌溉设施" in result.answer
    assert result.meta["multi_agent"]["sources"] == 1
    assert result.meta["multi_agent"]["critic"]["performed"]
    assert rt.finished is result
    assert (tmp_path / "multi-agent-traces" / (task.run_id + ".json")).exists()


@pytest.mark.asyncio
async def test_multi_scheduler_fails_closed_without_verified_evidence(env, monkeypatch):
    s, task = env
    s = replace(s, qa_engine="multi", run_timeout=100, llm_api_key="offline-test",
                llm_base_url="https://fake.invalid/v1")
    rt = FakeRuntime()

    async def no_evidence(a, *_args, **_kwargs):
        return WorkerOutcome(a, "timeout")

    monkeypatch.setattr(scheduler.worker, "run_worker", no_evidence)
    result = await scheduler.run_multi_task(task, s, rt)
    assert result.evidence_status == "none"
    assert result.claims == []
    assert result.meta["research_completion"]["status"] == "partial"
    assert rt.finished is result


def test_workspace_does_not_write_original_question(tmp_path):
    question = "私有政策研究问题与号码12345"
    workspace = Workspace("run_1", tmp_path, question)
    workspace.record("worker_returned", worker="W1", status="completed", facts=2)
    raw = workspace.path.read_text(encoding="utf-8")
    assert question not in raw and "私有政策" not in raw
    assert json.loads(raw)["events"][0]["facts"] == 2
