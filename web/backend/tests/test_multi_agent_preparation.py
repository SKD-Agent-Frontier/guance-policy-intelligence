"""Multi Agent preparation follows the opt-in bounded partial contract."""
import pytest

from app.preparation import ResearchPreparationService


@pytest.mark.asyncio
async def test_multi_preparation_allows_bounded_research_not_exhaustive_guarantee():
    response = await ResearchPreparationService(allow_bounded_partial=True).prepare({
        "mode": "qa", "message": "列出全部舆论治理政策并准确统计篇数",
        "autonomous": True,
    })
    assert response.status == "ready"
    assert response.request is not None
    assert any("不保证全库" in w for w in response.warnings)


@pytest.mark.asyncio
async def test_default_mode_still_blocks():
    response = await ResearchPreparationService().prepare({
        "mode": "qa", "message": "列出全部舆论治理政策并准确统计篇数",
        "autonomous": True,
    })
    assert response.status == "blocked"
    assert response.request is None
