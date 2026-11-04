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
    _period_matches_content,
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


def test_requested_content_period_may_come_from_the_current_passage():
    requested = {"第十个五年计划"}
    assert _period_matches_content(
        requested, "第十一个五年规划", "一、“十五”金融业发展基本情况",
        "十五期间保险业务快速发展。",
    )
    assert not _period_matches_content(
        requested, "第十一个五年规划", "十一五发展目标",
        "十一五期间金融业收入增长目标。",
    )


@pytest.mark.asyncio
async def test_period_metadata_miss_retries_bounded_search_by_content_period(env, monkeypatch):
    settings, origin = env
    task = origin.model_copy(update={"question": "常德市十五期间金融业保险业务有哪些？"})
    hit = {"chunk_id": "period-chunk", "planid": "965", "title": "常德市金融业发展十一五规划",
           "area": "常德市", "fyp": "第十一个五年规划", "section_path": "十五金融业基本情况",
           "content": "十五期间保险业务发展。"}
    searches = []

    async def fake_call(ctx, name, method, *args, **kwargs):
        if name == "kb_search":
            searches.append(args)
            return json.dumps({"status": "empty"} if args[2] else
                              {"status": "ok", "data": [hit]})
        return json.dumps({"status": "ok", "data": {
            "chunks": [{"chunk_id": "period-chunk"}], "is_index": False,
            "planid": "965", "title": hit["title"], "area": "常德市",
            "fyp": "第十一个五年规划", "content": hit["content"],
            "section_path": hit["section_path"],
        }})

    monkeypatch.setattr("guance_agent.qa_controlled._call", fake_call)
    ref = SimpleNamespace(source="kb_read", observed_ranges=[(0, 20)])
    ctx = SimpleNamespace(
        route=SimpleNamespace(kind="open", planids=(), areas=("常德市",),
                              fyps=("第十个五年计划",), sub_query="保险业务"),
        scope=None, qa_followup=None, research_intent=None, tool_calls=[],
        settings=SimpleNamespace(max_tool_calls=20), evidence=SimpleNamespace(get=lambda _: ref),
        kb=SimpleNamespace(search=lambda *args, **kwargs: None,
                           read=lambda *args, **kwargs: None),
    )
    evidence = await acquire(task, ctx, time.monotonic() + 180)
    assert len(searches) == 2
    assert searches[0][2] == "第十个五年计划"
    assert searches[1][2] is None
    assert [item["planid"] for item in evidence] == ["965"]


@pytest.mark.asyncio
async def test_catalog_facets_are_frozen_before_the_answer_model_call(env, monkeypatch):
    settings, origin = env
    task = origin.model_copy(update={"question": "规划发展目标涉及哪些方面？"})
    api = ChatAPI()
    rt = SimpleNamespace(
        client=SimpleNamespace(chat=SimpleNamespace(completions=api)),
        meta={"research_requirements": [{"id": "subject:1", "kind": "subject_fact",
                                          "label": "发展目标", "subject": "发展目标"}]},
        log=lambda *a, **kw: None,
    )
    ctx = _context(settings, task, MockKB(settings.mock_kb_path), scope=("TEST-001",))

    async def fake_acquire(task, ctx, deadline, **kwargs):
        ctx._overview_catalog = {
            "status": "accepted", "planid": "TEST-001", "title": "规划",
            "facets": [
                {"section_idx": 2, "label": "经济表现",
                 "section_path": "一、发展目标"},
                {"section_idx": 2, "label": "公共服务",
                 "section_path": "一、发展目标"},
                {"section_idx": None, "label": "发展目标的正文分项超过本轮展开预算",
                 "section_path": "一、发展目标", "overflow": True},
            ],
        }
        return [{"chunk_id": "catalog-chunk", "planid": "TEST-001", "title": "规划",
                 "section_path": "一、发展目标",
                 "text": "推进经济表现和公共服务。"}]

    monkeypatch.setattr("guance_agent.qa_controlled.acquire", fake_acquire)
    outcome = await execute_controlled(task, settings, ctx, rt, time.monotonic() + 120)
    assert outcome.error_code is None
    facets = [unit for unit in rt.meta["research_requirements"]
              if unit.get("label") in {"目录方面：经济表现", "目录方面：公共服务"}]
    assert len(facets) == 2
    assert all(rt.meta["catalog_requirement_sources"][facet["id"]] == {
        "planid": "TEST-001", "section_path": "一、发展目标",
    } for facet in facets)
    unresolved = next(unit for unit in rt.meta["research_requirements"]
                      if unit.get("label") == "发展目标的正文分项超过本轮展开预算")
    assert unresolved["kind"] == "unresolved_requirement"
    assert unresolved["actionable"] is False
    assert "经济表现" in "\n".join(
        str(message.get("content") or "") for message in api.messages[0])
    assert "公共服务" in "\n".join(
        str(message.get("content") or "") for message in api.messages[0])


