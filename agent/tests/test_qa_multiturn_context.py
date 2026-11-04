"""Multi-turn trusted context: deterministic resolution and real controlled QA wiring."""
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from guance_agent.context import RunContext
from guance_agent.kb import MockKB
from guance_agent.orchestrator import run_task
from guance_agent.qa_context import (
    TrustedContextStore,
    build_trusted_turn,
    resolve_followup,
)
from guance_agent.qa_controlled import execute_controlled
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


def _turn(question, *documents):
    return {
        "question": question,
        "documents": list(documents),
        "verified_chunk_ids": ["chunk_1"],
        "verified_claim_count": 1,
    }


def test_store_round_trip_and_does_not_persist_claims(tmp_path):
    store = TrustedContextStore(tmp_path / "sessions.sqlite", "s_test")
    turn = _turn("上一轮问题", {"planid": "TEST-001", "title": "测试规划",
                              "area": "甲市", "fyp": "第十个五年计划"})
    store.append(turn)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0]["documents"][0]["planid"] == "TEST-001"
    assert "claims" not in loaded[0]
    assert "answer" not in loaded[0]


def test_failed_turn_keeps_only_explicit_scope_for_followup_research():
    from guance_agent.routing import RouteDecision

    ctx = SimpleNamespace(
        route=RouteDecision(kind="open", areas=("衡水市",),
                            fyps=("第十三个五年规划",)),
        evidence=None,
        qa_context_resolution={"status": "no_prior", "requested_constraints": None},
        research_intent=None,
    )
    result = SimpleNamespace(
        status="failed", error=object(), evidence_refs=[], claims=[], meta={},
        evidence_status="insufficient", structured=None,
    )

    turn = build_trusted_turn("需要归纳衡水市十三五规划", ctx, result)

    assert turn["user_scope"]["areas"] == ["衡水市"]
    assert turn["user_scope"]["periods"] == ["第十三个五年规划"]
    assert "衡水市" in turn["user_scope"]["research_query"]
    assert turn["documents"] == []
    assert turn["verified_facts"] == []


def test_trusted_turn_persists_all_frozen_requirements_beyond_old_cap():
    from guance_agent.routing import RouteDecision

    requirements = [{"id": f"requirement-{index}", "kind": "subject_fact",
                     "label": f"目标方面{index}"} for index in range(25)]
    ctx = SimpleNamespace(
        route=RouteDecision(kind="open", areas=("衡水市",)), evidence=None,
        qa_context_resolution={"status": "no_prior"}, research_intent=None,
    )
    result = SimpleNamespace(
        status="failed", error=object(), evidence_refs=[], claims=[], meta={
            "research_requirements": requirements,
        }, evidence_status="insufficient", structured=None,
    )

    turn = build_trusted_turn("请总结衡水市十三五发展目标", ctx, result)

    assert len(turn["user_scope"]["requirements"]) == 25
    assert turn["user_scope"]["requirements"][-1]["id"] == "requirement-24"


def test_failed_followup_keeps_prior_pending_user_scope_for_next_round():
    from guance_agent.routing import RouteDecision

    prior = {"question": "衡水市十三五规划经济发展", "documents": [],
             "user_scope": {"areas": ["衡水市"],
                            "periods": ["第十三个五年规划"],
                            "titles": [], "planids": []}}
    ctx = SimpleNamespace(
        route=RouteDecision(kind="open"), evidence=None,
        qa_context_resolution={"status": "fresh_research",
                               "requested_constraints": {
                                   "areas": ["衡水市"],
                                   "periods": ["第十三个五年规划"],
                                   "titles": [],
                               }},
        research_intent=None, trusted_turns=[prior],
    )
    result = SimpleNamespace(
        status="completed", error=None, evidence_refs=[], claims=[], meta={
            "quote_verification": {"approved": 0, "unverified": 0}},
        evidence_status="insufficient", structured=None,
    )

    turn = build_trusted_turn("你上面提到的规划数值是什么？", ctx, result)

    assert turn["user_scope"]["areas"] == ["衡水市"]
    assert turn["user_scope"]["periods"] == ["第十三个五年规划"]
    assert turn["documents"] == []
    assert turn["verified_facts"] == []


