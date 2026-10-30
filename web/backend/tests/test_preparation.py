import asyncio
from functools import wraps

import pytest
from pydantic import ValidationError

from app.preparation import PreparationStateError, ResearchPreparationService
from app.preparation_models import ResearchPrepareRequest, ResearchPrepareResponse


def run_async(test):
    @wraps(test)
    def wrapper(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))

    return wrapper


def test_request_model_rejects_invalid_types_extras_and_blank_message():
    invalid_payloads = [
        {"mode": "unknown", "message": "政策"},
        {"mode": "qa", "message": "   "},
        {"mode": "qa", "message": 123},
        {"mode": "qa", "message": "政策", "extra": True},
        {"mode": "qa", "message": "政策", "state": {"state_id": "x", "revision": True}},
        {"mode": "qa", "message": "政策", "state": {"state_id": "x", "revision": 1.0}},
        {"mode": "qa", "message": "政策", "state": {"state_id": "x", "revision": 0, "topic": "伪造"}},
        {"mode": "qa", "message": "政策", "state": None},
    ]

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            ResearchPrepareRequest.model_validate(payload)


def test_request_model_accepts_4000_characters_and_rejects_4001():
    accepted = ResearchPrepareRequest(mode="qa", message="政" * 4000)

    assert len(accepted.message) == 4000
    with pytest.raises(ValidationError):
        ResearchPrepareRequest(mode="qa", message="政" * 4001)


@run_async
async def test_response_serialization_has_contract_fields_and_ready_request_shape():
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": "什么是数字经济？"}
    )

    assert isinstance(response, ResearchPrepareResponse)
    assert response.model_dump(mode="json").keys() == {
        "status", "mode", "message", "options", "state", "request", "warnings"
    }
    assert response.request.model_dump(exclude_none=True) == {
        "task_type": "qa", "question": "什么是数字经济？"
    }
    assert response.state.original_request == "什么是数字经济？"


@run_async
async def test_single_topic_qa_asks_about_aspect():
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": "数字经济"}
    )

    assert response.status == "needs_clarification"
    assert response.request is None
    assert response.options[0].id == "aspect"
    assert response.state.topic == "数字经济"
    assert response.state.pending_conditions[0].id == "aspect"


@run_async
async def test_qa_focus_answer_is_retained_in_prepared_question():
    service = ResearchPreparationService()
    first = await service.prepare({"mode": "qa", "message": "数字经济"})
    second = await service.prepare(
        {
            "mode": "qa",
            "message": "产业政策",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "ready"
    assert "数字经济" in second.request.question
    assert "产业政策" in second.request.question


@run_async
async def test_explicit_qa_question_is_ready_without_forced_time_clarification():
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": "什么是数字经济？"}
    )

    assert response.status == "ready"
    assert response.options == []
    assert response.request.question == "什么是数字经济？"
    assert response.state.time_scope.precision == "unspecified"


@run_async
async def test_all_policies_or_exact_count_claim_is_blocked_for_qa():
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": "列出全部数字经济政策并准确统计篇数"}
    )

    assert response.status == "blocked"
    assert response.request is None
    assert "全部" in response.message or "准确" in response.message


@run_async
async def test_blocked_exhaustive_qa_retains_calendar_time_warning():
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": "列出2021年全部数字经济政策"}
    )

    assert response.status == "blocked"
    assert "不保证按发布日期精确过滤" in response.warnings
    assert any(s.condition_id == "time_scope" and s.status == "request_only" for s in response.state.condition_statuses)


@run_async
async def test_wide_organize_request_asks_for_missing_scope_and_dimensions():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理政策"}
    )

    assert response.status == "needs_clarification"
    assert response.request is None
    assert response.state.topic is None
    assert response.options