@pytest.mark.asyncio
async def test_overview_retrieval_reads_catalog_then_searches_heading_facets(env, monkeypatch):
    settings, origin = env
    task = origin.model_copy(update={"question": "主要任务有哪些方面？"})
    paths = {
        "经济发展": "一、主要任务 / （一）经济发展",
        "城乡建设": "一、主要任务 / （二）城乡建设",
    }
    base_hit = {"chunk_id": "base", "planid": "overview-doc", "title": "示例规划",
                "area": "示例市", "fyp": "十五五", "section_path": "一、主要任务",
                "content": "推进经济和城乡建设。"}
    facet_hits = {
        label: {**base_hit, "chunk_id": "chunk-" + label,
                "section_path": path, "content": label + "规划措施。"}
        for label, path in paths.items()
    }
    refs = {hit["chunk_id"]: SimpleNamespace(source="kb_read", observed_ranges=[(0, 30)])
            for hit in [base_hit, *facet_hits.values()]}
    ctx = _context(settings, task, SimpleNamespace(search=None, read=None))
    ctx.evidence = SimpleNamespace(get=refs.get)
    calls = []

    async def fake_call(ctx, name, method, *args, **kwargs):
        calls.append((name, args, kwargs))
        ctx.tool_calls.append({"name": name, "status": "ok"})
        if name == "kb_search":
            query = args[0]
            hits = [base_hit] if len(calls) == 1 else [facet_hits[query]]
            return json.dumps({"status": "ok", "data": hits})
        if kwargs.get("chunk_id") is None:
            return json.dumps({"status": "ok", "data": {
                "planid": "overview-doc", "title": "示例规划", "is_index": True,
                "sections": [{"idx": index, "path": path}
                             for index, path in enumerate(paths.values(), start=1)],
            }})
        cid = kwargs["chunk_id"]
        hit = next(item for item in [base_hit, *facet_hits.values()]
                    if item["chunk_id"] == cid)
        return json.dumps({"status": "ok", "data": {
            "planid": hit["planid"], "title": hit["title"], "area": hit["area"],
            "fyp": hit["fyp"], "is_index": False, "content": hit["content"],
            "section_path": hit["section_path"],
            "chunks": [{"chunk_id": cid}],
        }})

    monkeypatch.setattr("guance_agent.qa_controlled._call", fake_call)
    evidence = await acquire(task, ctx, time.monotonic() + 180)
    assert [call[0] for call in calls].count("kb_read") >= 2
    assert {call[1][0] for call in calls if call[0] == "kb_search"} >= {
        "经济发展", "城乡建设",
    }
    assert evidence
    assert len(evidence) >= 2, ctx._qa_search_diagnostics
    assert [item["label"] for item in ctx._overview_catalog["facets"]] == [
        "经济发展", "城乡建设",
    ]


