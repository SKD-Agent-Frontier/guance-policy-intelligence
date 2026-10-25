"""要素提取：缺失维度判定与结构化产物装配。

核心是区分「该篇正文里没有」和「根本没检索到」——把两者混为一谈会误导政策判断。
"""
import json
import types
from dataclasses import replace

from guance_agent.config import ROOT, Settings
from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.orchestrator import _attach, _documents, _missing_dimensions
from guance_agent.output import build_result
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import Claim, RunResult, TaskRequest

CID = "chunk_c5481643e7c67d653e29160c30719104"


def task(**kw):
    base = dict(run_id="r", session_id="s", question="q", created_at="x")
    return TaskRequest(**{**base, **kw})


def result_with(claims, tool_calls, refs=()):
    return RunResult(run_id="r", session_id="s", status="completed",
                     evidence_status="verified", answer="ok", claims=list(claims),
                     evidence_refs=list(refs), dropped_chunk_ids=[], error=None,
                     meta={"tool_calls": list(tool_calls)})


def claim(dimension, text="要素"):
    return Claim(text=text, chunk_ids=[CID], quotes=[], dimension=dimension)


def test_covered_dimension_is_not_missing():
    r = result_with([claim("量化目标")], [{"status": "ok"}])
    missing = _missing_dimensions(r, task(task_type="extract"))
    assert [m["dimension"] for m in missing] == ["适用对象", "政策工具", "责任主体", "期限", "资金规模"]
    assert {m["reason"] for m in missing} == {"not_in_document"}


def test_all_failed_tools_mean_not_retrieved():
    """一次读取都没成功时，只能说「没检索到」，不能说「该篇没有」。"""
    r = result_with([], [{"status": "empty"}, {"status": "not_found"}])
    missing = _missing_dimensions(r, task(task_type="extract"))
    assert missing and {m["reason"] for m in missing} == {"not_retrieved"}


def test_custom_dimensions_replace_defaults():
    r = result_with([claim("人才培养")], [{"status": "ok"}])
    missing = _missing_dimensions(r, task(task_type="extract",
                                          dimensions=["人才培养", "财政支持"]))
    assert missing == [{"dimension": "财政支持", "reason": "not_in_document"}]


def test_documents_are_deduplicated_by_planid():
    from guance_agent.schemas import EvidenceRef

    def ref(cid, planid, title):
        return EvidenceRef(planid=planid, chunk_id=cid, title=title, area="甲市", fyp=None,
                           admin_level=None, plantype_label=None, section_idx=0,
                           section_path="s", char_start=0, char_end=1, source="kb_search")

    refs = [ref("c1", "p1", "篇一"), ref("c2", "p1", "篇一"), ref("c3", "p2", "篇二")]
    docs = _documents(result_with([], [], refs))
    assert [d["planid"] for d in docs] == ["p1", "p2"]


def test_attach_only_fills_structured_for_extract():
    r = result_with([claim("量化目标", "培训率100%")], [{"status": "ok"}])
    qa = _attach(r.model_copy(deep=True), task(task_type="qa"))
    assert qa.task_type == "qa" and qa.structured is None

    ex = _attach(r.model_copy(deep=True), task(task_type="extract"))
    assert ex.structured["elements"][0]["dimension"] == "量化目标"
    assert ex.structured["dimensions"] == list(
        ("适用对象", "政策工具", "量化目标", "责任主体", "期限", "资金规模"))


async def test_extract_prompt_is_applied_and_dimension_survives_validation():
    """走一遍完整解析：dimension 必须从模型输出一直保留到 structured。"""
    from guance_agent.agent import effective_prompt, prompt_version

    assert "政策要素提取助手" in effective_prompt(False, "extract")
    assert prompt_version(False, "extract") != prompt_version(False, "qa")

    s = Settings(session_path=ROOT / "data/t.sqlite", run_log=ROOT / "data/t.jsonl")
    kb = get_kb(s)
    ctx = RunContext(s, kb, "r", "s")
    from guance_agent.tools import _call
    await _call(ctx, "kb_search", ctx.kb.search, "农业", None, None, 1)
    cid = ctx.evidence.ids()[0]
    raw = json.dumps({"answer": "已提取", "claims": [
        {"dimension": "量化目标", "text": "农业重点", "chunk_ids": [cid], "quotes": []}]})
    result = build_result(task(task_type="extract"), ExecOutcome(raw, None), ctx, {})
    assert result.claims[0].dimension == "量化目标"


# ---------------- 一次性提取链路（程序检索 + 单次模型调用） ----------------

class StubRuntime:
    """只提供 _run_extract 需要的 client / meta，避免真调模型。"""

    def __init__(self, content):
        self.meta = {}
        async def create(**_kw):
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))])
        self.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


async def test_one_shot_extract_uses_program_retrieval(tmp_path):
    """检索与读取由程序做完，模型只被调用一次——这是 180 秒预算下的唯一可行形态。"""
    from guance_agent.context import RunContext
    from guance_agent.extract import retrieve
    from guance_agent.kb import get_kb
    from guance_agent.orchestrator import _run_extract

    s = replace(Settings(session_path=tmp_path / "s.sqlite", run_log=tmp_path / "r.jsonl"),
                llm_base_url="https://x.invalid/v1", llm_api_key="k")
    ctx = RunContext(s, get_kb(s), "r", "s")
    blocks, found = await retrieve(ctx, "农业", 3)
    assert found["status"] == "ok" and blocks, "程序侧检索必须能拿到原文"
    chunk_id = blocks[0]["hit"]["chunk_id"]

    payload = json.dumps({"answer": "覆盖 1 个维度", "claims": [
        {"dimension": "量化目标", "text": "农业重点",
         "chunk_ids": [chunk_id], "quotes": []}]})
    outcome = await _run_extract(task(question="农业", task_type="extract"),
                                 s, ctx, StubRuntime(payload))
    assert outcome.error_code is None
    assert json.loads(outcome.final_output)["claims"][0]["dimension"] == "量化目标"


async def test_retrieval_failure_is_reported_not_faked(tmp_path):
    """检索不到时直接返回失败状态，不让模型凭空编。"""
    from guance_agent.context import RunContext
    from guance_agent.extract import retrieve
    from guance_agent.kb import get_kb

    s = Settings(session_path=tmp_path / "s.sqlite", run_log=tmp_path / "r.jsonl")
    ctx = RunContext(s, get_kb(s), "r", "s")
    blocks, found = await retrieve(ctx, "这个词库里绝对不存在", 3)
    assert blocks == []
    assert found["status"] in {"empty", "not_found", "no_text"}
