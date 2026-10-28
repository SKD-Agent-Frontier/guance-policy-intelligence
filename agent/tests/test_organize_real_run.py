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
    monkeypatch.setattr(script, "load_settings", lambda **_: settings)
    assert business.get_module("organize") is not None
    warmed = []

    def unavailable(_):
        warmed.append(True)
        raise RuntimeError("mock missing runtime")

    monkeypatch.setattr(script, "warm_up", unavailable)
    assert await script.acceptance() == 2
    assert warmed == [True]
    assert sample.read_text(encoding="utf-8") == "unchanged sentinel"


async def test_registered_organize_success_submits_all_questions_without_reregistering(
    env, monkeypatch, tmp_path
):
    settings = replace(env[0], kb_backend="rag", rag_mode="bm25",
                       rag_store_project="mock-store",
                       llm_base_url="https://example.invalid/v1", llm_api_key="mock-test-key")
    registered_module = business.get_module("organize")
    assert registered_module is not None
    submits = []
    writes = []

    monkeypatch.setattr(script, "load_settings", lambda **_: settings)
    monkeypatch.setattr(script, "SAMPLE", tmp_path / "sample.json")
    monkeypatch.setattr(script, "warm_up", lambda _: None)
    monkeypatch.setattr(script, "close_kb", lambda: None)
    monkeypatch.setattr(script, "git_metadata", lambda: {"commit_sha": "mock", "working_tree_dirty": False})
    monkeypatch.setattr(script, "write_json", lambda path, payload: writes.append((path, payload)))

    async def submit(**kwargs):
        submits.append(kwargs)
        result = SimpleNamespace(
            run_id=f"mock-{len(submits)}",
            status="completed",
            error=None,
            evidence_status="verified",
            claims=["mock claim"],
            structured={"schema_version": "organize/1", "coverage": {
                "documents_summarized": script.MIN_SUMMARIZED_DOCS,
            }},
            meta={
                "quote_verification": {"approved": 1, "rejected": [], "unverified": []},
                "business_telemetry": {"model_calls": 1},
                "tool_calls": [],
            },
            model_dump=lambda **_: {"mock": True},
        )
        assert script.sample_accepted(result, settings)
        return result

    class MockClient:
        async def close(self):
            pass

    monkeypatch.setattr(script, "submit", submit)
    monkeypatch.setattr(script, "get_client", lambda _: MockClient())

    assert await script.acceptance() == 0
    assert len(submits) == 3
    assert all(call["task_type"] == "organize" for call in submits)
    assert [call["question"] for call in submits] == list(script.QUESTIONS)
    assert business.get_module("organize") is registered_module
    assert len(writes) == 4
    assert writes[1][0] == tmp_path / "sample.json"


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