@pytest.mark.asyncio
async def test_overview_expands_parent_section_before_freezing_research_facets(env, monkeypatch):
    settings, origin = env
    task = origin.model_copy(update={"question": "发展目标主要涉及哪些方面？"})
    parent = {"chunk_id": "base", "planid": "outline-doc", "title": "示例规划",
                  "area": "示例市", "fyp": "十五五", "section_path": "四、主要任务",
              "content": "发展目标"}
    hits = {
        label: {**parent, "chunk_id": f"chunk-{label}",
                    "section_path": "三、经济和社会发展重点", "content": label}
        for label in ("经济表现", "公共服务")
    }
    refs = {hit["chunk_id"]: SimpleNamespace(source="kb_read", observed_ranges=[(0, 80)])
            for hit in [parent, *hits.values()]}
    ctx = _context(settings, task, SimpleNamespace(search=None, read=None))
    ctx.evidence = SimpleNamespace(get=refs.get)
    calls = []

    async def fake_call(ctx, name, method, *args, **kwargs):
        calls.append((name, args, kwargs))
        ctx.tool_calls.append({"name": name, "status": "ok"})
        if name == "kb_search":
            query = args[0]
            return json.dumps({"status": "ok", "data":
                               [parent] if query == "发展目标" else [hits[query]]})
        if args[1] is None and kwargs.get("chunk_id") is None:
            return json.dumps({"status": "ok", "data": {
                "planid": "outline-doc", "title": "示例规划", "is_index": True,
                "sections": [{"idx": 1, "path": "三、经济和社会发展重点"}],
            }})
        if kwargs.get("chunk_id") is None:
            return json.dumps({"status": "ok", "data": {
                "planid": "outline-doc", "title": "示例规划", "is_index": False,
                "section_path": "三、经济和社会发展重点",
                "content": "发展目标：——经济表现：稳步提升。——公共服务：持续完善。",
            }})
        hit = next(item for item in [parent, *hits.values()]
                   if item["chunk_id"] == kwargs["chunk_id"])
        return json.dumps({"status": "ok", "data": {
            "planid": hit["planid"], "title": hit["title"], "area": hit["area"],
            "fyp": hit["fyp"], "is_index": False, "content": hit["content"],
            "section_path": hit["section_path"],
            "chunks": [{"chunk_id": hit["chunk_id"]}],
        }})

    monkeypatch.setattr("guance_agent.qa_controlled._call", fake_call)
    evidence = await acquire(task, ctx, time.monotonic() + 180)

    labels = [facet["label"] for facet in ctx._overview_catalog["facets"]]
    assert labels == ["经济表现", "公共服务"]
    assert {call[1][0] for call in calls if call[0] == "kb_search"} >= {
        "经济表现", "公共服务",
    }
    assert evidence, (ctx._qa_search_diagnostics, calls)


@pytest.mark.asyncio
async def test_acquire_comparison_expansion_searches_prior_object_and_current_side(env, monkeypatch):
    settings, origin = env
    question = "这些工业发展目标与农业特色产业相比有何不同？比如大棚果和奶牛养殖。"
    prior_question = "你上面提到的第①项中，铝型材和不锈钢加工业的具体发展目标是什么？"
    task = origin.model_copy(update={"question": question})
    ctx = _context(settings, task, SimpleNamespace(search=None, read=None),
                   scope=("1831",))
    ctx.research_intent = {"comparison_expansion": True, "primary": "compare"}
    queries = []

    async def empty_search(ctx, name, method, *args, **kwargs):
        ctx.tool_calls.append({"name": name, "status": "empty"})
        queries.append(args[0])
        return json.dumps({"status": "empty", "data": []})

    monkeypatch.setattr("guance_agent.qa_controlled._call", empty_search)
    await acquire(task, ctx, time.monotonic() + 180,
                  query_question=prior_question + " " + question)

    assert any("铝型材" in query or "不锈钢加工业" in query for query in queries)
    assert any("大棚果" in query or "奶牛养殖" in query for query in queries)


