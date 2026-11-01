"""Intent inference must change execution, not only label questions."""
from guance_agent.research_intent import classify_intent

PRIOR = {
    "verified_facts": [{"text": "政策提出农业调整措施", "chunk_ids": ["c1"], "quotes": ["农业调整"]}],
    "evidence_status": "verified",
    "evidence_refs": [{"chunk_id": "c1", "planid": "2561"}],
    "quote_verification": {"checked": 1, "approved": 1, "rejected": 0, "unverified": 0},
    "question": "沧州市十五计划产业结构调整措施",
}


def test_first_turn_fact_lookup_stays_on_fast_path():
    intent = classify_intent("沧州市“十五”计划提出的产业布局总体设想是什么？")
    assert intent.primary == "fact_lookup"
    assert not intent.need_report


def test_measure_question_is_organized_not_a_raw_fact_list():
    intent = classify_intent("沧州市“十五”计划中产业结构调整的主要措施有哪些？")
    assert intent.primary == "organize"
    assert intent.need_report


def test_summarize_uses_prior_verified_research_without_retrieval():
    intent = classify_intent("总结以上内容。", PRIOR)
    assert intent.primary == "summarize"
    assert intent.use_history
    assert not intent.need_report


def test_analyze_followup_uses_prior_research():
    intent = classify_intent("这些措施反映出怎样的发展思路？", PRIOR)
    assert intent.primary == "analyze"
    assert intent.use_history
    assert intent.need_report


def test_multi_document_induction_is_not_misclassified_as_simple_summary():
    intent = classify_intent(
        "请归纳海西州与成都市双流区在数字基础设施建设方面的共同做法，并说明差异。")
    assert intent.primary == "compare"
    assert intent.need_report


def test_comparison_without_a_new_object_requests_clarification():
    intent = classify_intent("那与其他地区相比呢？", PRIOR)
    assert intent.primary == "compare"
    assert intent.compare_without_new_object


def test_summary_only_instruction_never_retrieves_new_policy():
    intent = classify_intent("只总结已有证据，不要再查新文献。", PRIOR)
    assert intent.primary == "summarize"
    assert intent.use_history
