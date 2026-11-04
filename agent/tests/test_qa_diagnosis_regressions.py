from types import SimpleNamespace

import pytest

from guance_agent.qa_controlled import (
    rank_topic_candidates,
    search_queries,
    verbatim_coverage_claims,
)
from guance_agent.research_quality import requirements
from guance_agent.research_requirements import normalize_requirements
from guance_agent.routing import parse_areas, parse_planids


@pytest.mark.parametrize("question", [
    "不同地方帮助大学毕业生找工作有哪些具体做法？请比较并给出政策原文依据。",
    "高校毕业生就业有哪些支持措施？",
    "大学毕业生毕业后找工作，哪些地方提供见习机会？",
])
def test_employment_queries_keep_population_and_subject(question):
    queries = search_queries(question)
    assert any("毕业生" in query and "就业" in query for query in queries)
    assert all(query not in {"措施", "政策措施", "共同点", "差异", "比较"} for query in queries)


@pytest.mark.parametrize("question", [
    "planid=12915《云南省2019年政府工作报告》中，要求再为企业降低成本多少？",
    "云南省2019年政府工作报告要求降低多少企业成本？",
])
def test_document_title_does_not_turn_fact_lookup_into_analysis(question):
    needed = requirements(question)
    assert not needed["needs_report"]
    assert not needed["needs_analysis"]


def test_current_region_switch_and_equal_sign_identifier():
    assert parse_areas("改看云南省2019年政府工作报告") == ("云南省",)
    assert parse_areas("改看从江县就业政策") == ("从江县",)
    assert parse_planids("planid=12915和planid：18563") == ("12915", "18563")


def test_explicit_analysis_command_survives_source_title_masking():
    needed = requirements("请根据《云南省2019年政府工作报告》撰写一份分析报告。")
    assert needed["needs_report"]
    assert needed["needs_analysis"]


def test_report_reference_and_report_deliverable_are_distinct():
    needed = requirements("改看《云南省2019年政府工作报告》：报告要求再为企业降低成本多少？")
    assert not needed["needs_report"]
    assert not needed["needs_analysis"]
    assert requirements("请给我一份报告，列举企业降成本措施")["needs_report"]


def test_document_identity_is_not_a_numeric_fact_subject():
    units = normalize_requirements(
        "改看《云南省2019年政府工作报告》：报告要求再为企业降低成本多少？",
        [{"kind": "numeric_fact", "label": "企业降成本的金额", "subject": "云南省2019年政府工作报告"}],
    )
    assert next(unit for unit in units if unit["label"] == "企业降成本的金额")["subject"] is None


def test_multi_aspect_query_does_not_lose_digital_subject():
    queries = search_queries("各地支持数字产业和数字化发展的资金、人才、基础设施举措有哪些？")
    assert any("数字产业" in query or "数字化" in query for query in queries)


def test_known_document_query_does_not_rank_title_ahead_of_task():
    queries = search_queries("planid=12915《云南省2019年政府工作报告》中，要求再为企业降低成本多少？")
    assert all("planid" not in query and "政府工作报告" not in query for query in queries)
    assert any("成本" in query for query in queries)


def test_generic_policy_dimension_cannot_repair_employment_with_audit_fact():
    task = SimpleNamespace(question="各地在促进高校毕业生就业方面有哪些政策措施，可以如何比较？")
    evidence = [{"chunk_id": "audit", "text": "按季度提出政策跟踪审计重点事项，推动重大政策措施更好落实。"}]
    assert verbatim_coverage_claims(SimpleNamespace(claims=[]), task, evidence, None) == []


def test_document_diversity_does_not_displace_relevant_evidence_with_audit():
    hits = [
        {"chunk_id": "jobs-1", "planid": "jobs", "content": "高校毕业生就业招聘会"},
        {"chunk_id": "audit", "planid": "audit", "content": "政策跟踪审计措施"},
        {"chunk_id": "jobs-2", "planid": "jobs", "content": "高校毕业生就业见习和创业补贴"},
    ]
    ranked = rank_topic_candidates(hits, "高校毕业生就业有哪些支持措施？")
    assert [hit["chunk_id"] for hit in ranked] == ["jobs-1", "jobs-2", "audit"]
    assert len(hits) == len(ranked)
