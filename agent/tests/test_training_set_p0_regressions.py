"""Delivery-regression guards from real training conversations."""
from types import SimpleNamespace

from guance_agent.qa_context import build_trusted_turn, resolve_followup
from guance_agent.qa_controlled import search_queries
from guance_agent.research_intent import classify_intent, requires_fresh_evidence
from guance_agent.research_targets import extract_targets, mentions_subject
from guance_agent.routing import parse_areas


def test_preposition_does_not_corrupt_region():
    assert parse_areas("从温州市的医疗工作安排来看") == ("温州市",)
    assert parse_areas("从江县的产业部署") == ("从江县",)


def test_comparison_advantage_is_not_cross_region_comparison():
    intent = classify_intent("密云县十一五规划的发展基础和比较优势有哪些？")
    assert intent.primary != "compare"


def test_named_subjects_drive_retrieval():
    q = "关于铝型材和不锈钢加工业的具体发展目标是什么？请说明量化指标"
    assert extract_targets(q) == ("铝型材", "不锈钢加工业")
    assert {"铝型材", "不锈钢加工业"} <= set(search_queries(q))
    assert mentions_subject("发展不锈钢加工，产量达到15万吨", "不锈钢加工业")
    assert extract_targets("特别是大棚果和奶牛养殖有哪些具体安排？") == ("大棚果", "奶牛养殖")


def test_new_facts_bypass_historical_only_path():
    prior = {"verified_facts": [{"text": "产业发展", "chunk_ids": ["c1"], "quotes": ["产业"]}],
             "evidence_refs": [{"chunk_id": "c1"}],
             "quote_verification": {"approved": 1, "unverified": 0}}
    assert classify_intent("总结以上内容。", prior).use_history
    for question in ("其中铝型材和不锈钢的量化指标是什么？请引用原文",
                     "这些支持方式相比有什么不同？特别是大棚果和奶牛养殖"):
        assert requires_fresh_evidence(question)
        assert not classify_intent(question, prior).use_history


def test_scope_survives_failed_continuation_but_not_scope_change():
    doc = {"planid": "1831", "title": "临朐县十一五规划",
           "area": "临朐县", "fyp": "第十一个五年规划"}
    ctx = SimpleNamespace(evidence=None, qa_context_resolution={
        "status": "continued", "documents": [doc], "inherited_planids": ["1831"]})
    no_facts = SimpleNamespace(status="completed", error=None, claims=[],
                               evidence_refs=[], evidence_status="insufficient",
                               meta={"quote_verification": {"approved": 0, "unverified": 0}})
    failed = build_trusted_turn("这些指标是什么？", ctx, no_facts)
    assert failed["documents"] == []
    assert failed["context_documents"] == [doc]
    continued = resolve_followup("那大棚果有什么目标？", [failed])
    assert continued["status"] == "continued"
    assert continued["inherited_planids"] == ["1831"]
    ctx.qa_context_resolution = {"status": "scope_changed", "documents": [doc]}
    stopped = build_trusted_turn("那河北省的政策呢？", ctx, no_facts)
    assert resolve_followup("其中有何举措？", [stopped])["status"] == "ambiguous"
