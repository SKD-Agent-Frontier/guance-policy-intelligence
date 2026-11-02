"""Autonomous preparation removes missing-field gates without weakening evidence rules."""
import pytest

from app.preparation import ResearchPreparationService


@pytest.mark.asyncio
async def test_bare_topic_research_starts_without_forced_aspect():
    response = await ResearchPreparationService().prepare({
        "mode": "qa", "message": "数字经济", "autonomous": True,
    })
    assert response.status == "ready"
    assert response.options == []
    assert response.request is not None
    assert response.request.task_type == "qa"
    assert response.request.question == "数字经济"
    assert response.state.topic == "数字经济"


@pytest.mark.asyncio
async def test_organize_theme_without_explicit_date_or_region_starts():
    response = await ResearchPreparationService().prepare({
        "mode": "organize", "message": "梳理数字经济政策", "autonomous": True,
    })
    assert response.status == "ready"
    assert response.request is not None
    assert response.request.question == "梳理数字经济政策"
    assert response.options == []
    assert not response.state.pending_conditions


@pytest.mark.asyncio
async def test_legacy_guided_preparation_remains_compatible():
    response = await ResearchPreparationService().prepare({
        "mode": "qa", "message": "数字经济",
    })
    assert response.status == "needs_clarification"
    assert response.options[0].id == "aspect"


@pytest.mark.asyncio
async def test_autonomous_preparation_does_not_overrule_explicit_conflicts():
    response = await ResearchPreparationService().prepare({
        "mode": "qa", "message": "梳理全国和北京市的数字经济", "autonomous": True,
    })
    assert response.status == "needs_clarification"
    assert any(item.id == "area_scope" for item in response.options)


@pytest.mark.asyncio
async def test_autonomous_research_still_blocks_false_full_corpus_promises():
    response = await ResearchPreparationService().prepare({
        "mode": "qa", "message": "列出全部数字经济政策并准确统计篇数",
        "autonomous": True,
    })
    assert response.status == "blocked"
    assert response.request is None


@pytest.mark.asyncio
async def test_autonomous_three_turn_qa_uses_current_question_and_changed_region():
    service = ResearchPreparationService()
    messages = [
        "苏州市十四五养老服务规划有哪些人才培养要求？",
        "那它的资金支持有哪些？",
        "地区改为杭州市，分析资金支持措施。",
    ]
    previous = None
    responses = []
    for msg in messages:
        payload = {"mode": "qa", "message": msg, "autonomous": True}
        if previous is not None:
            payload["state"] = {"state_id": previous.state.state_id,
                                "revision": previous.state.revision}
        response = await service.prepare(payload)
        assert response.status == "ready"
        assert response.request is not None
        responses.append(response)
        previous = response

    first, second, third = responses
    assert first.request.question == messages[0]
    assert "资金支持" in second.request.question
    assert "人才培养要求" not in second.request.question
    assert "苏州市十四五养老服务规划" in second.request.question
    assert third.state.area == "杭州市"
    assert "资金支持措施" in third.request.question
    assert "杭州市" in third.request.question
    assert "苏州市" not in third.request.question
    assert "人才培养要求" not in third.request.question
    assert third.state.clarification_count == 0


@pytest.mark.asyncio
async def test_autonomous_qa_new_standalone_question_never_replays_old_topic():
    service = ResearchPreparationService()
    prior = await service.prepare({"mode": "qa", "message": "数字经济有什么政策？",
                                   "autonomous": True})
    later = await service.prepare({
        "mode": "qa", "message": "什么是科技创新？", "autonomous": True,
        "state": {"state_id": prior.state.state_id, "revision": prior.state.revision},
    })
    assert later.request.question == "什么是科技创新？"
    assert "数字经济" not in later.request.question


@pytest.mark.asyncio
async def test_autonomous_qa_new_turn_does_not_inherit_old_full_corpus_block():
    service = ResearchPreparationService()
    first = await service.prepare({
        "mode": "qa", "message": "列出全部数字经济政策", "autonomous": True,
    })
    # A blocked QA turn has not completed: a new standalone question should
    # start a new preparation state. The old token must not be treated as ready.
    assert first.status == "blocked"
    fresh = await service.prepare({
        "mode": "qa", "message": "数字经济产业支持有哪些？", "autonomous": True,
    })
    assert fresh.status == "ready"
