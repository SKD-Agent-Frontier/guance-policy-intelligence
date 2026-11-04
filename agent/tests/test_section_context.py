import json
import time
from types import SimpleNamespace

import pytest

from guance_agent.qa_controlled import _read_relevant_parent, messages_for


@pytest.mark.asyncio
@pytest.mark.parametrize("primary,expanded", [
    ("compare", True), ("analyze", True), ("fact_lookup", False),
])
async def test_actual_acquire_uses_serialized_intent_for_expansion(env, monkeypatch, primary, expanded):
    from guance_agent.context import RunContext
    from guance_agent.kb import MockKB
    from guance_agent.qa_controlled import acquire

    settings, original = env
    task = original.model_copy(update={"question": "农业灌溉"})
    ctx = RunContext(settings, MockKB(settings.mock_kb_path), task.run_id,
                     task.session_id, question=task.question, scope=("TEST-001",))
    ctx.research_intent = {"primary": primary}
    called = []

    async def parent(*args):
        called.append(True)
        return [{"chunk_id": "parent", "planid": "TEST-001", "title": "规划",
                 "section_path": "农业", "text": "完整相关章节", "section_context": True}]

    monkeypatch.setattr("guance_agent.qa_controlled._read_relevant_parent", parent)
    result = await acquire(task, ctx, time.monotonic() + 180)
    assert bool(called) is expanded
    if expanded:
        assert result[0]["chunk_id"] == "parent"


@pytest.mark.asyncio
async def test_parent_pages_preserve_chunk_text_and_message_tail(monkeypatch):
    task = SimpleNamespace(question="产业发展有哪些安排？")
    text = "农业灌溉和标准化基地。" * 200
    split = 1800
    calls = []
    ctx = SimpleNamespace(settings=SimpleNamespace(max_tool_calls=20), tool_calls=[],
        kb=SimpleNamespace(read=lambda: None),
        evidence=SimpleNamespace(get=lambda cid: SimpleNamespace(source="kb_read", complete=True)))

    async def call(ctx, name, method, planid, section, max_chars, **kwargs):
        calls.append((section, kwargs["offset"]))
        ctx.tool_calls.append({"name": name})
        if section is None:
            data = {"is_index": True, "sections": [{"idx": 2, "path": "三、产业发展"}]}
        else:
            offset = kwargs["offset"]
            end = min(offset + split, len(text))
            data = {"planid": "DOC", "title": "规划", "area": "甲市", "fyp": "十四五",
                "section_path": "三、产业发展", "char_start": offset,
                "content": text[offset:end], "next_offset": end if end < len(text) else None,
                "chunks": [{"chunk_id": "C", "char_start": offset, "char_end": end}]}
        return json.dumps({"status": "ok", "data": data}, ensure_ascii=False)

    monkeypatch.setattr("guance_agent.qa_controlled._call", call)
    evidence = await _read_relevant_parent(task, ctx, [{"planid": "DOC",
        "section_path": "三、产业发展 / 农业"}], time.monotonic() + 180,
        ("DOC",), {"甲市"}, set())
    assert calls == [(None, 0), (2, 0), (2, split)]
    assert evidence[0]["text"] == text
    assert text in messages_for(task, evidence)[1]["content"]


@pytest.mark.asyncio
async def test_section_scope_mismatch_is_not_published(monkeypatch):
    task = SimpleNamespace(question="产业发展有哪些安排？")
    ctx = SimpleNamespace(settings=SimpleNamespace(max_tool_calls=20), tool_calls=[],
        kb=SimpleNamespace(read=lambda: None))

    async def call(ctx, name, method, planid, section, max_chars, **kwargs):
        ctx.tool_calls.append({"name": name})
        data = ({"is_index": True, "sections": [{"idx": 2, "path": "产业发展"}]}
                if section is None else {"planid": "DOC", "area": "乙市", "content": "无关"})
        return json.dumps({"status": "ok", "data": data}, ensure_ascii=False)

    monkeypatch.setattr("guance_agent.qa_controlled._call", call)
    assert await _read_relevant_parent(task, ctx, [{"planid": "DOC",
        "section_path": "产业发展 / 农业"}], time.monotonic() + 180,
        ("DOC",), {"甲市"}, set()) == []


@pytest.mark.asyncio
async def test_historical_cache_miss_returns_to_retrieval_before_drafting(monkeypatch):
    from guance_agent.orchestrator import _run_historical_research

    old = SimpleNamespace(meta={})
    ctx = SimpleNamespace(scope=None)
    rt = SimpleNamespace(meta={"research_requirements": [{"id": "new-topic"}]})
    monkeypatch.setattr("guance_agent.orchestrator.build_historical_state",
                        lambda *args: (old, object()))
    monkeypatch.setattr("guance_agent.orchestrator.build_coverage", lambda *args: {
        "requirement_coverage": [{"status": "missing", "phase": "evidence", "actionable": True}]})

    async def no_draft(*args, **kwargs):
        raise AssertionError("Unanswered factual requirements must return to retrieval")

    monkeypatch.setattr("guance_agent.orchestrator.draft_verified_report", no_draft)
    assert await _run_historical_research(
        SimpleNamespace(question="同一规划里的另一产业呢？"), ctx, rt,
        time.monotonic() + 180, {}, SimpleNamespace(primary="organize")) is None
    assert ctx.scope is None
    assert rt.meta["historical_reuse_skipped"] == "current_evidence_requirements_missing"


def test_document_reference_with_new_topic_does_not_reuse_old_facts():
    from guance_agent.research_intent import classify_intent

    from .test_research_intent import PRIOR

    intent = classify_intent("同一规划回顾的产业升级成果有哪些？把工业、旅游和现代服务业的变化分开说明。", PRIOR)
    assert not intent.use_history


@pytest.mark.asyncio
async def test_section_selection_receives_prior_question_during_focused_retrieval(monkeypatch):
    from guance_agent.qa_controlled import execute_controlled

    seen = []

    async def complete(messages, **_kwargs):
        seen.append(json.loads(messages[-1]["content"]))
        return '{"section_idx":2}'

    async def acquire(_task, _ctx, _deadline, **kwargs):
        selected = await kwargs["section_selector"]({
            "title": "示例规划", "sections": [{"idx": 2, "path": "发展回顾"}]})
        assert selected == 2
        return []

    monkeypatch.setattr("guance_agent.qa_controlled.acquire", acquire)
    prior_question = "回顾上一规划期交通建设已经取得的成果。"
    ctx = SimpleNamespace(qa_followup={"previous_question": prior_question,
        "context_note": "研究对象为同一规划，前轮询问已有成果。"}, tool_calls=[])
    rt = SimpleNamespace(meta={}, complete_text=complete)
    await execute_controlled(SimpleNamespace(question="其中铁路进展有哪些？"),
                             SimpleNamespace(), ctx, rt, time.monotonic() + 180,
                             focuses=["铁路"])
    assert seen[0]["previous_question"] == prior_question
    assert seen[0]["question"] == "其中铁路进展有哪些？"
