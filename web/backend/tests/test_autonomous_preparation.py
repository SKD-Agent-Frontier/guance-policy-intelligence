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
