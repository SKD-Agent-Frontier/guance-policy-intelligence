"""路由接线：按对象限定检索范围，逐对象留证据，失败逐对象记录。

注意：这里用 MockKB 验证控制流程；真实运行库的验收见报告，不能用本文件冒充。
"""
import asyncio
import json
from dataclasses import replace

from guance_agent.orchestrator import run_task
from guance_agent.runner import ExecOutcome
from guance_agent.runtime import Runtime


def _configured(env):
    return replace(env[0], llm_base_url="https://example.invalid/v1", llm_api_key="offline-key")


def _stub(monkeypatch, seen):
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda s: object())
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda s, c: object())
    monkeypatch.setattr("guance_agent.orchestrator.build_agent", lambda s, m: object())

    async def fake_execute(agent, task, ctx, rt, deadline=None):
        # 记录本次真正生效的范围，以及该范围下后端实际返回的篇目
        hits = await asyncio.to_thread(ctx.kb.search, "农业", None, None, 5,
                                       planids=ctx.scope)
        seen.append({"scope": ctx.scope, "planids": sorted({h.planid for h in hits})})
        body = json.dumps({"answer": "ok", "claims": [], "insufficient": bool(not hits)})
        return ExecOutcome(body, None)

    monkeypatch.setattr("guance_agent.orchestrator.execute", fake_execute)


async def test_single_object_scopes_retrieval_before_search(env, monkeypatch):
    s, base = _configured(env), env[1]
    seen = []
    _stub(monkeypatch, seen)
    task = base.model_copy(update={"question": "planid TEST-001 的农业重点是什么？"})
    result = await run_task(task, s, Runtime(s, task))
    assert [o["planid"] for o in result.route["objects"]] == ["TEST-001"]
    assert seen[0]["scope"] == ("TEST-001",)
    assert seen[0]["planids"] == ["TEST-001"]


async def test_multi_object_retrieves_each_object_separately(env, monkeypatch):
    s, base = _configured(env), env[1]
    seen = []
    _stub(monkeypatch, seen)
    task = base.model_copy(update={"question": "比较 planid TEST-001 与 planid TEST-002 的农业重点。"})
    result = await run_task(task, s, Runtime(s, task))
    assert [o["planid"] for o in result.route["objects"]] == ["TEST-001", "TEST-002"]
    # 两次独立检索，各自只看到自己那篇，不是合并后一次全局 Top-K
    assert [s_["scope"] for s_ in seen] == [("TEST-001",), ("TEST-002",)]
    assert [s_["planids"] for s_ in seen] == [["TEST-001"], ["TEST-002"]]


async def test_open_topic_keeps_global_scope(env, monkeypatch):
    s, base = _configured(env), env[1]
    seen = []
    _stub(monkeypatch, seen)
    task = base.model_copy(update={"question": "农业有哪些政策要求？"})
    result = await run_task(task, s, Runtime(s, task))
    assert result.route["kind"] == "open"
    assert seen[0]["scope"] is None
    assert len(seen[0]["planids"]) > 1        # 开放主题确实跨多篇


async def test_invalid_planid_never_falls_back_to_global(env, monkeypatch):
    s, base = _configured(env), env[1]
    seen = []
    _stub(monkeypatch, seen)
    task = base.model_copy(update={"question": "planid TEST-999 的农业重点是什么？"})
    result = await run_task(task, s, Runtime(s, task))
    assert seen[0]["scope"] == ("TEST-999",)
    assert seen[0]["planids"] == []           # 编号不存在时检索范围为空，没有退回全库
    assert result.route["objects"][0]["status"] != "ok"
    assert result.evidence_refs == []


async def test_object_without_body_is_reported_not_faked(env, monkeypatch):
    s, base = _configured(env), env[1]
    seen = []
    _stub(monkeypatch, seen)
    task = base.model_copy(update={"question": "planid TEST-003 的农业重点是什么？"})
    result = await run_task(task, s, Runtime(s, task))
    assert seen[0]["planids"] == []           # TEST-003 没有正文，取不到片段
    status = result.route["objects"][0]["status"]
    assert status != "ok"
    assert any("未取得证据" in w for w in result.warnings) or result.evidence_status == "insufficient"


async def test_over_long_question_is_refused_without_retrieval(env, monkeypatch):
    s, base = _configured(env), env[1]
    seen = []
    _stub(monkeypatch, seen)
    task = base.model_copy(update={"question": "分析" * 300})
    result = await run_task(task, s, Runtime(s, task))
    assert result.route["blocked"] == "query_too_long"
    assert seen == []                          # 明确拒绝，没有假装检索过
    assert "没有检索" in result.answer


async def test_area_and_period_filters_use_normalised_period(env, monkeypatch):
    """回归此前发现的期号口径问题：十四五 必须规范成第十四个五年规划。"""
    s, base = _configured(env), env[1]
    seen = []
    _stub(monkeypatch, seen)
    task = base.model_copy(update={"question": "十四五期间甲市农业有哪些要求？"})
    result = await run_task(task, s, Runtime(s, task))
    assert result.route["fyps"] == ["第十四个五年规划"]
    assert result.route["areas"] == ["甲市"]


