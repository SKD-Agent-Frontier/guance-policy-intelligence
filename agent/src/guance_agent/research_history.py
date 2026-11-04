"""Reconstruct a trusted previous research result for summarize/analyze turns.

Only facts that passed the previous turn's quote/number checks are loaded.
There is no fallback to a raw previous answer or to an unverified claim list.
"""
from __future__ import annotations

import re
from dataclasses import replace

from .context import EvidenceRegistry, RunContext
from .routing import parse_areas, parse_fyps
from .schemas import Claim, EvidenceRef, RunResult, TaskRequest

SUMMARY_MAX_TURNS = 8


def requested_history_turn_limit(question: str) -> int | None:
    """Parse an explicit history window; vague '前几轮' stays bounded."""
    match = re.search(r"前(?:([一二三四五六七八九\d]+)轮|(几轮))", question or "")
    if match:
        if match.group(2):
            return 3
        token = match.group(1)
        numbers = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                   "六": 6, "七": 7, "八": 8, "九": 9}
        value = int(token) if token.isdigit() else numbers.get(token, 3)
        return max(1, min(value, SUMMARY_MAX_TURNS))
    summary_intent = re.search(r"整理|总结|摘要|归纳|综合|研析|汇总", question or "")
    history_reference = re.search(
        r"前面(?:的)?(?:讨论|内容|轮次)|上述(?:讨论|内容|轮次)|以上(?:讨论|内容)|"
        r"上文(?:讨论|内容)|此前(?:讨论|内容|研究)|之前(?:讨论|内容|轮次)",
        question or "",
    )
    return 3 if summary_intent and history_reference else None


def history_summary_matches_scope(question: str, prior: dict | None) -> bool:
    """Allow an explicit bounded summary to revisit an earlier matching scope.

    A comparison expansion may make the immediately previous turn broader
    than the original target. A summary naming that original target still
    refers to the bounded history unless the user explicitly replaces scope.
    """
    if (not requested_history_turn_limit(question) or not isinstance(prior, dict)
            or re.search(r"改为|改成|只看|仅看|不要旧对象|只研究新对象", question or "")):
        return False
    explicit_areas = set(parse_areas(question or ""))
    explicit_periods = set(parse_fyps(question or ""))
    scope = prior.get("user_scope") or {}
    prior_areas = {str(value) for value in scope.get("areas") or [] if value}
    prior_periods = {str(value) for value in scope.get("periods") or [] if value}
    for doc in [*(prior.get("documents") or []), *(prior.get("context_documents") or [])]:
        if not isinstance(doc, dict):
            continue
        if doc.get("area"):
            prior_areas.add(str(doc["area"]))
        if doc.get("fyp"):
            prior_periods.update(parse_fyps(str(doc["fyp"])))
    areas_match = (not explicit_areas or not prior_areas or any(
        current == known or current in known or known in current
        for current in explicit_areas for known in prior_areas
    ))
    periods_match = (not explicit_periods or not prior_periods
                     or bool(explicit_periods & prior_periods))
    return areas_match and periods_match


def _turn_identity(turn: dict) -> tuple[set[str], set[str]]:
    scope = turn.get("user_scope") or {}
    documents = [*(turn.get("documents") or []), *(turn.get("context_documents") or [])]
    ids = {str(doc.get("planid")) for doc in documents
           if isinstance(doc, dict) and doc.get("planid")}
    ids.update(str(value) for value in scope.get("planids", []) if value)
    titles = {str(doc.get("title")) for doc in documents
              if isinstance(doc, dict) and doc.get("title")}
    titles.update(str(value) for value in scope.get("titles", []) if value)
    return ids, titles


def scoped_turn_window(turns: list[dict], *, max_turns: int,
                       areas: set[str] | None = None,
                       periods: set[str] | None = None,
                       planids: set[str] | None = None,
                       titles: set[str] | None = None) -> list[dict]:
    """Return one contiguous same-scope window for a requested summary."""
    if max_turns <= 0:
        return []
    selected = []
    anchor_areas, anchor_periods = set(areas or ()), set(periods or ())
    anchor_ids, anchor_titles = set(planids or ()), set(titles or ())
    for turn in reversed(turns or []):
        if not isinstance(turn, dict):
            break
        if selected and turn.get("resolution_status") in {"scope_changed", "new_topic", "ambiguous"}:
            break
        scope = turn.get("user_scope") or {}
        turn_areas = {str(value) for value in scope.get("areas", []) if value}
        turn_periods = {str(value) for value in scope.get("periods", []) if value}
        turn_ids, turn_titles = _turn_identity(turn)
        if not anchor_areas:
            anchor_areas = turn_areas
        if not anchor_periods:
            anchor_periods = turn_periods
        if not anchor_ids and not anchor_titles:
            anchor_ids, anchor_titles = turn_ids, turn_titles
        if anchor_areas and turn_areas and anchor_areas.isdisjoint(turn_areas):
            break
        if anchor_periods and turn_periods and anchor_periods.isdisjoint(turn_periods):
            break
        if anchor_ids and turn_ids and anchor_ids.isdisjoint(turn_ids):
            break
        if anchor_titles and turn_titles and anchor_titles.isdisjoint(turn_titles):
            break
        selected.append(turn)
        if turn.get("resolution_status") not in {"continued", "extended"}:
            break
        if len(selected) >= max_turns:
            break
    return list(reversed(selected))


