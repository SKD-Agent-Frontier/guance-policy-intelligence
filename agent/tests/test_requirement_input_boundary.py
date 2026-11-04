"""Treat model-generated requirement fields as untrusted typed data."""
import pytest

from guance_agent.research_quality import _matching
from guance_agent.research_quality import requirements as quality_requirements
from guance_agent.research_requirements import (
    normalize_requirements,
    supports_metric_value,
    supports_requirement,
)
from guance_agent.research_targets import extract_targets
from guance_agent.routing import parse_fyps


def test_unknown_metric_needs_semantic_review_instead_of_raising():
    requirement = {"kind": "metric", "label": "床位密度", "metric": "床位密度"}

    assert not supports_requirement(requirement, "每千人床位数达到六张")


def test_unknown_metric_mapping_still_requires_a_typed_numeric_fact():
    requirement = {
        "kind": "metric", "label": "投资强度", "subject": "新材料产业",
        "metric": "investment_intensity",
    }

    assert not supports_metric_value(requirement, "该产业具有较强投资强度")
    assert supports_metric_value(requirement, "新材料产业固定资产投资强度达到每亩1.2万元")
    assert not supports_metric_value(requirement, "规模养殖达到5万头")
    assert supports_metric_value(requirement, "新材料产业投资强度达到80%")
    assert not supports_metric_value(requirement, "覆盖率达到80%")


def test_metric_value_does_not_cross_negation_or_neighboring_metric():
    requirement = {
        "kind": "metric", "label": "年销售收入", "subject": "新材料产业",
        "metric": "annual_revenue",
    }
    unrelated_value = "新材料产业年销售收入目标未公开，固定资产投资额达到3亿元。"
    same_clause_unrelated_value = "新材料产业年销售收入目标未公开且固定资产投资额达到3亿元。"

    assert not supports_requirement(requirement, unrelated_value)
    assert not supports_metric_value(requirement, unrelated_value)
    assert not supports_requirement(requirement, same_clause_unrelated_value)
    assert not supports_metric_value(requirement, same_clause_unrelated_value)
    assert supports_requirement(requirement, "新材料产业年销售收入达到3亿元。")
    assert supports_metric_value(requirement, "新材料产业年销售收入达到3亿元。")


def test_planned_analysis_label_can_be_matched_without_a_static_dimension_entry():
    assert _matching({"text": "两类产业的资源配置方式不同"}, "资源配置方式")
    assert not _matching({"text": "提高农业产值"}, "资源配置方式")


@pytest.mark.parametrize("field,value", [
    ("phase", []), ("phase", {}), ("operator", []), ("operator", {}),
    ("label", ["产能"]), ("subject", {"text": "装备制造"}),
    ("actionable", "false"),
])
def test_invalid_planner_field_does_not_crash_or_become_a_search_term(field, value):
    planned = {"kind": "metric", "label": "产能", "metric": "production_capacity",
               "phase": "evidence", "actionable": True, field: value}

    units = normalize_requirements("请核实装备制造发展情况", [planned])

    assert units
    assert all(isinstance(unit["label"], str) for unit in units)
    assert all(isinstance(unit.get("subject"), (str, type(None))) for unit in units)
    assert all(unit.get("phase") in {"evidence", "synthesis"} for unit in units)
    assert all(type(unit.get("actionable")) is bool for unit in units)
    assert not any(unit.get("kind") == "metric" for unit in units)


def test_incomplete_comparison_cannot_enter_evidence_matching():
    units = normalize_requirements("请比较两种发展路径", [
        {"kind": "comparison", "label": "发展路径", "left": "工业", "right": None},
    ])

    assert not any(unit["kind"] == "comparison" and not unit.get("right") for unit in units)
    assert any(unit["kind"] == "unresolved_requirement" for unit in units)


def test_subjects_exclude_prepositions_and_question_suffixes():
    subjects = extract_targets("比如在温室蔬菜和水产养殖方面是如何部署的？")
    assert set(subjects) == {"温室蔬菜", "水产养殖"}
    assert extract_targets("关于农业现代化具体有哪些量化目标？") == ("农业现代化",)


def test_numeric_request_without_metric_name_remains_verifiable():
    units = normalize_requirements("请给出具体量化指标并引用原文", [
        {"kind": "metric", "label": "具体量化指标", "metric": None},
        {"kind": "output_section", "label": "引用原文说明"},
    ])
    assert any(unit["kind"] == "numeric_fact" for unit in units)
    assert any(unit["kind"] == "source_citation" for unit in units)


def test_same_document_comparison_does_not_require_regional_differences():
    required = quality_requirements("该规划中工业与农业相比，支持方式有何不同？")
    assert "地区差异" not in required["dimensions"]
    assert required["needs_analysis"]


def test_historical_plan_periods_remain_distinct_in_a_comparison():
    assert parse_fyps("比较该县十五与八五、九五时期的发展基础") == (
        "第十个五年计划", "第八个五年计划", "第九个五年计划")
