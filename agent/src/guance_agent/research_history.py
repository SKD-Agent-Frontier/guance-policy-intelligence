"""Reconstruct a trusted previous research result for summarize/analyze turns.

Only facts that passed the previous turn's quote/number checks are loaded.
There is no fallback to a raw previous answer or to an unverified claim list.
"""
from __future__ import annotations

from dataclasses import replace

from .context import EvidenceRegistry, RunContext
from .schemas import Claim, EvidenceRef, RunResult, TaskRequest


def latest_verified_turn(turns: list[dict] | None) -> dict | None:
    """Only the immediately latest turn may be reused.

    An unanswered/empty boundary must not resurrect an older policy from the
    same session just because a pronoun follow-up occurs later.
    """
    if not turns:
        return None
    turn = turns[-1]
    quote_meta = turn.get("quote_verification") if isinstance(turn, dict) else None
    quote_ok = (isinstance(quote_meta, dict)
                and quote_meta.get("approved", 0) > 0
                and quote_meta.get("unverified") == 0)
    if (isinstance(turn, dict)
            and quote_ok
            and turn.get("verified_facts")
            and turn.get("evidence_refs")):
        return turn
    return None


def _historical_registry(turn: dict) -> tuple[EvidenceRegistry, set[str]]:
    registry = EvidenceRegistry()
    accepted: set[str] = set()
    for value in turn.get("evidence_refs") or []:
        try:
            ref = EvidenceRef.model_validate(value)
        except (ValueError, TypeError):
            continue
        registry.register(ref)
        if registry.get(ref.chunk_id) is not None:
            accepted.add(ref.chunk_id)
    return registry, accepted


def build_historical_state(ctx: RunContext, task: TaskRequest, turn: dict):
    """Return (result, historical_context) or (None, None)."""
    registry, accepted = _historical_registry(turn)
    if not accepted:
        return None, None
    claims: list[Claim] = []
    used: set[str] = set()
    for value in turn.get("verified_facts") or []:
        try:
            claim = Claim.model_validate(value)
        except (ValueError, TypeError):
            continue
        if (not claim.quotes or not claim.chunk_ids
                or not set(claim.chunk_ids) <= accepted):
            continue
        claims.append(claim)
        used.update(claim.chunk_ids)
    if not claims:
        return None, None
    refs: list[EvidenceRef] = []
    for chunk_id in used:
        ref = registry.get(chunk_id)
        if ref is not None:
            refs.append(ref)
    quote_check = dict(turn.get("quote_verification") or {})
    if not quote_check:
        quote_check = {"checked": len(claims), "approved": len(claims),
                       "rejected": 0, "unverified": 0}
    result = RunResult(
        run_id=task.run_id,
        session_id=task.session_id,
        status="completed",
        evidence_status="verified",
        answer="",
        claims=claims,
        evidence_refs=refs,
        dropped_chunk_ids=[],
        error=None,
        meta={
            "quote_verification": quote_check,
            "qa_execution_mode": "historical_synthesis",
            "historical_reuse": True,
            "historical_turn_question": str(turn.get("question") or "")[:1000],
            "research_intent": getattr(ctx, "research_intent", None),
        },
        task_type="qa",
    )
    historical = replace(
        ctx,
        evidence=registry,
        question=task.question,
        tool_calls=[],
        scope=None,
        qa_followup=None,
        research_intent=getattr(ctx, "research_intent", None),
    )
    return result, historical