def test_pending_research_question_survives_multiple_no_result_followups():
    from guance_agent.routing import RouteDecision

    first = {"question": "密云县十一五规划的发展基础和比较优势有哪些？",
             "documents": [],
             "user_scope": {"areas": ["密云区"],
                            "periods": ["第十一个五年规划"],
                            "research_query": "密云县十一五规划的发展基础和比较优势有哪些？",
                            "requirements": []}}
    followup = resolve_followup(
        "你上面提到的第①项经济发展目标具体是多少？", [first],
        RouteDecision(kind="open"))
    assert followup["status"] == "fresh_research"
    ctx = SimpleNamespace(
        route=RouteDecision(kind="open"), evidence=None,
        qa_context_resolution=followup, research_intent=None,
        trusted_turns=[first],
    )
    result = SimpleNamespace(
        status="completed", error=None, evidence_refs=[], claims=[], meta={
            "quote_verification": {"approved": 0, "unverified": 0}},
        evidence_status="insufficient", structured=None,
    )
    second = build_trusted_turn(
        "你上面提到的第①项经济发展目标具体是多少？", ctx, result)
    third = resolve_followup(
        "上面第①项能按规划原文再核实一下吗？", [first, second],
        RouteDecision(kind="open"))

    assert third["status"] == "fresh_research"
    assert "发展基础和比较优势" in third["retrieval_query"]
    assert third["inherited_planids"] == []


def test_resolver_continues_single_document_and_marks_retrieval_context():
    documents = [{"planid": "2561", "title": "沧州市十五计划", "area": "沧州市",
                  "fyp": "第十个五年计划"}]
    resolution = resolve_followup(
        "其中关于高新技术产业的部分具体是怎么说的？",
        [_turn("沧州市十五计划中产业结构调整的主要措施有哪些？", *documents)],
        SimpleNamespace(areas=(), fyps=()),
    )
    assert resolution["status"] == "continued"
    assert resolution["inherited_planids"] == ["2561"]
    assert "沧州市十五计划" in resolution["context_note"]
    assert "产业结构调整" in resolution["retrieval_query"]


def test_resolver_does_not_inherit_after_explicit_region_change():
    documents = [{"planid": "2561", "title": "沧州市十五计划", "area": "沧州市",
                  "fyp": "第十个五年计划"}]
    resolution = resolve_followup(
        "那河北省的十五计划里类似措施有哪些？",
        [_turn("沧州市十五计划中产业结构调整的主要措施有哪些？", *documents)],
        SimpleNamespace(areas=("河北省",), fyps=("第十个五年计划",)),
    )
    assert resolution["status"] == "scope_changed"
    assert resolution["inherited_planids"] == []
    assert resolution["requested_constraints"] == {
        "areas": ["河北省"], "periods": ["第十个五年计划"]}
    assert "不得沿用" in resolution["context_note"]


def test_composed_subject_prefix_does_not_put_prior_region_in_new_scope():
    documents = [{"planid": "2561", "title": "沧州市十五计划", "area": "沧州市",
                  "fyp": "第十个五年计划"}]
    resolution = resolve_followup(
        "研究对象：沧州市“十五”计；本轮问题：那河北省的十五计划里类似措施有哪些？",
        [_turn("沧州市十五计划中产业结构调整的主要措施有哪些？", *documents)],
        SimpleNamespace(areas=("河北省",), fyps=("第十个五年计划",)),
    )
    assert resolution["status"] == "scope_changed"
    assert resolution["requested_constraints"] == {
        "areas": ["河北省"], "periods": ["第十个五年计划"]}
    assert "沧州市" not in resolution["requested_constraints"]["areas"]


