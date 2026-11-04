"""Intent inference must change execution, not only label questions."""
from guance_agent.research_intent import classify_intent, intent_payload

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


def test_comparison_followup_expands_history_unless_user_replaces_scope():
    expanded = classify_intent("与其他区县比较", PRIOR)
    replaced = classify_intent("改为只看石家庄市", PRIOR)

    assert expanded.use_history
    assert expanded.comparison_expansion
    assert not expanded.replace_scope
    assert intent_payload(expanded)["comparison_expansion"]
    assert replaced.replace_scope


def test_summary_only_instruction_never_retrieves_new_policy():
    intent = classify_intent("只总结已有证据，不要再查新文献。", PRIOR)
    assert intent.primary == "summarize"
    assert intent.use_history


def test_bounded_summary_of_prior_target_survives_later_comparison_scope_expansion():
    prior = {
        **PRIOR,
        "user_scope": {"areas": ["衡水市", "河北省"],
                       "periods": ["第十三个五年规划"]},
        "documents": [{"planid": "1116", "area": "衡水市",
                       "fyp": "第十三个五年规划"},
                      {"planid": "2210", "area": "河北省",
                       "fyp": "第十三个五年规划"}],
    }

    same_scope = classify_intent("请将前三轮讨论内容整理成摘要，突出衡水市十三五发展目标", prior)
    new_scope = classify_intent("改为只看石家庄市十三五规划，整理前三轮内容", prior)

    assert same_scope.primary == "organize"
    assert same_scope.use_history
    assert not new_scope.use_history
    assert new_scope.replace_scope


def test_topic_only_policy_query_requests_a_reviewed_organization():
    for prompt in ("消防相关政策", "养老服务相关政策", "消防政策梳理"):
        intent = classify_intent(prompt)
        assert intent.primary == "organize"
        assert intent.need_report
        assert not intent.use_history

    # One precise factual question still takes the inexpensive lookup path.
    single = classify_intent("沧州市十五计划提出的产业布局总体设想是什么？")
    assert single.primary == "fact_lookup"