@run_async
async def test_organize_accepts_plain_topic_as_answer_to_topic_clarification():
    service = ResearchPreparationService()
    first = await service.prepare({"mode": "organize", "message": "梳理政策"})
    second = await service.prepare(
        {
            "mode": "organize",
            "message": "数字经济政策",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.state.topic == "数字经济政策"
    assert second.options[0].id == "scope"


@run_async
async def test_organize_combines_topic_area_time_and_dimensions_in_one_turn():
    response = await ResearchPreparationService().prepare(
        {
            "mode": "organize",
            "message": "梳理北京市数字经济政策，按支持对象和工具分类，限定十三五",
        }
    )

    assert response.status == "ready"
    assert response.state.topic == "数字经济政策"
    assert response.state.area == "北京市"
    assert response.state.time_scope.precision == "range"
    assert response.state.time_scope.start == "2016"
    assert response.state.time_scope.end == "2020"
    assert response.request.task_type == "organize"
    assert response.request.dimensions == ["支持对象", "工具"]
    assert any(item.status == "executable_filter" for item in response.state.condition_statuses)


@run_async
async def test_organize_followup_adds_time_area_and_dimensions_without_losing_topic():
    service = ResearchPreparationService()
    first = await service.prepare({"mode": "organize", "message": "梳理数字经济政策"})

    second = await service.prepare(
        {
            "mode": "organize",
            "message": "北京市，十三五，按支持对象分类",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "ready"
    assert second.state.topic == "数字经济政策"
    assert second.state.area == "北京市"
    assert second.state.time_scope.start == "2016"
    assert second.request.dimensions == ["支持对象"]
    assert len(second.state.completed_questions) == 1


@run_async
async def test_organize_can_explicitly_unrestrict_time_and_area():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理全国数字经济政策，不限时间、不限地区，按产业分类"}
    )

    assert response.status == "ready"
    assert response.state.area is None
    assert response.state.time_scope.precision == "unspecified"
    unrestricted = [s for s in response.state.condition_statuses if s.status == "unrestricted"]
    assert {s.condition_id for s in unrestricted} == {"area", "time_scope"}


@run_async
async def test_multiple_areas_are_preserved_as_request_only():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理北京市和上海市数字经济政策，不限时间，按产业分类"}
    )

    area_status = next(s for s in response.state.condition_statuses if s.condition_id == "area")
    assert area_status.status == "request_only"
    assert area_status.value == "北京市、上海市"
    assert "北京市" in response.request.question
    assert "上海市" in response.request.question


@run_async
async def test_single_route_compatible_prefecture_area_is_executable():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理苏州市数字经济政策，不限时间，按产业分类"}
    )

    assert response.state.area == "苏州市"
    assert response.state.topic == "数字经济政策"
    assert response.request.question.count("苏州市") == 1
    area_status = next(s for s in response.state.condition_statuses if s.condition_id == "area")
    assert area_status.status == "request_only"


@run_async
async def test_unconfirmed_unlisted_city_stays_in_topic_and_request():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理温州市数字经济政策，不限时间，按产业分类"}
    )

    assert response.state.area is None
    assert "温州市" in response.state.topic
    assert "温州市" in response.request.question


@run_async
async def test_organize_followup_modifies_prior_condition_and_does_not_repeat_question():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理北京市数字经济政策，不限时间，按产业分类"}
    )
    second = await service.prepare(
        {
            "mode": "organize",
            "message": "改为上海市，并按支持对象分类",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.state.area == "上海市"
    assert second.state.topic == first.state.topic
    assert second.request.dimensions == ["支持对象"]
    assert all(option.id not in {q.question_id for q in second.state.completed_questions} for option in second.options)
    assert "北京市" not in second.request.question


@run_async
async def test_organize_explicit_topic_change_updates_topic():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理北京市数字经济政策，不限时间，按产业分类"}
    )
    second = await service.prepare(
        {
            "mode": "organize",
            "message": "主题改为人工智能产业政策",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.state.topic == "人工智能产业政策"
    assert "数字经济政策" not in second.request.question


@run_async
@pytest.mark.parametrize("time_text", ["2021年", "2021—2023年", "2024-03-12", "近十年"])
async def test_calendar_dates_and_relative_periods_are_request_only(time_text):
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": f"梳理北京市数字经济政策，{time_text}，按产业分类"}
    )

    assert response.status in {"ready", "needs_clarification"}
    time_status = next(s for s in response.state.condition_statuses if s.condition_id == "time_scope")
    assert time_status.status in {"pending", "request_only"}
    if response.status == "ready":
        assert time_status.status == "request_only"
        assert "不保证按发布日期精确过滤" in response.warnings


@run_async
async def test_exact_time_requires_confirmation_before_ready():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "qa", "message": "查询2024-03-12发布的数字经济政策"}
    )

    assert first.status == "needs_clarification"
    assert first.request is None
    assert any(s.status == "pending" for s in first.state.condition_statuses)
    assert "不保证按发布日期精确过滤" in first.warnings
    second = await service.prepare(
        {
            "mode": "qa",
            "message": "确认继续",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "ready"
    assert "2024-03-12" in second.request.question
    assert any(s.status == "request_only" for s in second.state.condition_statuses)
    assert "不保证按发布日期精确过滤" in second.warnings


@run_async
async def test_exact_timestamp_retains_explicit_timezone():
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": "查询2024-03-12T14:30:00+08:00发布的数字经济政策"}
    )

    assert response.status == "needs_clarification"
    assert response.state.time_scope.precision == "exact"
    assert response.state.time_scope.timezone == "+08:00"
    assert response.state.condition_statuses[0].value == "2024-03-12T14:30:00+08:00"


@run_async
async def test_chinese_exact_date_and_time_do_not_collapse_to_a_year():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理北京市数字经济政策，2024年3月12日14时30分，按产业分类"}
    )

    assert response.status == "needs_clarification"
    assert response.state.time_scope.precision == "exact"
    time_status = next(s for s in response.state.condition_statuses if s.condition_id == "time_scope")
    assert time_status.value == "2024年3月12日14时30分"
    assert "不保证按发布日期精确过滤" in response.warnings


