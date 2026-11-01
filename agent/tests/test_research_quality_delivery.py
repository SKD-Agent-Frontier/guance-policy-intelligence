"""Research-intent evaluation, audited report drafting, and bounded gap repair."""
import json
import time
from types import SimpleNamespace

import pytest

import guance_agent.research_supplement as supplement
from guance_agent.research_delivery import deliver_research, draft_verified_report
from guance_agent.research_quality import evaluate, followup_gap, requirements
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import Claim, EvidenceRef, RunResult


def _ref(cid="chunk-1"):
    return EvidenceRef(
        planid="2561", chunk_id=cid, title="某地规划纲要", area="某市",
        fyp="十四五", admin_level=None, plantype_label=None,
        section_idx=1, section_path="发展方向", char_start=0, char_end=50,
        source="kb_read",
    )


def _result():
    return RunResult(
        run_id="run", session_id="session", status="completed",
        evidence_status="verified", answer="模型未经审核的分析不得直接发表",
        claims=[Claim(text="政策提出建设基础设施", chunk_ids=["chunk-1"],
                      quotes=["建设基础设施"]),
                Claim(text="政策提出人才培养措施", chunk_ids=["chunk-2"],
                      quotes=["人才培养"])],
        evidence_refs=[_ref("chunk-1"), _ref("chunk-2")],
        dropped_chunk_ids=[], error=None, meta={"quote_verification": {"unverified": 0}},
        task_type="qa",
    )


def test_user_requirements_are_not_satisfied_by_model_declarations():
    result = _result()
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["政策目标"],
        "blocks": [{"dimension": "政策目标", "title_path": ["目标"],
                    "kind": "fact", "text": "基础设施建设",
                    "verification": "model_reviewed_evidence_linked"}],
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    quality = evaluate(result, "分析当地政策优势及问题挑战")
    assert quality["status"] == "partial"
    assert quality["missing_dimensions"] == ["政策优势", "问题挑战"]
    assert "audited_analysis_missing" in quality["gaps"]
    assert requirements("分析当地政策优势及问题挑战")["needs_report"]


def test_audited_analysis_can_satisfy_explicit_dimension():
    result = _result()
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["政策优势"],
        "blocks": [{"dimension": "政策优势", "title_path": ["政策优势"],
                    "kind": "analysis", "text": "两类支持措施具有互补作用",
                    "verification": "model_reviewed_evidence_linked",
                    "supporting_chunk_ids": ["chunk-1", "chunk-2"]}],
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    assert evaluate(result, "分析政策优势")["status"] == "complete"
    deliver_research(result, "分析政策优势")
    assert "## 研究问题" in result.answer
    assert "## 参考文献" in result.answer
    assert "planid=2561" in result.answer
    assert result.meta["research_delivery"]["audited_analysis"]


def test_missing_semantic_review_never_publishes_unreviewed_prose():
    result = _result()
    deliver_research(result, "分析政策优势及问题挑战")
    assert "模型未经审核" not in result.answer
    assert "政策提出建设基础设施" in result.answer
    assert "非完整研究结论" in result.answer
    assert result.meta["research_quality"]["status"] == "partial"
    assert result.evidence_status == "insufficient"


@pytest.mark.asyncio
async def test_report_synthesis_fails_closed_when_insufficient_time():
    result = _result()
    called = []

    async def complete_text(*args, **kwargs):
        called.append(1)
        raise AssertionError("no budget for another model call")

    runtime = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
        complete_text=complete_text,
    )
    assert not await draft_verified_report(
        result, "分析政策优势", runtime, time.monotonic() + 40)
    assert not called
    assert not result._qa_report_supplied


@pytest.mark.asyncio
async def test_model_report_is_only_pending_until_semantic_audit():
    result = _result()

    async def complete_text(*args, **kwargs):
        return json.dumps({
            "dimensions": ["政策优势"],
            "blocks": [{"id": "b1", "title_path": ["已知事实"],
                        "dimension": "政策优势", "kind": "fact",
                        "text": "政策涉及基础设施建设",
                        "supporting_claim_indexes": [0]}],
        })

    runtime = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=40),
        complete_text=complete_text,
    )
    assert await draft_verified_report(
        result, "分析政策优势", runtime, time.monotonic() + 120)
    assert result._qa_report_supplied
    assert len(result._qa_report_candidates) == 1
    assert result.meta["research_report_draft"] == "pending_semantic_review"
    assert "qa-report" not in str(result.structured)
    assert result.answer == "模型未经审核的分析不得直接发表"


