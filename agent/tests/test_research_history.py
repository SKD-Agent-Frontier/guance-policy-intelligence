"""Historical summarize/analyze must reuse verified research, not re-retrieve."""
import json
import time
from types import SimpleNamespace

import pytest

from guance_agent.context import RunContext
from guance_agent.orchestrator import _run_historical_research, _summarize_historical
from guance_agent.qa_report_verification import finalize_report
from guance_agent.research_delivery import draft_verified_report
from guance_agent.research_intent import classify_intent
from guance_agent.schemas import Claim, EvidenceRef, RunResult, TaskRequest


def _ref(chunk_id="c1", planid="2561"):
    return EvidenceRef(
        planid=planid, chunk_id=chunk_id, title="沧州市十五计划", area="沧州市",
        fyp="第十个五年计划", admin_level="prefecture", plantype_label="规划纲要",
        section_idx=1, section_path="产业结构调整", char_start=0, char_end=20,
        chunk_char_start=0, chunk_char_end=20, source="kb_read",
    )


def _prior_turn():
    claim_a = Claim(text="农业结构调整包括推进农业科技进步。",
                    chunk_ids=["c1"], quotes=["推进农业科技进步"], dimension="农业")
    claim_b = Claim(text="工业结构调整包括启动高新技术产业。",
                    chunk_ids=["c1"], quotes=["强力启动高新技术产业"], dimension="工业")
    ref = _ref()
    return {
        "question": "沧州市“十五”计划中产业结构调整的主要措施有哪些？",
        "documents": [{"planid": "2561", "title": "沧州市十五计划",
                       "area": "沧州市", "fyp": "第十个五年计划"}],
        "verified_facts": [claim_a.model_dump(mode="json"), claim_b.model_dump(mode="json")],
        "evidence_refs": [ref.model_dump(mode="json")],
        "evidence_status": "verified",
        "quote_verification": {"checked": 2, "approved": 2, "rejected": 0, "unverified": 0},
    }


def _draft_payload():
    return json.dumps({
        "dimensions": ["产业结构调整"],
        "blocks": [
            {"id": "summary", "title_path": ["总体概括"], "dimension": "产业结构调整",
             "kind": "analysis", "text": "该规划从农业和工业两方面推进结构调整。",
             "supporting_claim_indexes": [0, 1]},
            {"id": "detail", "title_path": ["主要措施", "农业"], "dimension": "产业结构调整",
             "kind": "fact", "text": "农业结构调整包括推进农业科技进步。",
             "supporting_claim_indexes": [0]},
        ],
    }, ensure_ascii=False)


def _review_payload():
    return json.dumps({
        "facts": [{"index": 0, "supported": True}, {"index": 1, "supported": True}],
        "blocks": [{"id": "summary", "supported": True}, {"id": "detail", "supported": True}],
    })


@pytest.mark.asyncio
async def test_historical_summary_uses_previous_facts_and_semantic_review():
    responses = [_draft_payload(), _review_payload()]
    calls = []

    async def complete_text(messages, **kwargs):
        calls.append(messages)
        return responses.pop(0)

    rt = SimpleNamespace(
        complete_text=complete_text,
        hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(llm_request_timeout=30, max_turns=8),
        meta={},
    )
    ctx = RunContext(settings=SimpleNamespace(), kb=None, run_id="r", session_id="s")
    task = TaskRequest(run_id="r", session_id="s", created_at="2026-11-01T00:00:00Z",
                       question="总结以上内容。")
    prior = _prior_turn()
    intent = classify_intent(task.question, prior)
    assert intent.primary == "summarize" and intent.use_history

    result = await _run_historical_research(
        task, ctx, rt, time.monotonic() + 180, prior, intent)

    assert result is not None
    assert len(calls) == 2  # No unreviewed post-audit editorial model call
    assert result.meta["historical_reuse"] is True
    assert result.structured["schema_version"] == "qa-report/1"
    assert result.answer and "农业" in result.answer and "工业" in result.answer
    assert result.meta.get("historical_summary_generated") is None
    assert "该规划从农业和工业两方面推进结构调整。" in result.answer
    assert "研究完成情况" not in result.answer
    assert result.meta["research_delivery"]["audited_analysis"] is True
    assert {ref.planid for ref in result.evidence_refs} == {"2561"}


@pytest.mark.asyncio
async def test_forced_organization_report_is_structured_and_user_facing():
    ref = _ref()
    claims = [
        Claim(text="农业结构调整包括推进农业科技进步。", chunk_ids=["c1"],
              quotes=["推进农业科技进步"]),
        Claim(text="工业结构调整包括启动高新技术产业。", chunk_ids=["c1"],
              quotes=["强力启动高新技术产业"]),
    ]
    result = RunResult(
        run_id="r", session_id="s", status="completed", evidence_status="verified",
        answer="", claims=claims, evidence_refs=[ref], dropped_chunk_ids=[],
        error=None, meta={"quote_verification": {"checked": 2, "approved": 2,
                                                  "rejected": 0, "unverified": 0}},
        task_type="qa",
    )
    responses = [_draft_payload(), _review_payload()]

    async def complete_text(messages, **kwargs):
        return responses.pop(0)

    rt = SimpleNamespace(complete_text=complete_text,
                         hooks=SimpleNamespace(turns=0),
                         settings=SimpleNamespace(llm_request_timeout=30, max_turns=8),
                         meta={})
    ctx = RunContext(settings=SimpleNamespace(), kb=None, run_id="r", session_id="s")
    ctx.evidence.register(ref)
    assert await draft_verified_report(
        result, "沧州市“十五”计划中产业结构调整的主要措施有哪些？",
        rt, time.monotonic() + 180, force=True, intent="organize")
    result = await finalize_report(result, ctx, rt, time.monotonic() + 180)
    assert result.structured["schema_version"] == "qa-report/1"
    assert result.structured["verification"]["facts"] == "semantic_reviewed_against_quotes"
    assert "农业" in result.answer and "工业" in result.answer
    assert "研究完成情况" not in result.answer
    assert "是否足以回应全部研究要求" not in result.answer


@pytest.mark.asyncio
async def test_proposed_summary_is_never_published_without_report_review():
    """A new model summary must stay a draft even if it invents a conclusion."""
    unsafe_text = "没有依据的跨地区因果分析"
    async def complete_text(messages, **kwargs):
        return unsafe_text
    rt = SimpleNamespace(
        complete_text=complete_text, hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=8, llm_request_timeout=30),
    )
    prior = _prior_turn()
    ctx = RunContext(settings=SimpleNamespace(), kb=None, run_id="r", session_id="s")
    task = TaskRequest(run_id="r", session_id="s",
                       created_at="2026-11-01T00:00:00Z",
                       question="比较这两份政策的共同点")
    from guance_agent.research_history import build_historical_state
    result, _ = build_historical_state(ctx, task, prior)
    result.answer = "已经核验的事实"
    before = result.answer
    assert await _summarize_historical(
        result, task, rt, time.monotonic() + 180, compare=True, prepend=True)
    assert result.answer == before
    assert result.meta["_research_summary_text"] == unsafe_text
    assert result.meta.get("historical_summary_generated") is None