# ---------------- 回归：遥测、范围约束、证据隔离 ----------------

def test_usage_telemetry_reports_tokens_per_call():
    """回归：成功路径曾漏传 telemetry，导致 meta.llm 恒为空。"""
    import types

    from guance_agent.runner import usage_telemetry

    def resp(i, o):
        return types.SimpleNamespace(usage=types.SimpleNamespace(input_tokens=i, output_tokens=o))

    result = types.SimpleNamespace(raw_responses=[resp(1220, 63), resp(18519, 281)])
    data = usage_telemetry(result)
    assert data["model_calls"] == 2
    assert data["input_tokens"] == 19739 and data["output_tokens"] == 344
    assert data["per_call"] == [{"input": 1220, "output": 63}, {"input": 18519, "output": 281}]


def test_kb_read_is_blocked_outside_route_scope():
    from guance_agent.context import RunContext
    from guance_agent.tools import out_of_scope

    ctx = RunContext(None, None, "r", "s")
    ctx.scope = ("TEST-001",)
    assert out_of_scope(ctx, "TEST-001") is None      # 范围内可读
    assert "不在本轮取证范围" in out_of_scope(ctx, "TEST-002")
    ctx.scope = None
    assert out_of_scope(ctx, "TEST-002") is None      # 开放主题不限制


async def test_multi_object_claims_cannot_borrow_other_object_evidence(env, monkeypatch):
    """每个对象的结论只能引用来自该对象的证据。"""
    from guance_agent.tools import _call

    s, task = _configured(env), env[1]
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda s: object())
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda s, c: object())
    monkeypatch.setattr("guance_agent.orchestrator.build_agent", lambda s, m: object())

    async def fake_execute(agent, task, ctx, rt, deadline=None):
        await _call(ctx, "kb_search", ctx.kb.search, "农业", None, None, 3, planids=ctx.scope)
        own = [cid for cid in ctx.evidence.ids()
               if ctx.evidence.get(cid).planid in ctx.scope]
        foreign = [cid for cid in ctx.evidence.ids()
                   if ctx.evidence.get(cid).planid not in ctx.scope]
        claims = [{"text": "来自本对象", "chunk_ids": own[:1], "quotes": []}]
        if foreign:   # 故意引用别的对象的证据
            claims.append({"text": "借用他人", "chunk_ids": foreign[:1], "quotes": []})
        return ExecOutcome(json.dumps({"answer": "ok", "claims": claims, "insufficient": False}), None)

    monkeypatch.setattr("guance_agent.orchestrator.execute", fake_execute)
    q = task.model_copy(update={"question": "比较 planid TEST-001 与 planid TEST-002 的农业重点。"})
    result = await run_task(q, s, Runtime(s, q))
    for ref in result.evidence_refs:
        assert ref.planid in {"TEST-001", "TEST-002"}
    # 合并后的每条结论，其 chunk 必须全部属于同一篇
    for claim in result.claims:
        planids = {result.evidence_refs[[r.chunk_id for r in result.evidence_refs].index(c)].planid
                   for c in claim.chunk_ids if c in [r.chunk_id for r in result.evidence_refs]}
        assert len(planids) <= 1, claim.text


# ---------------- 状态一致性：completed 不得与 INTERNAL 并存 ----------------

async def test_unknown_task_type_is_rejected_explicitly(env, monkeypatch):
    """未知 task_type 不能静默走 QA，必须显式失败。"""
    s, base = _configured(env), env[1]
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda s: object())
    task = base.model_copy(update={"question": "随便问问", "task_type": "compare"})
    result = await run_task(task, s, Runtime(s, task))
    assert result.status == "failed"
    assert result.error is not None and result.error.code == "INVALID_INPUT"
    assert "compare" in result.error.message


async def test_blocked_route_is_completed_without_internal_error(env, monkeypatch):
    """超长问题走 blocked 分支：是正常结束，不能带 INTERNAL。"""
    s, base = _configured(env), env[1]
    task = base.model_copy(update={"question": "分析" * 300})
    result = await run_task(task, s, Runtime(s, task))
    assert result.route["blocked"] == "query_too_long"
    assert result.status == "completed"
    assert result.error is None, result.error
    assert "没有检索" in result.answer


async def test_no_evidence_objects_is_completed_without_internal_error(env, monkeypatch):
    """所有对象都没取到证据：completed + insufficient，且不带 INTERNAL。"""
    s, base = _configured(env), env[1]
    monkeypatch.setattr("guance_agent.runtime.get_client", lambda s: object())
    monkeypatch.setattr("guance_agent.orchestrator.chat_model", lambda s, c: object())
    monkeypatch.setattr("guance_agent.orchestrator.build_agent", lambda s, m: object())

    async def empty_execute(agent, task, ctx, rt, deadline=None):
        return ExecOutcome(json.dumps({"answer": "没有", "claims": [], "insufficient": True}), None)

    monkeypatch.setattr("guance_agent.orchestrator.execute", empty_execute)
    q = base.model_copy(update={"question": "planid TEST-999 的农业重点是什么？"})
    result = await run_task(q, s, Runtime(s, q))
    assert result.status == "completed"
    assert result.error is None, result.error
    assert result.evidence_status == "insufficient"
