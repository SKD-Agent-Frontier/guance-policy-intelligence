"""S0 mock unit tests; no live model or RAG."""

import pytest

from guance_agent.business.organize.scope import parse_scope

from .organize_fakes import mock_context


@pytest.mark.parametrize(("question", "topic"), [
    ("梳理十四五时期的科技创新政策", "科技创新"),
    ("梳理十四五时期北京市科技创新领域的相关政策", "科技创新"),
    ("请帮我整理一下有关数字经济的政策文件", "数字经济"),
    ("归纳第十四个五年规划期间的科技创新文献", "科技创新"),
    ("梳理十四五时期的专项规划中有关数字经济的政策", "数字经济"),
    ("分析科技创新，按人才培养、平台建设分类", "科技创新"),
    ("梳理科技创新" + "相关政策" * 100, "科技创新"),
    ("梳理中小企业科技创新政策", "中小企业科技创新"),
])
def test_mock_topic_extraction(question, topic):
    task, ctx = mock_context(question)
    assert parse_scope(task, ctx).topic == topic


@pytest.mark.parametrize(("words", "expected"), [
    ("总体规划", ["总体规划纲要"]), ("规划纲要", ["总体规划纲要"]),
    ("专项规划", ["专项规划"]), ("政府工作报告", ["政府工作报告"]),
    ("五年规划", ["规划文件"]), ("规划文件", ["规划文件"]), ("十四五", []),
])
def test_mock_type_mapping(words, expected):
    task, ctx = mock_context(f"梳理{words}中科技创新政策")
    assert parse_scope(task, ctx).plantypes == expected


def test_mock_categories_dimensions_and_fixed_targets():
    task, ctx = mock_context("梳理十四五时期北京市科技创新政策，按人才培养和平台建设及资金保障归类",
                             targets=["P000"], dimensions=["自定义维度", "期限"])
    scope = parse_scope(task, ctx)
    assert scope.categories == ["人才培养", "平台建设", "资金保障"]
    assert scope.dimensions == task.dimensions
    assert scope.areas == list(ctx.route.areas) and scope.fyps == list(ctx.route.fyps)
    assert scope.targets == ["P000"]
    assert scope.effective_area is scope.effective_fyp is None


def test_mock_empty_topic_falls_back():
    task, ctx = mock_context("请梳理相关政策")
    assert parse_scope(task, ctx).topic == ctx.route.sub_query
