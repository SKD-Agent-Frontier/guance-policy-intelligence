"""已读证据不能被后续检索降级；有界会话内的核验事实不能静默丢失。"""
from types import SimpleNamespace

import pytest

from guance_agent.context import EvidenceRegistry
from guance_agent.qa_context import build_trusted_turn
from guance_agent.research_history import collect_scoped_history
from guance_agent.schemas import Claim, RunResult
from guance_agent.verify import verify_quotes

from .test_evidence_contract import make_ref


def _verified_turn(prefix, count=20):
    registry = EvidenceRegistry()
    ref = make_ref(source="kb_read", char_end=11315)
    registry.register(ref)
    claims = [Claim(text=f"{prefix}事实{index}", quotes=[f"{prefix}事实{index}"],
                    chunk_ids=[ref.chunk_id]) for index in range(count)]
    ctx = SimpleNamespace(evidence=registry, route=None,
                          qa_context_resolution={"status": "continued"},
                          trusted_turns=[], research_intent=None)
    result = RunResult(run_id="r", session_id="s", answer="", dropped_chunk_ids=[],
                       status="completed", error=None, claims=claims,
                       evidence_refs=[ref], evidence_status="verified",
                       meta={"quote_verification": {"approved": count, "unverified": 0}})
    return ctx, result


def test_search_after_read_preserves_read_provenance():
    registry = EvidenceRegistry()
    registry.register(make_ref(source="kb_read", char_end=11315))
    registry.register(make_ref())
    ref = registry.get(make_ref().chunk_id)
    assert ref.source == "kb_read"
    assert ref.complete


def test_search_preview_does_not_complete_a_partial_body_read():
    registry = EvidenceRegistry()
    registry.register(make_ref())
    registry.register(make_ref(source="kb_read", char_start=11164, char_end=11315))
    ref = registry.get(make_ref().chunk_id)
    assert ref.source == "kb_read"
    assert not ref.complete
    assert ref.observed_ranges == [(11164, 11315)]
    assert (ref.char_start, ref.char_end) == (11164, 11315)


def test_all_verified_facts_survive_turn_persistence():
    ctx, result = _verified_turn("当前")
    turn = build_trusted_turn("这些指标有哪些？", ctx, result)
    assert len(turn["verified_facts"]) == len(result.claims)
    assert turn["verified_facts"][-1]["text"] == result.claims[-1].text


def test_same_scope_history_keeps_later_facts_beyond_old_cap():
    turns = []
    for prefix in ("早期", "后续"):
        ctx, result = _verified_turn(prefix)
        turns.append(build_trusted_turn("继续补充指标", ctx, result))
    history = collect_scoped_history(turns, {"20699"})
    assert len(history["verified_facts"]) == 40
    assert history["verified_facts"][-1]["text"] == "后续事实19"


def test_persisted_fact_requires_every_chunk_to_have_read_provenance():
    ctx, result = _verified_turn("未读", count=1)
    ctx.evidence = EvidenceRegistry()
    ctx.evidence.register(make_ref())
    turn = build_trusted_turn("以上事实", ctx, result)
    assert turn["verified_facts"] == []
    assert turn["evidence_refs"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("count", "history_count", "expected"), [
    (25, 0, 20), (25, 25, 25), (121, 121, 120),
])
async def test_history_summary_has_bounded_capacity_to_recheck_accumulated_facts(
        count, history_count, expected):
    ctx, result = _verified_turn("核验", count=count)
    result.task_type = "qa"
    result.warnings = []
    result.meta["historical_facts_rechecked"] = history_count
    ctx.settings = SimpleNamespace(tool_timeout=2, log_content=False)
    ctx.kb = SimpleNamespace(verify_quote=lambda *_args, **_kwargs: True)
    await verify_quotes(result, ctx)
    assert len(result.claims) == expected
    assert result.meta["quote_verification"]["checked"] == expected
    assert result.meta["quote_verification"]["unverified"] == count - expected


@pytest.mark.asyncio
async def test_expanded_history_capacity_still_rejects_an_unsupported_quote():
    ctx, result = _verified_turn("核验", count=25)
    result.meta["historical_facts_rechecked"] = 25
    ctx.settings = SimpleNamespace(tool_timeout=2, log_content=False)
    ctx.kb = SimpleNamespace(verify_quote=lambda _cid, quote, *_args, **_kwargs:
                            quote != "核验事实24")
    await verify_quotes(result, ctx)
    assert len(result.claims) == 24
    assert result.meta["quote_verification"]["rejected"] == 1
    assert result.meta["quote_verification"]["unverified"] == 0
    assert all(claim.text != "核验事实24" for claim in result.claims)
