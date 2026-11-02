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
    acquire,
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
    assert len(queries) == 3
    assert queries == ["数字经济", "产业扶持", "人才培养"]
    task = SimpleNamespace(question="产业结构调整")
    evidence = [{"chunk_id": str(i), "planid": "x", "title": "title",
                 "section_path": "section", "text": "a" * 2500} for i in range(5)]
    messages = messages_for(task, evidence)
    assert sum(min(len(x["text"]), 1800) for x in evidence) > TOTAL_EVIDENCE_CHARS
    assert messages[1]["content"].count("【来源】") == 5
    assert len(messages[1]["content"]) < TOTAL_EVIDENCE_CHARS + 1100


def test_explicit_opportunity_challenge_dimension_adds_concrete_facets():
    queries = search_queries(
        "与北京市其他远郊区县相比，密云区在'十一五'期间面临的发展机遇和挑战有什么独特之处？")
    assert "发展机遇" in queries
    assert "面临挑战" in queries
    assert len(queries) <= 4


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


@pytest.mark.asyncio
async def test_complex_scoped_question_runs_multiple_facets_before_generation(env):
    settings, original = env
    task = original.model_copy(update={"question": (
        "分析某市产业结构调整的产业发展方向、重点措施和政策意义，并形成研究报告。"
    )})
    queries = search_queries(task.question)
    assert len(queries) >= 2
    assert "产业结构调整" in queries
    api = ChatAPI()

    class TracedKB(MockKB):
        def __init__(self, path):
            super().__init__(path)
            self.queries = []

        def search(self, query, area, fyp, top_k, *, planids=None):
            self.queries.append(query)
            return super().search("农业", area, fyp, top_k, planids=planids)

    kb = TracedKB(settings.mock_kb_path)
    ctx = _context(settings, task, kb, scope=("TEST-001",))
    rt = SimpleNamespace(
        client=SimpleNamespace(chat=SimpleNamespace(completions=api)),
        meta={}, log=lambda *a, **kw: None,
    )
    outcome = await execute_controlled(task, settings, ctx, rt,
                                       time.monotonic() + 120)
    assert outcome.error_code is None
    assert len(kb.queries) >= 2
    assert api.calls == 1
    assert 1 <= len(rt.meta["qa_controlled_retrieval"]["read_chunks"]) <= 6


@pytest.mark.asyncio
async def test_followup_can_exclude_previously_read_chunks(env):
    settings, original = env
    task = original.model_copy(update={"question": "研究农业发展政策"})
    kb = MockKB(settings.mock_kb_path)
    ctx = _context(settings, task, kb, scope=("TEST-001",))
    api = ChatAPI()
    rt = SimpleNamespace(
        client=SimpleNamespace(chat=SimpleNamespace(completions=api)),
        meta={}, log=lambda *a, **kw: None,
    )
    first = await execute_controlled(task, settings, ctx, rt,
                                     time.monotonic() + 120)
    assert first.error_code is None and api.calls == 1
    current = frozenset(ctx.evidence.ids())
    second = await execute_controlled(
        task, settings, ctx, rt, time.monotonic() + 120,
        focus="农业", exclude_chunks=current,
    )
    assert second.error_code is None
    assert not set(rt.meta["qa_supplement_retrieval"]["read_chunks"]) & current


@pytest.mark.asyncio
async def test_open_digital_economy_rejects_unrelated_tourism_fund(monkeypatch):
    task = SimpleNamespace(question=(
        "研究数字经济相关政策，在产业扶持、基础设施建设、人才培养方面的异同"
    ))
    hits = [
        {"chunk_id": "bad", "planid": "99", "title": "文旅专项资金",
         "section_path": "资金管理", "content": "支持人才培养"},
        {"chunk_id": "good", "planid": "100", "title": "数字经济发展政策",
         "section_path": "数字经济", "content": "数字经济发展措施"},
    ]
    read_calls, searches = [], []

    async def fake_call(ctx, name, method, *args, **kwargs):
        if name == "kb_search":
            searches.append(args[0])
            return json.dumps({"status": "ok", "data": hits})
        cid = kwargs["chunk_id"]
        read_calls.append(cid)
        return json.dumps({
            "status": "ok",
            "data": {
                "chunks": [{"chunk_id": cid}],
                "is_index": False,
                "content": ("促进数字经济基础设施建设" if cid == "good"
                            else "支持文化旅游产业发展"),
                "section_path": "政策措施",
            },
        })

    refs = {
        cid: SimpleNamespace(source="kb_read", observed_ranges=[(0, 10)])
        for cid in ("good", "bad")
    }
    ctx = SimpleNamespace(
        route=SimpleNamespace(kind="open", planids=(), areas=(), fyps=(),
                              sub_query=""),
        scope=None,
        tool_calls=[],
        evidence=SimpleNamespace(get=refs.get),
        kb=SimpleNamespace(search=lambda *args, **kwargs: None,
                           read=lambda *args, **kwargs: None),
    )
    monkeypatch.setattr("guance_agent.qa_controlled._call", fake_call)
    evidence = await acquire(task, ctx, time.monotonic() + 180)
    assert len(searches) >= 2
    assert all(query.startswith("数字经济") for query in searches)
    assert [item["chunk_id"] for item in evidence] == ["good"]
    assert ctx._qa_search_diagnostics["topic_filter"] == "digital_economy"
    assert ctx._qa_search_diagnostics["unrelated_read_rejected"] == 1
    assert len(read_calls) <= 6