@run_async
async def test_exact_time_can_be_adjusted_without_retaining_the_old_date():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "qa", "message": "查询2024-03-12发布的数字经济政策"}
    )
    second = await service.prepare(
        {
            "mode": "qa",
            "message": "调整为2023年",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "ready"
    assert "2023年" in second.request.question
    assert "2024-03-12" not in second.request.question
    assert "不保证按发布日期精确过滤" in second.warnings


@run_async
@pytest.mark.parametrize("mode", ["extract", "compare", "compare_plans"])
async def test_unsupported_modes_are_blocked_without_switching(mode):
    response = await ResearchPreparationService().prepare(
        {"mode": mode, "message": "请处理这个研究任务"}
    )

    assert response.status == "blocked"
    assert response.mode == mode
    assert response.state.mode == mode
    assert response.request is None


@run_async
async def test_clarification_limit_blocks_instead_of_looping():
    service = ResearchPreparationService(max_clarifications=1)
    first = await service.prepare({"mode": "organize", "message": "梳理政策"})
    second = await service.prepare(
        {
            "mode": "organize",
            "message": "还是政策",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "blocked"
    assert second.request is None


@run_async
async def test_unknown_expired_and_mode_mismatch_states_conflict():
    service = ResearchPreparationService(ttl_seconds=0.01)
    with pytest.raises(PreparationStateError) as unknown:
        await service.prepare(
            {"mode": "qa", "message": "继续", "state": {"state_id": "missing", "revision": 0}}
        )
    assert unknown.value.detail == "研究准备状态已失效或冲突"

    first = await service.prepare({"mode": "qa", "message": "数字经济"})
    with pytest.raises(PreparationStateError):
        await service.prepare(
            {
                "mode": "organize",
                "message": "继续",
                "state": {"state_id": first.state.state_id, "revision": first.state.revision},
            }
        )
    await asyncio.sleep(0.02)
    with pytest.raises(PreparationStateError):
        await service.prepare(
            {
                "mode": "qa",
                "message": "继续",
                "state": {"state_id": first.state.state_id, "revision": first.state.revision},
            }
        )


@run_async
async def test_stale_revision_conflicts_but_identical_immediate_retry_is_idempotent():
    service = ResearchPreparationService()
    first = await service.prepare({"mode": "qa", "message": "数字经济"})
    payload = {
        "mode": "qa",
        "message": "想了解产业政策",
        "state": {"state_id": first.state.state_id, "revision": first.state.revision},
    }

    second = await service.prepare(payload)
    replay = await service.prepare(payload)

    assert replay.model_dump(mode="json") == second.model_dump(mode="json")
    assert replay.state.revision == first.state.revision + 1
    with pytest.raises(PreparationStateError):
        await service.prepare({**payload, "message": "换一个问题"})


@run_async
async def test_concurrent_identical_submission_is_idempotent():
    service = ResearchPreparationService()
    first = await service.prepare({"mode": "qa", "message": "数字经济"})
    payload = {
        "mode": "qa",
        "message": "产业政策",
        "state": {"state_id": first.state.state_id, "revision": first.state.revision},
    }

    left, right = await asyncio.gather(service.prepare(payload), service.prepare(payload))

    assert left.model_dump(mode="json") == right.model_dump(mode="json")
    assert left.state.revision == 1


@run_async
async def test_state_capacity_is_bounded_and_oldest_state_is_evicted():
    service = ResearchPreparationService(max_states=1)
    first = await service.prepare({"mode": "qa", "message": "数字经济"})
    await service.prepare({"mode": "qa", "message": "什么是产业政策？"})

    with pytest.raises(PreparationStateError):
        await service.prepare(
            {
                "mode": "qa",
                "message": "继续",
                "state": {"state_id": first.state.state_id, "revision": first.state.revision},
            }
        )


@run_async
async def test_topic_ending_with_city_suffix_is_not_inferred_as_an_area():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理企业上市政策，不限时间，按产业分类"}
    )

    assert response.state.area is None
    assert "企业上市" in response.state.topic
    assert not any(
        item.condition_id == "area" and item.status == "executable_filter"
        for item in response.state.condition_statuses
    )


@run_async
async def test_unrecognized_area_with_explicit_scope_marker_stays_request_only():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理数字经济政策，地区为火星市，不限时间，按产业分类"}
    )

    assert response.state.area is None
    assert response.state.topic == "数字经济政策"
    area_status = next(s for s in response.state.condition_statuses if s.condition_id == "area")
    assert area_status.status == "request_only"
    assert area_status.value == "火星市"
    assert "火星市" in response.request.question


@run_async
async def test_year_prefix_before_a_real_area_keeps_area_and_topic_exact():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理2020年前北京市数字经济政策，不限时间，按产业分类"}
    )

    assert response.state.area == "北京市"
    assert response.state.topic == "数字经济政策"


@run_async
@pytest.mark.parametrize("time_text", ["2020年以前", "截至2020年", "2020年之后", "2020年以来"])
async def test_calendar_year_boundary_is_preserved_in_value_and_ready_question(time_text):
    response = await ResearchPreparationService().prepare(
        {
            "mode": "organize",
            "message": f"梳理北京市数字经济政策，{time_text}，按产业分类",
        }
    )

    time_status = next(s for s in response.state.condition_statuses if s.condition_id == "time_scope")
    assert time_status.status == "request_only"
    assert time_status.value == time_text
    assert response.status == "ready"
    assert time_text in response.request.question
    assert "不保证按发布日期精确过滤" in response.warnings


@run_async
async def test_nationwide_scope_is_request_only_and_explicit_in_request_text():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理全国范围数字经济政策，不限时间，按产业分类"}
    )

    assert response.status == "ready"
    assert response.state.area is None
    area_status = next(s for s in response.state.condition_statuses if s.condition_id == "area")
    assert area_status.status == "request_only"
    assert "全国" in area_status.value
    assert "全国" in response.request.question
    assert response.state.topic == "数字经济政策"