@pytest.mark.asyncio
async def test_gap_followup_respects_budget_and_original_scope():
    result = _result()
    called = []

    async def never_execute(*args):
        called.append(1)

    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0))
    task = SimpleNamespace(task_type="qa", question="分析政策优势",
                           model_copy=lambda update: SimpleNamespace(
                               task_type="qa", question=update["question"]))
    s = SimpleNamespace(max_turns=8, max_input_chars=4000)
    ctx = SimpleNamespace(route=SimpleNamespace(kind="open"))
    await supplement.supplement_one(result, task, s, ctx, rt,
                                    time.monotonic() + 90, never_execute, None)
    assert not called
    assert result.meta["research_supplement"]["status"] == "skipped_budget"
    assert followup_gap(result, task.question) == "政策优势"


@pytest.mark.asyncio
async def test_followup_adds_only_fresh_verified_facts(monkeypatch):
    result = _result()
    part = _result()
    part.claims = [Claim(text="新增有效措施", chunk_ids=["chunk-3"],
                         quotes=["新增有效措施"])]
    part.evidence_refs = [_ref("chunk-3")]
    calls = []

    async def execute_once(task, s, ctx, rt, deadline):
        calls.append(task.question)
        return ExecOutcome("{}", None)

    async def recover(outcome, *_args, **_kwargs):
        return outcome, None

    async def noop(*_args):
        return None

    monkeypatch.setattr(supplement, "build_result", lambda *_args: part)
    monkeypatch.setattr(supplement, "verify_quotes", noop)
    monkeypatch.setattr(supplement, "verify_numbers", noop)
    monkeypatch.setattr(supplement, "enforce_literal_result", lambda *_args: None)
    task = SimpleNamespace(task_type="qa", question="分析政策优势",
                           model_copy=lambda update: SimpleNamespace(
                               task_type="qa", question=update["question"]))
    s = SimpleNamespace(max_turns=8, max_input_chars=4000)
    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0), meta={})
    ctx = SimpleNamespace(route=SimpleNamespace(kind="open"))
    await supplement.supplement_one(result, task, s, ctx, rt,
                                    time.monotonic() + 120, execute_once, recover)
    assert len(calls) == 1
    assert "政策优势" in calls[0]
    assert "新增有效措施" in [c.text for c in result.claims]
    assert "chunk-3" in {r.chunk_id for r in result.evidence_refs}
    assert result.meta["research_supplement"]["added_claims"] == 1


def test_simple_single_facet_qa_credits_verified_facts_without_a_report():
    result = _result()
    question = "这些政策的主要措施有哪些？"
    quality = evaluate(result, question)
    assert quality["needs_report"] is False
    assert quality["missing_dimensions"] == []
    assert quality["factual_answer_coverage"] is True
    assert quality["status"] == "complete"


@pytest.mark.asyncio
async def test_real_rag_supplement_uses_fresh_bounded_reads_not_legacy_agent(monkeypatch):
    result = _result()
    part = _result()
    part.claims = [Claim(text="新检索的可引用措施", chunk_ids=["fresh"],
                         quotes=["新检索的可引用措施"])]
    part.evidence_refs = [_ref("fresh")]
    seen = []

    async def controlled(task, settings, ctx, rt, deadline, *, focus, exclude_chunks):
        seen.append((focus, exclude_chunks, task.question))
        rt.meta["qa_controlled_retrieval"] = {"read_chunks": ["fresh"]}
        return ExecOutcome("{}", None)

    async def should_not_run(*args):
        raise AssertionError("Old agent tool loop must not run for real-RAG supplement")

    async def recover(outcome, *args, **kwargs):
        return outcome, None

    async def noop(*args):
        return None

    monkeypatch.setattr(supplement, "execute_controlled", controlled)
    monkeypatch.setattr(supplement, "build_result", lambda *args: part)
    monkeypatch.setattr(supplement, "verify_quotes", noop)
    monkeypatch.setattr(supplement, "verify_numbers", noop)
    monkeypatch.setattr(supplement, "enforce_literal_result", lambda *args: None)
    monkeypatch.setattr(supplement, "finalize_controlled_facts", lambda *args: None)

    original_question = "分析政策优势"
    task = SimpleNamespace(task_type="qa", question=original_question,
                           model_copy=lambda update: SimpleNamespace(
                               task_type="qa", question=update["question"]))
    ctx = SimpleNamespace(route=SimpleNamespace(kind="open"), scope=None)
    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0),
                         meta={"qa_execution_mode": "bounded_retrieval"})
    s = SimpleNamespace(kb_backend="rag", max_turns=8, max_input_chars=4000)
    await supplement.supplement_one(
        result, task, s, ctx, rt, time.monotonic() + 160,
        should_not_run, recover,
    )
    assert seen == [("政策优势", frozenset({"chunk-1", "chunk-2"}), original_question)]
    assert result.meta["research_supplement"]["status"] == "verified_facts_added"
    assert [c.text for c in result.claims][-1] == "新检索的可引用措施"
    assert ctx.scope is None
