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


def test_actual_web_app_enables_experiment_only_for_multi(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from guance_agent.config import Settings

    from app import main as api

    monkeypatch.setattr(api, "warm_up", lambda _: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)

    async def close_client():
        return None

    monkeypatch.setattr(api, "aclose_client", close_client)
    for engine in ("controlled", "sdk", "hyper", "multi"):
        settings = Settings(
            qa_engine=engine,
            session_path=tmp_path / (engine + ".sqlite"),
            run_log=tmp_path / (engine + ".jsonl"),
        )
        with TestClient(api.create_app(settings=settings)) as client:
            response = client.post("/api/research/prepare", json={
                "mode": "qa",
                "message": "列出全部数字经济政策并准确统计篇数",
                "autonomous": True,
            })
            assert response.status_code == 200
            expected = "ready" if engine in {"hyper", "multi"} else "blocked"
            assert response.json()["status"] == expected
