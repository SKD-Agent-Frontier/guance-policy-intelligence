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
    assert store.load(), "first turn must persist trusted source identity"

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
    assert second_api.calls == 1
    prompt = second_api.messages[0][1]["content"]
    assert "多轮上下文" in prompt
    assert "TEST-001" in prompt


@pytest.mark.asyncio
async def test_ambiguous_followup_stops_before_model_call(env, monkeypatch):
    settings, origin = env
    settings = replace(
        settings, kb_backend="rag", rag_store_project="/offline-rag-test",
        llm_base_url="https://example.invalid/v1", llm_api_key="fake",
    )
    kb = MockKB(settings.mock_kb_path)
    first_task = origin.model_copy(update={"run_id": "run_first", "question": "农业"})
    first_api = ChatAPI()
    await _run_turn(settings, kb, first_api, monkeypatch, first_task)
    store = TrustedContextStore(settings.session_path, first_task.session_id)
    turns = store.load()
    if not turns or len(turns[-1].get("documents", [])) < 2:
        pytest.skip("mock corpus returned only one source; ambiguity needs >=2")

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