def test_resolver_marks_ambiguous_when_multiple_previous_documents():
    documents = [
        {"planid": "23132", "title": "海西州数字经济规划", "area": "海西州",
         "fyp": "第十四个五年规划"},
        {"planid": "45100", "title": "双流区新基建规划", "area": "双流区",
         "fyp": "第十四个五年规划"},
    ]
    resolution = resolve_followup(
        "其中关于基础设施的部分具体是怎么说的？",
        [_turn("归纳海西州与双流区的基础设施做法", *documents)],
        SimpleNamespace(areas=(), fyps=()),
    )
    assert resolution["status"] == "ambiguous"
    assert resolution["inherited_planids"] == []
    assert "无法唯一确定" in resolution["clarification"]
    assert "海西州数字经济规划" in resolution["clarification"]


def test_resolver_treats_unrelated_question_as_new_topic():
    documents = [{"planid": "2561", "title": "沧州市十五计划", "area": "沧州市",
                  "fyp": "第十个五年计划"}]
    resolution = resolve_followup(
        "什么是产业结构调整？",
        [_turn("沧州市十五计划中产业结构调整的主要措施有哪些？", *documents)],
        SimpleNamespace(areas=(), fyps=()),
    )
    assert resolution["status"] == "new_topic"
    assert resolution["inherited_planids"] == []


async def _run_turn(settings, kb, api, monkeypatch, task):
    client = SimpleNamespace(chat=SimpleNamespace(completions=api))
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda _: client)
    monkeypatch.setattr("guance_agent.orchestrator.get_kb", lambda _: kb)
    runtime = Runtime(settings, task)
    runtime.received()
    runtime.prepare()
    try:
        return await run_task(task, settings, runtime)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_controlled_qa_inherits_previous_single_document(env, monkeypatch):
    settings, origin = env
    settings = replace(
        settings, kb_backend="rag", rag_store_project="/offline-rag-test",
        llm_base_url="https://example.invalid/v1", llm_api_key="fake",
    )
    kb = MockKB(settings.mock_kb_path)
    first_task = origin.model_copy(update={
        "run_id": "run_first", "question": "农业", "targets": ["TEST-001"],
    })
    first_api = ChatAPI()
    await _run_turn(settings, kb, first_api, monkeypatch, first_task)
    store = TrustedContextStore(settings.session_path, first_task.session_id)
    # The first model returned no claims: read-only hits must not be promoted
    # to trusted follow-up identities. Seed a separately validated prior turn
    # to test the inherited-scope wiring below.
    assert store.load()[-1]["documents"] == []
    store.append(_turn(
        "农业",
        {"planid": "TEST-001", "title": "测试规划", "area": "甲市",
         "fyp": "第十个五年计划"},
    ))

    second_task = origin.model_copy(update={
        "run_id": "run_followup",
        "question": "其中关于农业的部分具体是怎么说的？",
    })
    second_api = ChatAPI()
    second = await _run_turn(settings, kb, second_api, monkeypatch, second_task)
    resolution = second.meta["qa_context_resolution"]
    assert resolution["status"] == "continued"
    assert resolution["inherited_planids"] == ["TEST-001"]
    assert second.meta["qa_controlled_retrieval"]["read_planids"] == ["TEST-001"]
    assert second.meta["llm"]["model_calls"] == 1
    # Planner and a bounded empty-evidence retry are separate from extraction.
    extraction_calls = [messages for messages in second_api.messages
                        if "多轮上下文" in messages[1]["content"]]
    assert extraction_calls
    prompt = extraction_calls[0][1]["content"]
    assert "多轮上下文" in prompt
    assert "TEST-001" in prompt


@pytest.mark.asyncio
async def test_first_turn_policy_summary_retrieves_and_saves_scope_for_followup(env, monkeypatch):
    settings, origin = env
    settings = replace(
        settings, kb_backend="rag", rag_store_project="/offline-rag-test",
        llm_base_url="https://example.invalid/v1", llm_api_key="fake",
    )
    kb = MockKB(settings.mock_kb_path)
    task = origin.model_copy(update={
        "run_id": "run_scoped_summary", "question": "沧州市十五计划的产业优势能否概括？",
    })
    result = await _run_turn(settings, kb, ChatAPI(), monkeypatch, task)

    assert result.meta["qa_execution_mode"] == "bounded_retrieval"
    saved = TrustedContextStore(settings.session_path, task.session_id).load()[-1]
    assert saved["user_scope"]["areas"] == ["沧州市"]
    assert saved["user_scope"]["periods"] == ["第十个五年计划"]

    followup = resolve_followup(
        "你上面提到的产业优势具体是什么？", [saved], SimpleNamespace(areas=(), fyps=()))
    assert followup["status"] == "fresh_research"
    assert followup["inherited_planids"] == []
    assert followup["requested_constraints"]["areas"] == ["沧州市"]