@run_async
async def test_all_substring_in_ownership_reform_question_does_not_trigger_exhaustive_block():
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": "所有制改革的产业政策有哪些？"}
    )

    assert response.status == "ready"
    assert response.request is not None


@run_async
@pytest.mark.parametrize("message", ["列出全部政策", "请给出准确篇数", "总共有多少篇政策？"])
async def test_true_exhaustive_or_exact_count_qa_is_blocked(message):
    response = await ResearchPreparationService().prepare({"mode": "qa", "message": message})

    assert response.status == "blocked"
    assert response.request is None


@run_async
async def test_qa_followup_can_answer_aspect_and_add_calendar_year_together():
    service = ResearchPreparationService()
    first = await service.prepare({"mode": "qa", "message": "数字经济"})
    second = await service.prepare(
        {
            "mode": "qa",
            "message": "产业政策，2023年",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "ready"
    assert "aspect" not in {option.id for option in second.options}
    assert any(question.question_id == "aspect" for question in second.state.completed_questions)
    time_status = next(s for s in second.state.condition_statuses if s.condition_id == "time_scope")
    assert time_status.status == "request_only"
    assert time_status.value == "2023年"
    assert "产业政策" in second.request.question
    assert "2023年" in second.request.question
    assert "不保证按发布日期精确过滤" in second.warnings


@run_async
async def test_calendar_range_and_planning_period_are_both_request_only_and_need_clarification():
    response = await ResearchPreparationService().prepare(
        {
            "mode": "organize",
            "message": "梳理北京市数字经济政策，2021—2023年，十四五规划为背景，按产业分类",
        }
    )

    assert response.status == "needs_clarification"
    time_status = next(s for s in response.state.condition_statuses if s.condition_id == "time_scope")
    assert time_status.status == "pending"
    assert "2021—2023年" in time_status.value
    assert "十四五" in time_status.value
    assert response.state.time_scope.start == "2021"
    assert response.state.time_scope.end == "2023"
    assert "十四五" not in {s.value for s in response.state.condition_statuses if s.status == "executable_filter"}
    assert response.request is None
    assert "不保证按发布日期精确过滤" in response.warnings


@run_async
async def test_exact_date_and_planning_period_cannot_mask_each_other():
    response = await ResearchPreparationService().prepare(
        {
            "mode": "qa",
            "message": "截至2020-03-12，十四五规划为背景的数字经济政策有哪些？",
        }
    )

    assert response.status == "needs_clarification"
    assert response.request is None
    time_status = next(s for s in response.state.condition_statuses if s.condition_id == "time_scope")
    assert time_status.status == "pending"
    assert "截至2020-03-12" in time_status.value
    assert "十四五" in time_status.value
    assert response.state.time_scope.precision == "exact"


@run_async
@pytest.mark.parametrize("boundary", ["截至2020-03-12", "截至2020年3月12日"])
async def test_as_of_full_date_boundary_stays_pending_in_qa(boundary):
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": f"{boundary}发布的数字经济政策有哪些？"}
    )

    assert response.status == "needs_clarification"
    assert response.request is None
    assert response.state.time_scope.precision == "exact"
    time_status = next(s for s in response.state.condition_statuses if s.condition_id == "time_scope")
    assert time_status.status == "pending"
    assert time_status.value == boundary
    assert response.state.time_scope.start in {"2020-03-12", "2020年3月12日"}
    assert response.state.original_request.startswith(boundary)
    assert "不保证按发布日期精确过滤" in response.warnings


@run_async
@pytest.mark.parametrize(
    "intent",
    ["请列出全部政策", "请给完整清单", "全部列出数字经济政策", "请给出准确篇数", "总共有多少篇政策"],
)
async def test_full_corpus_intent_in_followup_blocks_accumulated_request(intent):
    service = ResearchPreparationService()
    first = await service.prepare({"mode": "qa", "message": "数字经济"})
    second = await service.prepare(
        {
            "mode": "qa",
            "message": intent,
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "blocked"
    assert second.request is None
    assert second.state.original_request == "数字经济"
    assert second.warnings == []


@run_async
async def test_national_scope_does_not_match_national_advisory_phrase():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理全国政协数字经济政策，不限时间，按产业分类"}
    )

    assert response.state.topic == "全国政协数字经济政策"
    assert not any(s.condition_id == "area" for s in response.state.condition_statuses)
    assert "全国政协" in response.request.question


@run_async
async def test_national_policy_phrase_is_an_explicit_national_scope():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理全国政策，不限时间，按产业分类"}
    )

    area_status = next(s for s in response.state.condition_statuses if s.condition_id == "area")
    assert area_status.status == "request_only"
    assert area_status.value == "全国范围"
    assert "全国" in response.request.question


@run_async
async def test_nationwide_and_specific_city_conflict_is_preserved_and_clarified():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理全国范围北京市数字经济政策，不限时间，按产业分类"}
    )

    assert response.status == "needs_clarification"
    area_status = next(s for s in response.state.condition_statuses if s.condition_id == "area")
    assert area_status.status == "pending"
    assert "全国范围" in area_status.value
    assert "北京市" in area_status.value
    assert any(item.id == "area" and item.text == area_status.value for item in response.state.pending_conditions)
    assert "全国范围" in response.state.original_request
    assert "北京市" in response.state.original_request
    assert response.request is None
    assert response.warnings == []


