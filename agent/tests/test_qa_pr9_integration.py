"""PR #9 端到端 Mock 回归：在真实编排路径上验证身份列举与格式恢复。"""
import json
from dataclasses import replace

import pytest

from guance_agent.agent import build_agent as real_build_agent
from guance_agent.kb import MockKB
from guance_agent.orchestrator import run_task
from guance_agent.runner import ExecOutcome
from guance_agent.runtime import Runtime

from .fake_model import FakeModel, tool


def _configured(env):
    return replace(env[0], llm_base_url="https://example.invalid/v1", llm_api_key="offline-key")


def _stub_model(monkeypatch, script=None):
    """离线模型：不访问网络；给了脚本时用真实 Agent 循环跑假模型。"""
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda s: object())
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda s, c: object())
    if script is not None:
        model = FakeModel(script)
        monkeypatch.setattr("guance_agent.orchestrator.build_agent",
                            lambda s, m: real_build_agent(s, model))
    else:
        # 这些用例用 stub execute 直接给结果，不需要真正的 Agent 实例
        monkeypatch.setattr("guance_agent.orchestrator.build_agent", lambda s, m: object())


@pytest.mark.asyncio
async def test_qa_listing_uses_metadata_identity_end_to_end(env, monkeypatch):
    """列举本轮真实检索文献：编号/篇名来自工具返回元数据，不被数字核验误删。"""
    s, task = _configured(env), env[1]
    hits = MockKB(s.mock_kb_path).search("农业", None, None, 2)
    assert len(hits) == 2
    lines, claims = ["本轮实际检索到的政策如下："], []
    for index, hit in enumerate(hits, 1):
        lines.append(f"{index}. 《{hit.title}》（planid={hit.planid}，{hit.area}，{hit.fyp}）")
        claims.append({"text": f"《{hit.title}》与本次检索主题相关。",
                       "chunk_ids": [hit.chunk_id], "quotes": []})
    final = json.dumps({"answer": "\n".join(lines), "claims": claims, "insufficient": False},
                       ensure_ascii=False)
    _stub_model(monkeypatch, [[tool("kb_search", query="农业", area=None, fyp=None, top_k=2)],
                              final])
    result = await run_task(task, s, Runtime(s, task))
    assert result.status == "completed", result.error
    assert result.evidence_status == "verified"
    assert len(result.claims) == 2
    assert result.meta.get("number_verification") is None
    assert all(hit.planid in result.answer for hit in hits)


@pytest.mark.asyncio
async def test_qa_output_parse_recovered_without_retrieval(env, monkeypatch):
    """一次受预算的格式修复可恢复 QA，且不重新检索（tool_calls 保持为空）。"""
    s, task = _configured(env), env[1]
    _stub_model(monkeypatch)
    calls = []

    async def broken_execute(agent, task, ctx, rt, deadline=None):
        return ExecOutcome('{"answer":"已恢复", "claims": }', None)

    async def fake_complete(self, messages, *, max_tokens, timeout):
        calls.append(messages)
        return json.dumps({"answer": "已恢复", "claims": [], "insufficient": True},
                          ensure_ascii=False)

    monkeypatch.setattr("guance_agent.orchestrator.execute", broken_execute)
    monkeypatch.setattr(Runtime, "complete_text", fake_complete)
    result = await run_task(task, s, Runtime(s, task))
    assert result.status == "completed" and result.error is None
    assert result.answer.startswith("已恢复")
    assert result.meta["output_parse"]["repair"] == "attempted"
    assert len(calls) == 1
    assert result.meta.get("tool_calls") == []


@pytest.mark.asyncio
async def test_qa_output_parse_fails_closed_after_one_repair(env, monkeypatch):
    s, task = _configured(env), env[1]
    _stub_model(monkeypatch)
    calls = []

    async def broken_execute(agent, task, ctx, rt, deadline=None):
        return ExecOutcome('{"answer":"坏", "claims": }', None)

    async def fake_complete(self, messages, *, max_tokens, timeout):
        calls.append(messages)
        return '{"answer":"还是坏的"'

    monkeypatch.setattr("guance_agent.orchestrator.execute", broken_execute)
    monkeypatch.setattr(Runtime, "complete_text", fake_complete)
    result = await run_task(task, s, Runtime(s, task))
    assert result.status == "failed" and result.error.code == "OUTPUT_PARSE"
    assert result.meta["output_parse"]["repair"] == "attempted"
    assert result.meta["output_parse"]["repair_result"] == "incomplete_json"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_objects_route_repairs_at_most_once_per_run(env, monkeypatch):
    """多对象路径整轮最多一次格式修复，且 meta 聚合每个对象的解析记录。"""
    s = _configured(env)
    task = env[1].model_copy(update={"targets": ["TEST-001", "TEST-002"]})
    _stub_model(monkeypatch)
    calls = []
    broken = '{"answer":"对象结果", "claims":[], "insufficient": }'
    recovered = json.dumps({"answer": "对象结果", "claims": [], "insufficient": True},
                           ensure_ascii=False)

    async def broken_execute(agent, task, ctx, rt, deadline=None):
        return ExecOutcome(broken, None)

    async def fake_complete(self, messages, *, max_tokens, timeout):
        calls.append(messages)
        return recovered

    monkeypatch.setattr("guance_agent.orchestrator.execute", broken_execute)
    monkeypatch.setattr(Runtime, "complete_text", fake_complete)
    result = await run_task(task, s, Runtime(s, task))
    assert len(calls) == 1
    meta = result.meta["output_parse"]
    assert isinstance(meta.get("objects"), list) and len(meta["objects"]) == 2
    assert meta["repair"] == "attempted"


@pytest.mark.asyncio
async def test_objects_route_keeps_each_object_parse_record(env, monkeypatch):
    """一个对象正常、另一个对象修复时，两个对象的解析记录都要保留。"""
    s = _configured(env)
    task = env[1].model_copy(update={"targets": ["TEST-001", "TEST-002"]})
    _stub_model(monkeypatch)
    calls, seen = [], {"count": 0}
    valid = json.dumps({"answer": "正常对象", "claims": [], "insufficient": True},
                       ensure_ascii=False)
    broken = '{"answer":"修复对象", "claims":[], "insufficient": }'
    recovered = json.dumps({"answer": "修复对象", "claims": [], "insufficient": True},
                           ensure_ascii=False)

    async def execute(agent, task, ctx, rt, deadline=None):
        seen["count"] += 1
        return ExecOutcome(valid if seen["count"] == 1 else broken, None)

    async def fake_complete(self, messages, *, max_tokens, timeout):
        calls.append(messages)
        return recovered

    monkeypatch.setattr("guance_agent.orchestrator.execute", execute)
    monkeypatch.setattr(Runtime, "complete_text", fake_complete)
    result = await run_task(task, s, Runtime(s, task))
    meta = result.meta["output_parse"]
    assert len(calls) == 1
    assert len(meta["objects"]) == 2
    assert meta["objects"][0]["classification"] == "ok"
    assert meta["objects"][1]["repair"] == "attempted"
    assert meta["repair"] == "attempted"