@pytest.mark.asyncio
async def test_ambiguous_followup_stops_before_model_call(env, monkeypatch):
    settings, origin = env
    settings = replace(
        settings, kb_backend="rag", rag_store_project="/offline-rag-test",
        llm_base_url="https://example.invalid/v1", llm_api_key="fake",
    )
    kb = MockKB(settings.mock_kb_path)
    store = TrustedContextStore(settings.session_path, origin.session_id)
    store.append(_turn(
        "归纳两篇政策",
        {"planid": "TEST-001", "title": "测试规划 A", "area": "甲市"},
        {"planid": "TEST-002", "title": "测试规划 B", "area": "乙市"},
    ))

    second_task = origin.model_copy(update={
        "run_id": "run_ambiguous",
        "question": "其中关于农业的部分具体是怎么说的？",
    })
    second_api = ChatAPI()
    second = await _run_turn(settings, kb, second_api, monkeypatch, second_task)
    assert second.meta["qa_context_resolution"]["status"] == "ambiguous"
    assert second_api.calls == 0
    assert "无法唯一确定" in second.answer
    assert second.evidence_status == "insufficient"


@pytest.mark.asyncio
async def test_explicit_period_change_rejects_mismatched_source(env):
    settings, origin = env
    task = origin.model_copy(update={"question": "河北省十五计划有哪些措施？"})
    api = ChatAPI()
    ctx = RunContext(settings, MockKB(settings.mock_kb_path), task.run_id,
                     task.session_id, question=task.question)
    ctx.route = SimpleNamespace(kind="objects", planids=("TEST-001",), areas=("河北省",),
                                fyps=("第十个五年计划",), sub_query="")
    ctx.scope = ("TEST-001",)
    ctx.qa_followup = {"requested_constraints": {
        "areas": ["河北"], "periods": ["第十个五年计划"]}}
    rt = SimpleNamespace(client=SimpleNamespace(chat=SimpleNamespace(completions=api)),
                         meta={}, log=lambda *a, **kw: None)
    outcome = await execute_controlled(task, settings, ctx, rt, time.monotonic() + 120)
    assert outcome.error_code is None
    assert api.calls == 0
    parsed = json.loads(outcome.final_output)
    assert parsed["insufficient"] is True and parsed["claims"] == []


def test_trusted_turn_uses_only_final_retained_claims_and_refs():
    from .test_evidence_contract import make_ref

    original = make_ref(chunk_id="c1", planid="2561", source="kb_read",
                        title="沧州政策", area="沧州市", fyp="第十个五年计划")
    discarded = make_ref(chunk_id="c2", planid="15576", source="kb_read",
                         title="山东禹城", area="山东省", fyp="第十个五年计划")
    registry = SimpleNamespace(get=lambda cid: {"c1": original, "c2": discarded}.get(cid))
    ctx = SimpleNamespace(evidence=registry, qa_context_resolution={"status": "continued"})
    result = SimpleNamespace(
        status="completed", error=None, evidence_status="insufficient",
        claims=[SimpleNamespace(chunk_ids=["c1"], quotes=["已核验原文"])],
        evidence_refs=[original],
        meta={"quote_verification": {"approved": 2, "rejected": 1, "unverified": 0}},
    )
    turn = build_trusted_turn("其中有哪些措施？", ctx, result)
    assert [doc["planid"] for doc in turn["documents"]] == ["2561"]
    assert turn["verified_chunk_ids"] == ["c1"]
    assert turn["verified_claim_count"] == 1

    result.claims = []
    result.evidence_refs = []
    empty = build_trusted_turn("那河北省的政策呢？", ctx, result)
    assert empty["documents"] == []
    assert empty["verified_chunk_ids"] == []


