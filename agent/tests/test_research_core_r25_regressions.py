import json
from types import SimpleNamespace

import pytest

from guance_agent.orchestrator import _rebuild_research_core_answer
from guance_agent.research_core import (
    _annotate_read_text,
    _quote_handles_for_passage,
    build_research_core_agent,
    execute_research_core,
)
from guance_agent.schemas import ChunkSpan, EvidenceRef, Passage, RunError

from .fake_model import FakeModel, tool
from .test_research_core import _make_state, _seed_read_fact


def _seed_two_sentence_passage(state):
    quotes = ["完善农业灌溉设施。", "推动绿色农业科技成果转化。"]
    chunk_ids = ["TEST-001__s01__p00", "TEST-001__s01__p01"]
    text = "".join(quotes)
    spans = []
    cursor = 0
    for chunk_id, quote in zip(chunk_ids, quotes, strict=True):
        end = cursor + len(quote)
        state.run.evidence.register(EvidenceRef(
            planid="TEST-001", chunk_id=chunk_id, title="农业发展规划", area="甲市",
            fyp=None, admin_level=None, plantype_label=None, section_idx=1,
            section_path="农业发展", char_start=cursor, char_end=end,
            chunk_char_start=cursor, chunk_char_end=end, source="kb_read",
            observed_ranges=[(cursor, end)], complete=True,
        ))
        state.read_text[chunk_id] = [quote]
        spans.append(ChunkSpan(chunk_id=chunk_id, char_start=cursor, char_end=end,
                                complete=True))
        cursor = end
    passage = Passage(
        planid="TEST-001", title="农业发展规划", area="甲市", fyp=None,
        admin_level=None, plantype_label=None, section_idx=1, section_path="农业发展",
        content=text, char_start=0, char_end=len(text), chunks=spans,
    )
    return quotes, chunk_ids, passage


@pytest.mark.asyncio
async def test_requirement_mapping_is_per_requirement_and_accepts_compact_read_handles(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业发展事实。"
    run, runtime, state = _make_state(settings, task, question)
    state.freeze_requirements(["农业灌溉事实", "绿色农业科技事实"])
    quotes, chunk_ids, passage = _seed_two_sentence_passage(state)
    handles = _quote_handles_for_passage(state, passage)
    handles_by_text = {
        state.evidence_handles[item["handle"]][1]: item["handle"] for item in handles
    }
    assert set(handles_by_text) == set(quotes)
    assert all(f"⟦{handle}⟧" in _annotate_read_text(passage.content, handles)
               for handle in handles_by_text.values())
    root_id, irrigation_id, green_id = [item.id for item in state.requirements]

    async def audit(current, mapped):
        assert [item["claim"] for item in mapped[irrigation_id]] == [quotes[0]]
        assert [item["claim"] for item in mapped[green_id]] == [quotes[1]]
        assert {item["source"][0]["chunk_id"] for item in mapped[root_id]} == set(chunk_ids)
        return json.dumps({
            "passed": True,
            "covered_requirement_ids": [item.id for item in current.requirements],
            "approved_block_ids": ["combined-facts"],
            "gaps": [], "unsupported_claims": [],
        }, ensure_ascii=False)

    model = FakeModel([[
        tool("submit_draft", evidence_candidates=[
            {"candidate_id": "irrigation", "claim": quotes[0],
             "evidence_handle": handles_by_text[quotes[0]],
             "requirement_ids": [root_id, irrigation_id]},
            {"candidate_id": "green", "claim": quotes[1],
             "evidence_handle": handles_by_text[quotes[1]],
             "requirement_ids": [root_id, green_id]},
        ], blocks=[{
            "id": "combined-facts", "title": "农业发展", "kind": "fact", "text": "",
            "requirement_ids": [root_id, irrigation_id, green_id],
            "evidence_ids": ["irrigation", "green"],
        }]),
    ]])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime, audit=audit), state, runtime)

    assert state.check_result["passed"] is True
    assert all(item.status == "delivered" for item in state.requirements)
    assert all(quote in answer for quote in quotes)
    status = state.status_for_model()
    assert all(chunk_id not in status for chunk_id in chunk_ids)
    assert '"quote"' not in status


@pytest.mark.asyncio
async def test_repeated_unchanged_submit_stops_without_partial_flag(env):
    settings, task = env
    question = "请查询planid TEST-001中的农业灌溉设施事实。"
    _run, runtime, state = _make_state(settings, task, question)
    state.freeze_requirements(["农业灌溉设施事实"])
    quote = "完善农业灌溉设施"
    chunk_id = _seed_read_fact(state, quote)
    requirement_ids = [item.id for item in state.requirements]
    submission = tool("submit_draft", evidence_candidates=[{
        "candidate_id": "irrigation", "claim": quote, "quote": quote,
        "chunk_id": chunk_id, "requirement_ids": requirement_ids,
    }], blocks=[{
        "id": "irrigation", "title": "灌溉设施", "kind": "fact", "text": "",
        "requirement_ids": requirement_ids, "evidence_ids": ["irrigation"],
    }])

    async def audit(_current, _mapped):
        return json.dumps({"passed": False, "covered_requirement_ids": [],
                           "approved_block_ids": [], "gaps": ["still incomplete"],
                           "unsupported_claims": []})

    model = FakeModel([[submission], [submission]])
    answer = await execute_research_core(
        build_research_core_agent(model, settings, runtime, audit=audit), state, runtime)

    assert model.calls == 2
    assert state.partial_finalized is True
    assert state.no_progress_submissions == 1
    assert quote in answer
    events = [item for item in state.action_log if item["action"] == "submit_draft_result"]
    assert len(events) == 2
    assert all(set(item) <= {"action", "kind", "candidate_count", "accepted_candidate_count",
                             "block_count", "accepted_block_count", "block_error_count",
                             "error_codes"} for item in events)
    serialized = json.dumps(events, ensure_ascii=False)
    assert chunk_id not in serialized
    assert quote not in serialized


def test_zero_claim_delivery_replaces_unreviewed_paraphrase_and_preserves_safe_error(env):
    settings, task = env
    _run, _runtime, state = _make_state(settings, task, task.question)
    state.requirements = []
    result = SimpleNamespace(answer="未经审核的转述", error=None, meta={})

    _rebuild_research_core_answer(result, state, [])

    assert "未经审核的转述" not in result.answer
    assert result.answer == "本轮未取得可核验的原文摘录。"
    assert result.meta["answer_rebuilt"] == "research_core_delivery_boundary"

    result.error = RunError(code="RUN_TIMEOUT", message="本次任务超时，已停止")
    _rebuild_research_core_answer(result, state, [])
    assert result.answer == result.error.message