@pytest.mark.asyncio
async def test_acquire_comparison_keeps_explicit_current_topic_query(env, monkeypatch):
    settings, origin = env
    question = "与甲市其他地市相比，乙市在第十三个五年规划中提出的产业结构优化目标有什么特点？"
    task = origin.model_copy(update={"question": question})
    ctx = _context(settings, task, SimpleNamespace(search=None, read=None),
                   scope=("plan-b",))
    ctx.research_intent = {"comparison_expansion": True, "primary": "compare"}
    searched = []

    async def empty_search(ctx, name, method, *args, **kwargs):
        ctx.tool_calls.append({"name": name, "status": "empty"})
        searched.append(args[0])
        return json.dumps({"status": "empty", "data": []})

    monkeypatch.setattr("guance_agent.qa_controlled._call", empty_search)
    await acquire(task, ctx, time.monotonic() + 180,
                  query_question="此前的经济实力目标。 " + question)

    assert any("产业结构优化目标" in query for query in searched)


@pytest.mark.asyncio
async def test_numeric_anaphor_keeps_prior_topic_inside_verified_scope(env, monkeypatch):
    settings, origin = env
    question = "这项要求是已经实现的效果，还是报告提出的目标？原文依据是什么？"
    prior = "planid=12915《云南省2019年政府工作报告》中，要求再为企业降低成本多少？"
    task = origin.model_copy(update={"question": question})
    ctx = _context(settings, task, SimpleNamespace(search=None, read=None), scope=("12915",))
    ctx.qa_followup = {"status": "continued", "previous_question": prior}
    searched = []

    async def empty_search(ctx, name, method, *args, **kwargs):
        ctx.tool_calls.append({"name": name, "status": "empty"})
        searched.append((args[0], kwargs.get("planids")))
        return json.dumps({"status": "empty", "data": []})

    monkeypatch.setattr("guance_agent.qa_controlled._call", empty_search)
    await acquire(task, ctx, time.monotonic() + 180, query_question=prior + " " + question)
    assert any("成本" in query for query, _ in searched)
    assert all(scope == ("12915",) for _, scope in searched)


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


def test_comparison_expansion_search_queries_keep_prior_side_and_current_focus():
    combined = (
        "你上面提到的第①项中，关于铝型材和不锈钢加工业的具体发展目标是什么？"
        "这些工业发展目标与农业领域的特色产业培育相比，支持方式有何不同？"
        "比如大棚果和奶牛养殖方面如何部署？"
    )

    queries = search_queries(combined)

    assert any("铝型材" in query or "不锈钢加工业" in query for query in queries)
    assert any("大棚果" in query or "奶牛养殖" in query for query in queries)


def test_comparison_queries_keep_current_objective_ahead_of_relation_cues():
    queries = search_queries(
        "与甲市其他地市相比，乙市在第十三个五年规划中提出的产业结构优化目标有什么特点？",
        comparison_intent=True,
    )

    assert "产业结构优化目标" in queries[0]
    assert queries[0] not in {"共同点", "差异", "比较"}


def test_broad_development_and_advantage_requirements_get_short_alternate_queries():
    queries = search_queries(
        "密云县十一五规划中关于发展基础和比较优势的总体情况，有哪些主要方面？")

    assert "发展基础" in queries
    assert "比较优势" in queries
    assert "经济发展" in queries
    assert "有利条件" in queries
    assert len(queries) <= 4


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
    assert api.calls == 2  # pre-answer requirements planner + grounded answer
    assert result.meta.get("qa_execution_mode") == "bounded_retrieval"
    assert [c["name"] for c in result.meta["tool_calls"]].count("kb_read") >= 1
    assert result.meta["llm"]["model_calls"] == 1
    assert result.meta["llm"]["parse"] == "ok"
    assert result.meta["research_requirement_planner"]["status"] == "invalid_response"
    assert result.meta["text_model_usage"]["by_purpose"]["requirement_planner"]["model_calls"] == 1


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
