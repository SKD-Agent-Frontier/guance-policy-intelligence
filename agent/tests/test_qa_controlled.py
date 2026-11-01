"""Programmatic QA retrieval must stay bounded and preserve original audit gates."""
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from guance_agent.context import RunContext
from guance_agent.kb import MockKB
from guance_agent.orchestrator import run_task
from guance_agent.qa_controlled import (
    TOTAL_EVIDENCE_CHARS,
    execute_controlled,
    messages_for,
    requested_area,
    search_queries,
)
from guance_agent.runtime import Runtime


class ChatAPI:
    def __init__(self):
        self.calls = 0
        self.messages = []

    async def create(self, **kwargs):
        self.calls += 1
        self.messages.append(kwargs["messages"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "answer": "本轮仅取得部分证据。", "claims": [], "insufficient": True,
            }, ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=240, completion_tokens=35),
        )


def _context(settings, task, kb, *, scope=None):
    ctx = RunContext(settings, kb, task.run_id, task.session_id, question=task.question)
    ctx.scope = scope
    ctx.route = SimpleNamespace(
        kind="objects" if scope else "open", planids=scope or (),
        areas=(), fyps=(), sub_query="",
    )
    return ctx


async def test_qa_programmatically_reads_before_one_model_call(env):
    settings, origin = env
    task = origin.model_copy(update={"question": "农业"})
    api = ChatAPI()
    rt = SimpleNamespace(
        client=SimpleNamespace(chat=SimpleNamespace(completions=api)),
        meta={}, log=lambda *a, **kw: None,
    )
    ctx = _context(settings, task, MockKB(settings.mock_kb_path),
                   scope=("TEST-001",))
    outcome = await execute_controlled(task, settings, ctx, rt,
                                       time.monotonic() + 120)
    assert outcome.error_code is None
    assert api.calls == 1
    assert any(c["name"] == "kb_search" and c["status"] == "ok"
               for c in ctx.tool_calls)
    assert any(c["name"] == "kb_read" and c["status"] == "ok"
               for c in ctx.tool_calls)
    assert rt.meta["qa_execution_mode"] == "bounded_retrieval"
    assert set(rt.meta["qa_controlled_retrieval"]["read_planids"]) == {"TEST-001"}
    assert rt.meta["llm"]["model_calls"] == 1
    assert len(api.messages[0][1]["content"]) < 8500
    assert "kb_search" not in api.messages[0][0]["content"]


async def test_no_search_match_never_calls_model(env):
    settings, origin = env
    task = origin.model_copy(update={"question": "完全不存在的显微奇异术语"})
    api = ChatAPI()
    rt = SimpleNamespace(
        client=SimpleNamespace(chat=SimpleNamespace(completions=api)),
        meta={}, log=lambda *a, **kw: None,
    )
    kb = MockKB(settings.mock_kb_path)
    ctx = _context(settings, task, kb, scope=("TEST-001",))
    outcome = await execute_controlled(task, settings, ctx, rt,
                                       time.monotonic() + 120)
    assert outcome.error_code is None and api.calls == 0
    parsed = json.loads(outcome.final_output)
    assert parsed["insufficient"] is True and parsed["claims"] == []
    assert "不代表相关政策不存在" in parsed["answer"]


def test_search_queries_are_bounded_and_sent_context_excludes_history():
    queries = search_queries("研究数字经济的产业扶持和人才培养政策")
    assert len(queries) == 2
    assert queries == ["数字经济", "产业扶持"]
    task = SimpleNamespace(question="产业结构调整")
    evidence = [{"chunk_id": str(i), "planid": "x", "title": "title",
                 "section_path": "section", "text": "a" * 2500} for i in range(5)]
    messages = messages_for(task, evidence)
    assert sum(min(len(x["text"]), 2100) for x in evidence[:3]) > TOTAL_EVIDENCE_CHARS
    assert messages[1]["content"].count("【来源】") == 3
    assert len(messages[1]["content"]) < TOTAL_EVIDENCE_CHARS + 1100


@pytest.mark.asyncio
async def test_real_rag_path_uses_controlled_qa_without_sdk_tool_loop(env, monkeypatch):
    settings, origin = env
    settings = replace(
        settings, kb_backend="rag", rag_store_project="/offline-rag-test",
        llm_base_url="https://example.invalid/v1", llm_api_key="fake",
    )
    task = origin.model_copy(update={"question": "农业"})
    api = ChatAPI()
    client = SimpleNamespace(chat=SimpleNamespace(completions=api))
    kb = MockKB(settings.mock_kb_path)
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda _: client)
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda _: kb)
    rt = Runtime(settings, task)
    rt.received()
    rt.prepare()
    try:
        result = await run_task(task, settings, rt)
    finally:
        rt.close()
    assert api.calls == 1
    assert result.meta.get("qa_execution_mode") == "bounded_retrieval"
    assert [c["name"] for c in result.meta["tool_calls"]].count("kb_read") >= 1
    assert result.meta["llm"]["model_calls"] == 1
    assert result.meta["llm"]["parse"] == "ok"


def test_area_parser_normalizes_verb_prefix_but_keeps_city_scope():
    for value, expected in [
        ("给出天津市", "天津市"),
        ("苏州市", "苏州市"),
        ("请查询成都市", "成都市"),
    ]:
        decision = SimpleNamespace(areas=(value,))
        assert requested_area(decision, "请给出天津市和苏州市或成都市的政策") == expected