@run_async
async def test_unconfirmed_city_is_kept_as_request_only_without_whitelist_expansion():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "梳理数字经济政策，地区为温州市，不限时间，按产业分类"}
    )

    assert response.state.area is None
    area_status = next(s for s in response.state.condition_statuses if s.condition_id == "area")
    assert area_status.status == "request_only"
    assert area_status.value == "温州市"
    assert "温州市" in response.request.question
    assert not any(s.status == "executable_filter" and s.value == "温州市" for s in response.state.condition_statuses)


def ambiguous_time_request():
    return {
        "mode": "organize",
        "message": "梳理北京市数字经济政策，2021—2023年，十四五规划为背景，按产业分类",
    }


@run_async
@pytest.mark.parametrize(
    ("answer", "expected_status", "expected_value", "expected_warning"),
    [
        ("按公历年份", "request_only", "2021—2023年", True),
        ("按规划时期", "executable_filter", "十四五", False),
        ("两者都作为文字要求", "request_only", "2021—2023年，十四五规划为背景", True),
    ],
)
async def test_time_scope_choice_resolves_conflict_and_is_not_reasked(
    answer, expected_status, expected_value, expected_warning
):
    service = ResearchPreparationService()
    first = await service.prepare(ambiguous_time_request())

    assert first.status == "needs_clarification"
    assert any(option.id == "time_scope" for option in first.options)
    assert first.request is None
    assert any(condition.id == "time_scope" for condition in first.state.pending_conditions)
    assert any(status.condition_id == "time_scope" and status.status == "pending" for status in first.state.condition_statuses)
    assert not any(condition.id == "time_scope" for condition in first.state.confirmed_conditions)
    assert not any(question.question_id == "time_scope" for question in first.state.completed_questions)

    second = await service.prepare(
        {
            "mode": "organize",
            "message": answer,
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "ready"
    assert not any(option.id == "time_scope" for option in second.options)
    assert not any(condition.id == "time_scope" for condition in second.state.pending_conditions)
    assert any(condition.id == "time_scope" for condition in second.state.confirmed_conditions)
    time_status = next(status for status in second.state.condition_statuses if status.condition_id == "time_scope")
    assert time_status.status == expected_status
    assert time_status.value == expected_value
    assert any(question.question_id == "time_scope" and question.answer == answer for question in second.state.completed_questions)
    assert second.request is not None
    assert "2021—2023年" in second.request.question if answer != "按规划时期" else "十四五" in second.request.question
    assert ("不保证按发布日期精确过滤" in second.warnings) is expected_warning

    third = await service.prepare(
        {
            "mode": "organize",
            "message": "保留当前整理方式",
            "state": {"state_id": second.state.state_id, "revision": second.state.revision},
        }
    )
    assert third.status == "ready"
    assert third.options == []
    assert third.state.pending_conditions == []
    assert any(condition.id == "time_scope" for condition in third.state.confirmed_conditions)
    assert any(status.condition_id == "time_scope" and status.status == expected_status for status in third.state.condition_statuses)
    assert third.request is not None and expected_value in third.request.question
    assert any(question.question_id == "time_scope" for question in third.state.completed_questions)
    assert not any(option.id == "time_scope" for option in third.options)


@run_async
async def test_area_scope_national_choice_resolves_and_is_not_reasked():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理全国范围北京市数字经济政策，不限时间，按产业分类"}
    )

    assert first.status == "needs_clarification"
    assert any(option.id == "area_scope" for option in first.options)
    assert any(condition.id == "area" for condition in first.state.pending_conditions)
    assert not any(condition.id == "area" for condition in first.state.confirmed_conditions)
    assert any(status.condition_id == "area" and status.status == "pending" for status in first.state.condition_statuses)
    assert first.request is None
    assert not any(question.question_id == "area_scope" for question in first.state.completed_questions)

    second = await service.prepare(
        {
            "mode": "organize",
            "message": "全国范围",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "ready"
    assert second.options == []
    assert not any(option.id == "area_scope" for option in second.options)
    assert not any(condition.id == "area" for condition in second.state.pending_conditions)
    assert any(condition.id == "area" for condition in second.state.confirmed_conditions)
    area_status = next(status for status in second.state.condition_statuses if status.condition_id == "area")
    assert area_status.status == "request_only"
    assert area_status.value == "全国范围"
    assert any(question.question_id == "area_scope" and question.answer == "全国范围" for question in second.state.completed_questions)
    assert second.request is not None and "全国范围" in second.request.question
    assert all(condition.id != "area" for condition in second.state.pending_conditions)
    assert any(condition.id == "area" for condition in second.state.confirmed_conditions)
    assert second.warnings == []

    third = await service.prepare(
        {
            "mode": "organize",
            "message": "保留当前范围",
            "state": {"state_id": second.state.state_id, "revision": second.state.revision},
        }
    )
    assert third.status == "ready"
    assert third.options == []
    assert third.state.pending_conditions == []
    assert any(status.condition_id == "area" and status.status == "request_only" for status in third.state.condition_statuses)
    assert third.request is not None and "全国范围" in third.request.question
    assert any(question.question_id == "area_scope" for question in third.state.completed_questions)
    assert not any(option.id == "area_scope" for option in third.options)


@run_async
async def test_area_scope_specific_city_choice_keeps_single_city():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理全国范围北京市数字经济政策，不限时间，按产业分类"}
    )
    second = await service.prepare(
        {
            "mode": "organize",
            "message": "具体地区",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "ready"
    assert second.options == []
    assert second.state.pending_conditions == []
    assert second.state.area == "北京市"
    area_status = next(status for status in second.state.condition_statuses if status.condition_id == "area")
    assert area_status.status == "executable_filter"
    assert "全国范围" not in second.request.question
    assert "北京市" in second.request.question
    assert any(condition.id == "area" for condition in second.state.confirmed_conditions)
    assert any(status.condition_id == "area" and status.status == "executable_filter" for status in second.state.condition_statuses)
    assert any(question.question_id == "area_scope" and question.answer == "具体地区" for question in second.state.completed_questions)


@run_async
async def test_area_scope_with_multiple_specific_regions_continues_clarification():
    service = ResearchPreparationService()
    first = await service.prepare(
        {
            "mode": "organize",
            "message": "梳理全国范围北京市和上海市数字经济政策，不限时间，按产业分类",
        }
    )
    second = await service.prepare(
        {
            "mode": "organize",
            "message": "具体地区",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "needs_clarification"
    assert any(option.id == "area_detail" for option in second.options)
    assert second.request is None
    assert any(option.id == "area_detail" for option in second.options)
    assert any(condition.id == "area" for condition in second.state.pending_conditions)
    assert any(status.condition_id == "area" and status.status == "pending" for status in second.state.condition_statuses)
    assert any(question.question_id == "area_scope" and question.answer == "具体地区" for question in second.state.completed_questions)
    assert not any(condition.id == "area" for condition in second.state.confirmed_conditions)

    third = await service.prepare(
        {
            "mode": "organize",
            "message": "上海市",
            "state": {"state_id": second.state.state_id, "revision": second.state.revision},
        }
    )
    assert third.status == "ready"
    assert third.options == []
    assert third.state.pending_conditions == []
    assert third.state.area == "上海市"
    assert not any(option.id == "area_detail" for option in third.options)
    assert any(question.question_id == "area_detail" and question.answer == "上海市" for question in third.state.completed_questions)
    assert "北京市" not in third.request.question
    assert any(condition.id == "area" for condition in third.state.confirmed_conditions)
    assert any(status.condition_id == "area" and status.status == "executable_filter" for status in third.state.condition_statuses)


@run_async
@pytest.mark.parametrize("phrase", ["全国和北京市", "全国与北京市", "全国、北京市"])
async def test_nationwide_connective_forms_create_scope_conflict(phrase):
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": f"梳理{phrase}数字经济政策，不限时间，按产业分类"}
    )

    assert response.status == "needs_clarification"
    assert any(option.id == "area_scope" for option in response.options)
    area_status = next(status for status in response.state.condition_statuses if status.condition_id == "area")
    assert area_status.status == "pending"
    assert "北京市" in area_status.value
    assert "全国政协" not in area_status.value
    assert response.request is None


@run_async
@pytest.mark.parametrize("negation", ["无需", "不必", "不要", "不需要"])
async def test_negated_exhaustive_intent_does_not_block(negation):
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": f"{negation}列出全部政策，只分析产业政策"}
    )

    assert response.status != "blocked"
    assert response.message
    assert response.state.original_request.startswith(negation)
    assert response.request is None if response.status == "needs_clarification" else response.request is not None
    assert response.warnings == []


@run_async
async def test_negated_exhaustive_followup_does_not_block_accumulated_qa():
    service = ResearchPreparationService()
    first = await service.prepare({"mode": "qa", "message": "数字经济"})
    second = await service.prepare(
        {
            "mode": "qa",
            "message": "不需要列出全部政策，只分析产业政策",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status != "blocked"
    assert second.request is None if second.status == "needs_clarification" else second.request is not None


@run_async
@pytest.mark.parametrize(
    "message",
    [
        "不要全部列出政策，只给三个例子",
        "无需列出全部政策，只分析产业政策",
    ],
)
async def test_finite_or_negated_full_listing_request_is_not_blocked(message):
    response = await ResearchPreparationService().prepare({"mode": "qa", "message": message})

    assert response.status != "blocked"
    assert response.request is None if response.status == "needs_clarification" else response.request is not None


@run_async
async def test_latest_positive_full_listing_intent_overrides_prior_negation():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "qa", "message": "不要全部列出政策，只分析数字经济"}
    )
    second = await service.prepare(
        {
            "mode": "qa",
            "message": "列出全部政策",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert first.status != "blocked"
    assert second.status == "blocked"
    assert second.request is None


@run_async
async def test_latest_negated_full_listing_intent_overrides_prior_positive():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "qa", "message": "梳理数字经济并列出全部政策"}
    )
    second = await service.prepare(
        {
            "mode": "qa",
            "message": "不要全部列出，只分析产业政策",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert first.status == "blocked"
    assert second.status != "blocked"
    assert second.request is None if second.status == "needs_clarification" else second.request is not None


@run_async
async def test_finite_examples_and_ownership_reform_are_not_exhaustive_intent():
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": "所有制改革有哪些政策？请只给三个例子"}
    )

    assert response.status == "ready"
    assert response.request is not None


@run_async
async def test_ownership_reform_question_is_not_treated_as_exhaustive_intent():
    response = await ResearchPreparationService().prepare(
        {"mode": "qa", "message": "所有制改革有哪些政策？"}
    )

    assert response.status == "ready"
    assert response.request is not None
    assert "所有制改革" in response.request.question


@run_async
async def test_organize_followup_with_comprehensive_review_preserves_original_topic():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理支持数字经济的政策"}
    )
    second = await service.prepare(
        {
            "mode": "organize",
            "message": "近十年、北京市、综合梳理",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )
    third = await service.prepare(
        {
            "mode": "organize",
            "message": "确认继续",
            "state": {"state_id": second.state.state_id, "revision": second.state.revision},
        }
    )

    assert third.status == "ready"
    assert third.state.topic == "支持数字经济的政策"
    assert "支持数字经济的政策" in third.request.question
    assert "综合梳理" not in third.state.topic
    assert "近十年" in third.request.question
    assert "北京市" in third.request.question


@run_async
async def test_original_fifteenth_plan_beijing_digital_economy_example_is_ready():
    response = await ResearchPreparationService().prepare(
        {"mode": "organize", "message": "十四五北京市数字经济重点企业扶持"}
    )

    assert response.status == "ready"
    assert response.request is not None
    assert "十四五" in response.request.question
    assert "北京市" in response.request.question
    assert "数字经济重点企业扶持" in response.request.question


@run_async
@pytest.mark.parametrize("month_text", ["2021年3月", "2021-03"])
async def test_organize_month_followup_retains_full_message_as_request_only(month_text):
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理数字经济政策"}
    )
    clarification = f"北京市，{month_text}，按支持对象分类"
    second = await service.prepare(
        {
            "mode": "organize",
            "message": clarification,
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert second.status == "ready"
    assert second.state.time_scope.precision == "unspecified"
    time_status = next(
        item for item in second.state.condition_statuses if item.condition_id == "time_scope"
    )
    assert time_status.status == "request_only"
    assert time_status.value == month_text
    assert clarification in second.request.question
    assert month_text in second.request.question
    assert "不保证按发布日期精确过滤" in second.warnings


@run_async
@pytest.mark.parametrize(
    ("replacement", "expected_time"),
    [("改为2022年", "2022年"), ("不限时间", "不限时间")],
)
async def test_replacing_month_condition_drops_stale_month_from_request(replacement, expected_time):
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理数字经济政策"}
    )
    month_reply = await service.prepare(
        {
            "mode": "organize",
            "message": "北京市，2021年3月，按支持对象分类",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )
    updated = await service.prepare(
        {
            "mode": "organize",
            "message": replacement,
            "state": {"state_id": month_reply.state.state_id, "revision": month_reply.state.revision},
        }
    )

    assert updated.status == "ready"
    assert expected_time in updated.request.question
    assert "2021年3月" not in updated.request.question
    assert "北京市，2021年3月，按支持对象分类" not in updated.request.question


@run_async
async def test_month_condition_can_be_replaced_by_relative_five_years_and_confirmed():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理数字经济政策"}
    )
    month_reply_text = "北京市，2021年3月，按支持对象分类"
    month_reply = await service.prepare(
        {
            "mode": "organize",
            "message": month_reply_text,
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )
    changed = await service.prepare(
        {
            "mode": "organize",
            "message": "时间改为近五年",
            "state": {"state_id": month_reply.state.state_id, "revision": month_reply.state.revision},
        }
    )
    confirmed = await service.prepare(
        {
            "mode": "organize",
            "message": "确认继续",
            "state": {"state_id": changed.state.state_id, "revision": changed.state.revision},
        }
    )

    assert changed.status == "needs_clarification"
    changed_time_status = next(
        item for item in changed.state.condition_statuses if item.condition_id == "time_scope"
    )
    assert changed_time_status.status == "pending"
    assert changed_time_status.value == "近五年"
    assert "2021年3月" not in changed.state.time_scope.model_dump_json()
    assert confirmed.status == "ready"
    time_status = next(
        item for item in confirmed.state.condition_statuses if item.condition_id == "time_scope"
    )
    assert time_status.status == "request_only"
    assert time_status.value == "近五年"
    assert "近五年" in confirmed.request.question
    assert "2021年3月" not in confirmed.request.question
    assert month_reply_text not in confirmed.request.question


@run_async
@pytest.mark.parametrize("relative_time", ["近五年", "过去3个月", "最近三年"])
async def test_relative_year_and_month_expressions_require_request_only_confirmation(relative_time):
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理数字经济政策"}
    )
    pending = await service.prepare(
        {
            "mode": "organize",
            "message": f"北京市，{relative_time}，按支持对象分类",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )

    assert pending.status == "needs_clarification"
    time_status = next(
        item for item in pending.state.condition_statuses if item.condition_id == "time_scope"
    )
    assert time_status.value == relative_time
    assert any(option.id == "continue_request_only" for option in pending.options)
    confirmed = await service.prepare(
        {
            "mode": "organize",
            "message": "确认继续",
            "state": {"state_id": pending.state.state_id, "revision": pending.state.revision},
        }
    )

    assert confirmed.status == "ready"
    time_status = next(
        item for item in confirmed.state.condition_statuses if item.condition_id == "time_scope"
    )
    assert time_status.status == "request_only"
    assert time_status.value == relative_time
    assert relative_time in confirmed.request.question


@run_async
async def test_unparseable_explicit_time_change_stays_pending_instead_of_reusing_month():
    service = ResearchPreparationService()
    first = await service.prepare(
        {"mode": "organize", "message": "梳理数字经济政策"}
    )
    month_reply = await service.prepare(
        {
            "mode": "organize",
            "message": "北京市，2021年3月，按支持对象分类",
            "state": {"state_id": first.state.state_id, "revision": first.state.revision},
        }
    )
    changed = await service.prepare(
        {
            "mode": "organize",
            "message": "时间改为一个更合适的区间",
            "state": {"state_id": month_reply.state.state_id, "revision": month_reply.state.revision},
        }
    )
    confirmed = await service.prepare(
        {
            "mode": "organize",
            "message": "确认继续",
            "state": {"state_id": changed.state.state_id, "revision": changed.state.revision},
        }
    )

    assert changed.status == "needs_clarification"
    assert changed.request is None
    assert any(option.id == "time_scope" for option in changed.options)
    assert confirmed.status == "needs_clarification"
    assert confirmed.request is None
    assert confirmed.state.time_scope.precision == "unspecified"
    time_status = next(
        item for item in confirmed.state.condition_statuses if item.condition_id == "time_scope"
    )
    assert time_status.status == "pending"
    assert time_status.value is None
