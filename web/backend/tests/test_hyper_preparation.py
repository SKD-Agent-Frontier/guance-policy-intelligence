"""Experimental bounded-coverage preparation never claims full enumeration."""
import pytest
from fastapi.testclient import TestClient
from guance_agent.config import Settings

from app import main as api
from app.preparation import ResearchPreparationService


@pytest.mark.asyncio
async def test_default_still_blocks_full_corpus_claims():
    response = await ResearchPreparationService().prepare({
        "mode": "qa",
        "message": "列出所有舆论相关政策并准确统计篇数",
        "autonomous": True,
    })
    assert response.status == "blocked"
    assert response.request is None


@pytest.mark.asyncio
async def test_experiment_allows_bounded_partial_with_explicit_warning():
    response = await ResearchPreparationService(allow_bounded_partial=True).prepare({
        "mode": "qa",
        "message": "列出所有舆论相关政策并准确统计篇数",
        "autonomous": True,
    })
    assert response.status == "ready"
    assert response.request is not None
    assert response.request.question == "列出所有舆论相关政策并准确统计篇数"
    assert any("不保证全库" in warning for warning in response.warnings)
    assert "未证明全量" in response.message


@pytest.mark.asyncio
async def test_experiment_does_not_bypass_ambiguity_gate():
    response = await ResearchPreparationService(allow_bounded_partial=True).prepare({
        "mode": "qa",
        "message": "列出所有北京市和全国范围的舆论相关政策",
        "autonomous": True,
    })
    assert response.status == "needs_clarification"
    assert response.request is None


def test_web_app_only_enables_partial_gate_for_hyper(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "warm_up", lambda _: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)

    async def close_client():
        return None

    monkeypatch.setattr(api, "aclose_client", close_client)
    for engine in ("controlled", "sdk", "hyper"):
        settings = Settings(
            qa_engine=engine,
            session_path=tmp_path / (engine + ".sqlite"),
            run_log=tmp_path / (engine + ".jsonl"),
        )
        with TestClient(api.create_app(settings=settings)) as client:
            response = client.post("/api/research/prepare", json={
                "mode": "qa", "message": "列出全部数字经济政策并准确统计篇数",
                "autonomous": True,
            })
            assert response.status_code == 200
            assert response.json()["status"] == ("ready" if engine == "hyper" else "blocked")
