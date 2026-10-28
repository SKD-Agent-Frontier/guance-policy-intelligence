"""Mock checks of real-run guardrails only; never create an acceptance sample."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from guance_agent import business
from scripts import organize_real_run as script


@pytest.mark.parametrize("backend", ["mock", "sample"])
async def test_mock_backend_cannot_generate_real_sample(env, monkeypatch, tmp_path, backend):
    settings = replace(env[0], kb_backend=backend)
    sample = tmp_path / "sample.json"
    monkeypatch.setattr(script, "SAMPLE", sample)
    monkeypatch.setattr(script, "load_settings", lambda **_: settings)
    assert await script.acceptance() == 2
    assert not sample.exists()


async def test_mock_rag_warmup_failure_keeps_existing_sample(env, monkeypatch, tmp_path):
    settings = replace(env[0], kb_backend="rag", rag_store_project="mock-missing-store",
                       llm_base_url="https://example.invalid/v1", llm_api_key="mock-test-key")
    sample = tmp_path / "sample.json"
    sample.write_text("unchanged sentinel", encoding="utf-8")
    monkeypatch.setattr(script, "SAMPLE", sample)
    monkeypatch.setattr(business, "_MODULES", {})
    monkeypatch.setattr(script, "load_settings", lambda **_: settings)

    def unavailable(_):
        raise RuntimeError("mock missing runtime")

    monkeypatch.setattr(script, "warm_up", unavailable)
    assert await script.acceptance() == 2
    assert sample.read_text(encoding="utf-8") == "unchanged sentinel"


@pytest.mark.parametrize("change", [
    {"status": "failed"}, {"evidence_status": "insufficient"}, {"claims": []},
    {"structured": {"schema_version": "organize/1", "coverage": {"documents_summarized": 1}}},
    {"meta": {"answer_rebuilt": "number_verification"}},
])
def test_mock_failed_result_not_eligible_for_sample(env, change):
    fields = dict(status="completed", error=None, evidence_status="verified", claims=[object(), object()],
                  structured={"schema_version": "organize/1", "coverage": {"documents_summarized": 2}},
                  meta={"quote_verification": {"approved": 2}, "business_telemetry": {"model_calls": 1}})
    valid_shape = SimpleNamespace(**fields)
    assert script.sample_accepted(valid_shape, env[0])  # shape check only; no sample written
    assert not script.sample_accepted(SimpleNamespace(**{**fields, **change}), env[0])


def test_mock_redaction_preserves_numeric_types_and_json_structure():
    value = {"count": 123, "error": 'key="mock-key" endpoint=https://example.invalid',
             "nested": [None, {"quote": "mock-key"}]}
    cleaned = script.redact_strings(value, ("mock-key", "https://example.invalid"))
    assert cleaned["count"] == 123
    assert cleaned["error"] == 'key="[REDACTED]" endpoint=[REDACTED]'
    assert cleaned["nested"] == [None, {"quote": "[REDACTED]"}]