def collect_scoped_history(turns: list[dict], planids: set[str], *,
                           max_turns: int | None = None) -> dict | None:
    """Collect verified facts for a summary, stopping at the last scope boundary."""
    facts, refs, documents, requirements = {}, {}, {}, {}
    window = scoped_turn_window(turns, max_turns=max_turns or len(turns or []),
                                planids=planids)
    for turn in window:
        if not isinstance(turn, dict):
            continue
        for unit in (turn.get("user_scope") or {}).get("requirements") or []:
            if isinstance(unit, dict):
                key = str(unit.get("id") or repr(sorted(unit.items())))
                requirements.setdefault(key, dict(unit))
        quote_check = turn.get("quote_verification") or {}
        if quote_check.get("approved", 0) > 0 and quote_check.get("unverified") == 0:
            accepted = {ref["chunk_id"]: ref for ref in turn.get("evidence_refs") or []
                        if isinstance(ref, dict) and ref.get("planid") in planids
                        and isinstance(ref.get("chunk_id"), str)}
            for fact in turn.get("verified_facts") or []:
                if (not isinstance(fact, dict) or not fact.get("quotes")
                        or not fact.get("chunk_ids")
                        or not set(fact["chunk_ids"]) <= set(accepted)):
                    continue
                signature = (fact.get("text"), tuple(fact["chunk_ids"]))
                facts.setdefault(signature, fact)
                refs.update({cid: accepted[cid] for cid in fact["chunk_ids"]})
            for doc in turn.get("documents") or []:
                if isinstance(doc, dict) and doc.get("planid") in planids:
                    documents.setdefault(doc["planid"], doc)
        if turn.get("resolution_status") in {"scope_changed", "new_topic", "ambiguous"}:
            break
    if not facts:
        return None
    return {"documents": list(documents.values()), "verified_facts": list(facts.values()),
            "evidence_refs": list(refs.values()), "question": str(turns[-1].get("question") or ""),
            "quote_verification": {"approved": len(facts), "unverified": 0},
            "requirements": list(requirements.values())}


def collect_scoped_requirements(turns: list[dict], *, max_turns: int,
                               areas: set[str] | None = None,
                               periods: set[str] | None = None,
                               planids: set[str] | None = None,
                               titles: set[str] | None = None) -> list[dict]:
    """Return frozen user requirements from the requested contiguous history.

    This carries obligations into an explicit multi-turn summary; it does not
    carry claims or grant source identity. A replacement/new-topic boundary or
    a conflicting explicit area/period stops collection.
    """
    if max_turns <= 0:
        return []
    collected: list[dict] = []
    seen: set[str] = set()
    window = scoped_turn_window(turns, max_turns=max_turns, areas=areas,
                                periods=periods, planids=planids, titles=titles)
    for turn in window:
        scope = turn.get("user_scope") or {}
        for unit in scope.get("requirements", []) or []:
            if not isinstance(unit, dict):
                continue
            key = str(unit.get("id") or repr(sorted(unit.items())))
            if key not in seen:
                seen.add(key)
                collected.append(dict(unit))
    return collected


def latest_verified_turn(turns: list[dict] | None) -> dict | None:
    """Read through failed continuations, but never cross a topic/scope change.

    A failed follow-up may lose its claims, not its previously established
    document identity. Only an earlier *verified* result may be reused;
    unverified intervening answers are never promoted to trusted evidence.
    """
    if not turns:
        return None
    allowed: set[str] | None = None
    for turn in reversed(turns):
        if not isinstance(turn, dict):
            return None
        quote_meta = turn.get("quote_verification")
        quote_ok = (isinstance(quote_meta, dict)
                    and quote_meta.get("approved", 0) > 0
                    and quote_meta.get("unverified") == 0)
        verified = (quote_ok and turn.get("verified_facts")
                    and turn.get("evidence_refs"))
        if verified:
            document_ids = {str(doc.get("planid")) for doc in turn.get("documents", [])
                            if isinstance(doc, dict) and doc.get("planid")}
            if allowed is not None and (not document_ids or
                                        not document_ids <= allowed):
                return None
            return turn
        # A failed, explicitly continued research turn is a recoverable gap.
        # scope_changed/new_topic/ambiguous are hard history boundaries.
        if turn.get("resolution_status") != "continued":
            return None
        identities = {str(doc.get("planid"))
                      for doc in turn.get("context_documents", [])
                      if isinstance(doc, dict) and doc.get("planid")}
        if not identities:
            return None
        allowed = identities if allowed is None else allowed & identities
        if not allowed:
            return None
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