def test_unanswered_scope_change_blocks_resurrection_of_old_policy(tmp_path):
    store = TrustedContextStore(tmp_path / "sessions.sqlite", "s_four_turns")
    old = {"planid": "2561", "title": "沧州市十五计划", "area": "沧州市",
           "fyp": "第十个五年计划"}
    store.append(_turn("沧州市十五计划有哪些措施？", old))
    assert resolve_followup(
        "其中高新技术产业怎么说？", store.load(),
    )["inherited_planids"] == ["2561"]
    # A new region was requested, but its target document could not be found.
    ctx = SimpleNamespace(evidence=None, qa_context_resolution={
        "status": "scope_changed", "requested_constraints": {
            "areas": ["河北省"], "periods": ["第十个五年计划"]}})
    result = SimpleNamespace(status="completed", error=None, claims=[],
                             evidence_refs=[], evidence_status="insufficient",
                             meta={"quote_verification": {"approved": 0, "unverified": 0}})
    store.append(build_trusted_turn("那河北省的十五计划呢？", ctx, result))
    resolution = resolve_followup("其中关于高新技术产业怎么说？", store.load())
    assert resolution["status"] == "fresh_research"
    assert resolution["inherited_planids"] == []
    assert resolution["requested_constraints"]["areas"] == ["河北省"]


def test_explicitly_named_verified_policy_outweighs_parent_city_scope_change():
    """Regression: viewed Shunqing policy must not vanish on a named follow-up."""
    docs = [
        {"planid": "SHUNQING", "title": "南充市顺庆区“十四五”消防事业发展规划",
         "area": "顺庆区", "fyp": "第十四个五年规划"},
        {"planid": "ZIYANG", "title": "资阳市“十四五”消防事业发展规划",
         "area": "资阳市", "fyp": "第十四个五年规划"},
        {"planid": "ZHEJIANG", "title": "浙江省消防事业发展“十四五”规划",
         "area": "浙江省", "fyp": "第十四个五年规划"},
    ]
    turns = [_turn("消防相关政策", *docs)]
    question = "南充市顺庆区‘十四五’消防事业发展规划主要讲了哪些内容？"
    # The route may have extracted the parent city; trusted title identity wins.
    resolution = resolve_followup(
        question, turns, SimpleNamespace(areas=("南充市",),
                                         fyps=("第十四个五年规划",)),
    )
    assert resolution["status"] == "continued"
    assert resolution["inherited_planids"] == ["SHUNQING"]
    assert resolution["requested_constraints"] is None
    assert resolution["retrieval_query"] == question
    assert "南充市顺庆区" in resolution["context_note"]


def test_named_policy_followup_does_not_resurrect_unrelated_prior_and_still_switches():
    docs = [
        {"planid": "SHUNQING", "title": "南充市顺庆区“十四五”消防事业发展规划",
         "area": "顺庆区", "fyp": "第十四个五年规划"},
        {"planid": "ZIYANG", "title": "资阳市“十四五”消防事业发展规划",
         "area": "资阳市", "fyp": "第十四个五年规划"},
    ]
    turns = [_turn("消防相关政策", *docs)]
    # A comparison adds the new city and retains only the named prior source.
    resolution = resolve_followup(
        "请比较南充市顺庆区‘十四五’消防事业发展规划与成都市消防政策",
        turns, SimpleNamespace(areas=("南充市", "成都市"), fyps=()),
    )
    assert resolution["status"] == "extended"
    assert resolution["inherited_planids"] == ["SHUNQING"]
    assert "成都市" in resolution["requested_constraints"]["areas"]
    assert "资阳市" not in resolution["requested_constraints"]["areas"]
    # Merely repeating a shared planning period is not enough to pick a
    # document when the user did not identify any of the previous sources.
    vague = resolve_followup(
        "其中十四五消防政策讲了什么？", turns,
        SimpleNamespace(areas=(), fyps=()),
    )
    assert vague["status"] == "ambiguous"
