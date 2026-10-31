"""Comparison relevance checks, independent of the school LLM and private corpus."""
from types import SimpleNamespace

from guance_agent.business.compare import relevance
from guance_agent.business.compare.evidence import Collection, EvidenceUnit
from guance_agent.business.compare.models import ComparisonPlan, GeneratedComparison
from guance_agent.business.compare.result import build_outcome


QUESTION = "比较浙江和北京十四五数字经济发展目标的异同"


def test_recognizes_explicit_research_subject_without_splitting():
    assert relevance.explicit_comparison_topic(QUESTION) == "数字经济"
    assert relevance.explicit_comparison_topic("比较浙江和北京十四五数字经济政策工具的差异") == "数字经济"
    assert relevance.explicit_comparison_topic("比较两项人才政策") is None


def test_unrelated_plan_titles_are_not_research_subjects_even_if_body_mentions_topic():
    objects = [
        {"planid": "24037", "title": "浙江省消防事业发展“十四五”规划"},
        {"planid": "26147", "title": "北京市“十四五”时期高精尖产业发展规划"},
    ]
    assert relevance.topic_mismatched_objects(objects, "数字经济", ["24037", "26147"]) == [
        "24037《浙江省消防事业发展“十四五”规划》",
        "26147《北京市“十四五”时期高精尖产业发展规划》",
    ]


def test_subject_only_without_goal_evidence_not_accepted_as_goal_fact():
    topic = "数字经济"
    title = "浙江省数字经济发展“十四五”规划"
    unrelated = EvidenceUnit("e0", "z", "c0",
                             "全面加强机关后勤与消防安全检查。", "行政保障")
    assert not relevance.evidence_on_topic(unrelated, "发展目标", topic, title)
    broad = EvidenceUnit("e1", "z", "c1", "开展数字经济培训工作。", "建设任务")
    assert not relevance.evidence_on_topic(broad, "发展目标", topic, title)
    goal = EvidenceUnit("e2", "z", "c2",
                        "到2025年数字经济核心产业增加值占GDP比重不断提升。", "发展目标")
    assert relevance.evidence_on_topic(goal, "发展目标", topic, title)
    # Topic in body doesn't override a policy with the wrong research subject.
    assert not relevance.evidence_on_topic(goal, "发展目标", topic, "浙江消防事业十四五规划")


def _matrix(fact_text_a, fact_text_b):
    task = SimpleNamespace(question=QUESTION, targets=["z", "b"])
    plan = ComparisonPlan(dimensions=["发展目标"], queries=["数字经济"], evaluation_criteria=[])
    collection = Collection(
        objects=[
            {"planid": "z", "title": "浙江省数字经济发展“十四五”规划",
             "fyp": "十四五", "area": "浙江省", "plantype_label": "专项规划"},
            {"planid": "b", "title": "北京市数字经济发展“十四五”规划",
             "fyp": "十四五", "area": "北京市", "plantype_label": "专项规划"},
        ],
        units=[
            EvidenceUnit("e0", "z", "z0", fact_text_a, "发展目标"),
            EvidenceUnit("e1", "b", "b0", fact_text_b, "发展目标"),
        ],
    )
    selected = GeneratedComparison.model_validate({"facts": [
        {"planid": "z", "dimension": "发展目标", "evidence_ids": ["e0"]},
        {"planid": "b", "dimension": "发展目标", "evidence_ids": ["e1"]},
    ], "differences": [], "scheme_analysis": []})
    return build_outcome(task, plan, collection, selected, scheme=False)


def test_irrelevant_quote_dropped_and_task_completion_is_partial():
    result = _matrix("加强消防宣传和火灾防控，减少死亡人数。",
                     "到2025年数字经济产业增加值占比达到目标。")
    row = result.structured["matrix"][0]
    assert row["cells"][0]["status"] == "insufficient"
    assert row["cells"][1]["status"] == "found"
    assert result.structured["task_completion"]["status"] == "partial"
    assert result.structured["task_completion"]["unsupported_dimensions"] == ["发展目标"]
    assert row["difference"]["status"] == "insufficient"
    assert result.insufficient


def test_both_relevant_goals_retained_without_claiming_completed_comparison():
    result = _matrix("到2025年数字经济产业规模达到既定水平。",
                     "到2025年数字经济产业增加值占比持续提升。")
    row = result.structured["matrix"][0]
    assert [x["status"] for x in row["cells"]] == ["found", "found"]
    assert len(result.claims) == 2
    # Even with facts from both sides, model hasn't produced substantiated difference.
    assert result.structured["task_completion"]["status"] == "partial"
    assert result.structured["task_completion"]["supported_dimensions"] == []


def test_legacy_comparison_without_unambiguous_topic_not_narrowed():
    unit = EvidenceUnit("e1", "p1", "chunk1", "人才保障措施。")
    assert relevance.explicit_comparison_topic("比较两份人才政策") is None
    assert relevance.evidence_on_topic(unit, "发展目标", "数字经济", "人才政策") is False
