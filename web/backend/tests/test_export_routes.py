import xml.etree.ElementTree as ET
from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document
from fastapi.testclient import TestClient
from guance_agent.config import Settings

from app import main as api


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "warm_up", lambda _settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)
    from unittest.mock import AsyncMock

    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    settings = Settings(run_log=tmp_path / "runs.jsonl", session_path=tmp_path / "sessions.sqlite")
    with TestClient(api.create_app(settings=settings)) as test_client:
        yield test_client


def base_result(**overrides):
    result = {
        "status": "completed",
        "task_type": "qa",
        "evidence_status": "verified",
        "answer": "# 结论\n基于来源的研究结果",
        "claims": [{
            "dimension": "资助范围",
            "text": "支持相关项目",
            "quotes": ["原文引句"],
            "chunk_ids": ["chunk-1"],
        }],
        "evidence_refs": [{
            "planid": "plan-1",
            "title": "政策文件",
            "area": "示例地区",
            "fyp": "2026",
            "section_path": "第三章",
            "chunk_id": "chunk-1",
            "char_start": 10,
            "char_end": 20,
        }],
    }
    result.update(overrides)
    return result


def test_docx_preserves_user_visible_result_and_unverified_warning(client):
    response = client.post("/api/exports/docx", json={
        "question": "研究问题",
        "result": base_result(evidence_status="insufficient", answer="正文\u0001仍可导出"),
    })

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    paragraphs = [paragraph.text for paragraph in Document(BytesIO(response.content)).paragraphs]
    report = "\n".join(paragraphs)
    assert "研究问题" in report
    assert "正文�仍可导出" in report
    assert "支持相关项目" in report
    assert "原文：原文引句" in report
    assert "政策文件" in report
    assert "证据未全部核验" in report


def test_xlsx_treats_formula_prefixes_as_text_and_bounds_joined_quotes(client):
    quotes = ["=SUM(1,1)", "\t+cmd", "-1", "@value", "引文" * 10_000]
    response = client.post("/api/exports/xlsx", json={
        "question": "研究",
        "result": base_result(claims=[{
            "text": "=HYPERLINK(\"https://example.invalid\")",
            "quotes": quotes,
            "chunk_ids": ["@chunk"],
        }], evidence_status="insufficient"),
    })

    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as workbook:
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet2.xml"))
        strings = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        assert not sheet.findall(".//x:f", ns)
        texts = [node.text or "" for node in strings.findall(".//x:t", ns)]
        assert "'=HYPERLINK(\"https://example.invalid\")" in texts
        joined_quotes = next(text for text in texts if text.startswith("'=SUM(1,1)"))
        assert "\t+cmd" in joined_quotes
        assert "-1" in joined_quotes
        assert "@value" in joined_quotes
        assert "本轮证据未全部核验，请核对来源原文。" in texts
        assert max(map(len, texts)) <= 32_000


@pytest.mark.parametrize("claims", ["not-a-list", {"text": "wrong-shape"}, [None, 3, {"quotes": "abc", "chunk_ids": 4}]])
def test_xlsx_handles_malformed_optional_collections(client, claims):
    response = client.post("/api/exports/xlsx", json={
        "question": "研究",
        "result": base_result(claims=claims, evidence_refs="not-a-list", structured=[None, "ok"]),
    })

    assert response.status_code == 200
    assert response.content.startswith(b"PK")


def test_export_rejects_incomplete_results_unsupported_format_and_extra_fields(client):
    incomplete = client.post("/api/exports/docx", json={
        "result": base_result(status="running"),
    })
    unsupported = client.post("/api/exports/pdf", json={"result": base_result()})
    extra = client.post("/api/exports/docx", json={
        "result": base_result(),
        "unexpected": "field",
    })

    assert incomplete.status_code == 422
    assert unsupported.status_code == 404
    assert extra.status_code == 422


def test_export_rejects_oversized_payload(client):
    response = client.post("/api/exports/xlsx", json={
        "result": base_result(answer="x" * 2_000_001),
    })

    assert response.status_code == 413


def test_export_rejects_streamed_oversized_body_before_json_parse(client):
    response = client.post(
        "/api/exports/docx",
        content=iter([b"x" * 1_000_000, b"x" * 1_000_001]),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
