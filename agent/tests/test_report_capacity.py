import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from guance_agent.qa_report_verification import _parse_review, _review_token_budget
from guance_agent.research_delivery import draft_verified_report
from guance_agent.runtime import Runtime
from guance_agent.schemas import Claim, RunResult

from .test_evidence_contract import make_ref


def test_review_capacity_grows_for_accumulated_work_with_a_configured_ceiling():
    payload = {"facts": [{}] * 34, "blocks": [{}] * 12, "requirements": [{}] * 35}
    assert 1500 < _review_token_budget(payload, 3000) <= 3000
    assert _review_token_budget(payload, 2000) <= 2000
    assert _review_token_budget({"facts": [{}], "blocks": [{}]}, 3000) == 1500


def test_review_diagnostics_identify_unknown_items_without_exposing_text():
    diagnostics = {}
    raw = json.dumps({"facts": [{"index": 99, "supported": True}],
                      "blocks": [{"id": "private-source-name", "supported": True}]})
    verdicts = _parse_review(raw, {0}, {"expected"}, diagnostics=diagnostics)
    assert verdicts[4]
    assert diagnostics["unknown_facts"] == diagnostics["unknown_blocks"] == 1
    assert "private-source-name" not in json.dumps(diagnostics)


def test_truncated_review_stays_invalid_and_reports_its_format_failure():
    diagnostics = {}
    assert _parse_review('{"facts":[', {0}, {"b"}, diagnostics=diagnostics) is None
    assert diagnostics == {"reason": "invalid_json"}


@pytest.mark.asyncio
async def test_text_telemetry_records_length_stop(env, monkeypatch):
    async def create(**_kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="length",
            message=SimpleNamespace(content="truncated"))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=20))

    monkeypatch.setattr("guance_agent.runtime.get_client", lambda _settings:
                        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    rt = Runtime(*env)
    await rt.complete_text([], max_tokens=20, timeout=1, purpose="review")
    assert rt.meta["text_model_requests"][0]["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_cancelled_text_request_is_counted_and_propagates(env, monkeypatch):
    async def create(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr("guance_agent.runtime.get_client", lambda _settings:
                        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    rt = Runtime(*env)
    with pytest.raises(asyncio.CancelledError):
        await rt.complete_text([], max_tokens=20, timeout=1, purpose="review")
    assert rt.meta["text_model_usage"]["failed_calls"] == 1
    assert rt.meta["text_model_requests"][0]["error"] == "CancelledError"


@pytest.mark.asyncio
async def test_reference_only_fact_block_is_filled_from_verified_claim():
    ref = make_ref(source="kb_read")
    claims = [Claim(text=text, chunk_ids=[ref.chunk_id], quotes=[text]) for text in (
        "建设农业示范基地。", "完善农业技术服务。")]
    result = RunResult(run_id="r", session_id="s", status="completed",
        evidence_status="verified", answer="", claims=claims, evidence_refs=[ref],
        dropped_chunk_ids=[], error=None,
        meta={"quote_verification": {"approved": 2, "unverified": 0}})

    async def complete(*_args, **_kwargs):
        return json.dumps({"dimensions": ["农业"], "blocks": [
            {"id": "fact", "title_path": ["农业"], "dimension": "农业", "kind": "fact",
             "supporting_claim_indexes": [0]},
            {"id": "analysis", "title_path": ["农业"], "dimension": "农业", "kind": "analysis",
             "text": "农业部署结合基地建设和技术服务。", "supporting_claim_indexes": [0, 1]},
        ]}, ensure_ascii=False)

    rt = SimpleNamespace(complete_text=complete, hooks=SimpleNamespace(turns=0),
        settings=SimpleNamespace(max_turns=12, llm_request_timeout=120))
    assert await draft_verified_report(result, "请分类归纳农业措施", rt,
                                       time.monotonic() + 150, force=True)
    assert len(result._qa_report_candidates) == 2
    fact = next(block for block in result._qa_report_candidates if block["kind"] == "fact")
    assert fact["text"] == claims[0].text
    assert fact["support_signatures"] == [(claims[0].text, tuple(claims[0].chunk_ids))]
    assert result.structured is None
