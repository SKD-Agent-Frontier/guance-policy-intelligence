"""Bounded, iterative repair of measured QA evidence gaps."""
from __future__ import annotations

import asyncio
import time

from .evidence_coverage import build_coverage
from .literal_keywords import enforce_literal_result
from .output import build_result
from .qa_controlled import MAX_QUERIES, execute_controlled, finalize_controlled_facts
from .research_quality import followup_gap
from .verify import verify_numbers, verify_quotes

MAX_BATCH_ACTIONS = MAX_QUERIES
REPORT_TIME_RESERVE = 35.0
MIN_CONTROLLED_WINDOW = 75.0
MIN_LEGACY_WINDOW = 95.0


def _tool_count(ctx) -> int:
    return len(getattr(ctx, "tool_calls", []) or [])


def _query_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _actions(coverage: dict, result, question: str) -> list[dict]:
    actions = coverage.get("next_actions")
    if isinstance(actions, list):
        return [dict(item) for item in actions if isinstance(item, dict)
                and item.get("phase", "evidence") != "synthesis"
                and item.get("retrievable", item.get("actionable", True)) is not False]
    # Compatibility while older coverage producers are being upgraded.
    action = coverage.get("next_action")
    if isinstance(action, dict):
        return [dict(action)]
    gap = followup_gap(result, question)
    if gap:
        return [{"requirement_id": "legacy_gap", "focus": gap, "query": gap,
                 "target_planid": None, "gap_label": gap}]
    return []


def _coverage_snapshot(coverage: dict) -> dict:
    units = [item for item in coverage.get("requirement_coverage") or []
             if isinstance(item, dict)]
    return {
        "status": coverage.get("status"),
        "gaps": list(coverage.get("gaps") or []),
        "missing_requirements": list(coverage.get("missing_requirements") or []),
        "missing_evidence_requirement_ids": [
            item.get("id") for item in units
            if item.get("phase") == "evidence" and item.get("actionable")
            and item.get("status") != "covered" and item.get("id")
        ],
        "missing_synthesis_requirement_ids": [
            item.get("id") for item in units
            if item.get("phase") == "synthesis" and item.get("status") != "covered"
            and item.get("id")
        ],
    }


def _merge_verification_counts(result, part) -> None:
    current = result.meta.setdefault("quote_verification", {})
    added = (part.meta or {}).get("quote_verification") or {}
    for key in ("checked", "approved", "rejected", "unverified",
                "removed_claims", "removed_quotes"):
        if key in added:
            current[key] = int(current.get(key, 0) or 0) + int(added.get(key, 0) or 0)
    if added.get("unverified", 0):
        current["unverified"] = int(current.get("unverified", 0) or 0)
    if (part.meta or {}).get("verification_incomplete"):
        result.meta["verification_incomplete"] = True
    if (part.meta or {}).get("number_verification"):
        result.meta["number_verification"] = part.meta["number_verification"]


def _merge_report_candidates(result, part, accepted: set[tuple[str, tuple[str, ...]]]) -> None:
    if not part._qa_report_supplied:
        return
    candidates = [dict(item) for item in part._qa_report_candidates
                  if all(signature in accepted
                         for signature in item.get("support_signatures") or [])]
    dimensions = list(dict.fromkeys(
        result._qa_report_dimensions + list(part._qa_report_dimensions)))
    if len(dimensions) > 8 or len(result._qa_report_candidates) + len(candidates) > 12:
        return
    old_ids = {item["id"] for item in result._qa_report_candidates}
    for item in candidates:
        item["id"] = "supplement:" + item["id"]
        if item["id"] not in old_ids:
            result._qa_report_candidates.append(item)
    result._qa_report_dimensions = dimensions
    result._qa_report_supplied = bool(result._qa_report_candidates)
    report_meta = result.meta.setdefault("qa_report", {})
    report_meta["received"] = len(result._qa_report_candidates)


def _merge_verified_part(result, part) -> int:
    existing = {(claim.text, tuple(claim.chunk_ids)) for claim in result.claims}
    active_refs = {ref.chunk_id: ref for ref in part.evidence_refs}
    additions = [claim for claim in part.claims
                 if claim.quotes and claim.chunk_ids
                 and (claim.text, tuple(claim.chunk_ids)) not in existing
                 and set(claim.chunk_ids) <= set(active_refs)]
    if not additions:
        return 0

    accepted = {(claim.text, tuple(claim.chunk_ids)) for claim in additions}
    used_chunk_ids = {chunk_id for claim in additions for chunk_id in claim.chunk_ids}
    result.claims.extend(additions)
    existing_refs = {ref.chunk_id for ref in result.evidence_refs}
    result.evidence_refs.extend(
        active_refs[chunk_id] for chunk_id in used_chunk_ids
        if chunk_id not in existing_refs
    )
    _merge_verification_counts(result, part)
    _merge_report_candidates(result, part, accepted)
    if result.claims:
        result.evidence_status = "verified"
    return len(additions)


async def supplement_one(result, task, s, ctx, rt, deadline, execute_once, recover):
    """Repair all current gaps in deduplicated batches within the run budget."""
    meta = {
        "attempted": False, "status": "not_needed", "iterations": 0,
        "attempts": [], "stop_reason": None,
    }
    result.meta["research_supplement"] = meta
    route_kind = getattr(ctx.route, "kind", None) if ctx is not None else None
    if (task.task_type != "qa" or result.status != "completed" or result.error
            or ctx is None or route_kind not in {"open", "objects"}
            or getattr(ctx.route, "blocked", None)
            or result.meta.get("verification_incomplete")):
        meta.update(status="ineligible", stop_reason="ineligible")
        return

    controlled = (getattr(s, "kb_backend", None) == "rag"
                  and rt.meta.get("qa_execution_mode") == "bounded_retrieval")
    if not controlled and not any(claim.quotes and claim.chunk_ids for claim in result.claims):
        meta.update(status="ineligible", stop_reason="verified_source_context_missing")
        return
    tool_limit = max(0, int(getattr(s, "max_tool_calls", 20)))
    attempted_queries: set[str] = set()
    any_additions = 0
    while True:
        before = _tool_count(ctx)
        coverage = build_coverage(result, task.question, ctx)
        result.meta["evidence_coverage"] = coverage
        snapshot = _coverage_snapshot(coverage)
        meta["coverage_after_last_attempt"] = snapshot
        if coverage.get("status") == "complete":
            reason = ("report_required" if snapshot["missing_synthesis_requirement_ids"]
                      else "coverage_complete")
            meta.update(status=reason, stop_reason=reason)
            break

        actions = _actions(coverage, result, task.question)
        if not actions:
            snapshot = _coverage_snapshot(coverage)
            reason = ("report_required" if snapshot["missing_synthesis_requirement_ids"]
                      else "no_actions" if not coverage.get("gaps") else "no_action_for_gap")
            meta.update(status=reason, stop_reason=reason)
            break

        pending = []
        for action in actions:
            query = str(action.get("query") or action.get("focus") or "").strip()
            focus = str(action.get("focus") or action.get("query") or "").strip()
            if not query or not focus or len(query) > 200:
                continue
            if _query_key(query) in attempted_queries:
                continue
            pending.append({**action, "query": query, "focus": focus})
        if not pending:
            meta.update(status="repeated_queries", stop_reason="repeated_queries")
            break

        remaining = deadline - time.monotonic()
        minimum_window = MIN_CONTROLLED_WINDOW if controlled else MIN_LEGACY_WINDOW
        if remaining < minimum_window:
            meta.update(status="skipped_budget", stop_reason="budget_exhausted")
            break
        if _tool_count(ctx) >= tool_limit:
            meta.update(status="tool_budget_exhausted", stop_reason="tool_budget_exhausted")
            break
        if rt.hooks.turns >= s.max_turns - 1:
            meta.update(status="turn_budget_exhausted", stop_reason="turn_budget_exhausted")
            break
        if len(task.question) > s.max_input_chars - 200:
            meta.update(status="skipped_input_budget", stop_reason="input_budget_exhausted")
            break

        # A batch may combine several gaps only when they share the same trusted
        # target scope; otherwise each scope is checked separately.
        target_planid = pending[0].get("target_planid")
        batch = [action for action in pending
                 if action.get("target_planid") == target_planid][:MAX_BATCH_ACTIONS]
        queries = list(dict.fromkeys(action["query"] for action in batch))
        focuses = list(dict.fromkeys(action["focus"] for action in batch))
        for query in queries:
            attempted_queries.add(_query_key(query))
        previous_scope = getattr(ctx, "scope", None)
        if target_planid and previous_scope and target_planid not in previous_scope:
            meta.update(status="out_of_scope_gap", stop_reason="out_of_scope_gap")
            break
        if target_planid:
            ctx.scope = (target_planid,)

        meta["attempted"] = True
        meta["status"] = "running"
        meta["iterations"] += 1
        attempt = {
            "iteration": meta["iterations"],
            "requirement_ids": [action.get("requirement_id") for action in batch
                                if action.get("requirement_id")],
            "queries": queries,
            "gap_labels": [action.get("gap_label") for action in batch
                           if action.get("gap_label")],
            "coverage_before": _coverage_snapshot(coverage),
            "tool_calls_before": before,
        }
        try:
            followup = task.model_copy(update={"question": task.question})
            if controlled:
                rt.meta["qa_supplement_retrieval"] = {}
                if len(focuses) == 1:
                    controlled_call = execute_controlled(
                        followup, s, ctx, rt, deadline, focus=focuses[0],
                        exclude_chunks=frozenset(),
                    )
                else:
                    controlled_call = execute_controlled(
                        followup, s, ctx, rt, deadline, focuses=focuses,
                        exclude_chunks=frozenset(),
                    )
                outcome = await asyncio.wait_for(
                    controlled_call,
                    timeout=max(0.1, deadline - time.monotonic() - REPORT_TIME_RESERVE),
                )
            else:
                outcome = await asyncio.wait_for(
                    execute_once(followup, s, ctx, rt, deadline),
                    timeout=max(0.1, deadline - time.monotonic() - REPORT_TIME_RESERVE),
                )
            if outcome.error_code is not None:
                attempt["error_code"] = outcome.error_code
                attempt["tool_calls_after"] = _tool_count(ctx)
                meta["attempts"].append(attempt)
                meta.update(status="execution_unavailable", stop_reason="execution_unavailable")
                return
            outcome, _ = await recover(
                outcome, s, rt, deadline, repair_budget={"remaining": 0})
            part = build_result(followup, outcome, ctx, rt.meta)
            if part.status != "completed" or part.error:
                attempt["tool_calls_after"] = _tool_count(ctx)
                attempt["result_status"] = part.status
                meta["attempts"].append(attempt)
                meta.update(status="no_verified_output", stop_reason="no_verified_output")
                return

            if controlled:
                retrieval = dict(rt.meta.get("qa_supplement_retrieval", {}))
                attempt["retrieval"] = retrieval
                fresh = set(retrieval.get("read_chunks") or [])
                part.claims = [claim for claim in part.claims
                               if claim.chunk_ids and set(claim.chunk_ids) <= fresh]
                part.evidence_refs = [ref for ref in part.evidence_refs
                                      if ref.chunk_id in fresh]
                if not part.claims:
                    attempt["tool_calls_after"] = _tool_count(ctx)
                    attempt["new_claims"] = 0
                    meta["attempts"].append(attempt)
                    meta.update(status="no_new_read_claims", stop_reason="no_new_read_claims")
                    return

            remaining = max(0.1, min(8.0, deadline - time.monotonic() - REPORT_TIME_RESERVE))
            await asyncio.wait_for(verify_quotes(part, ctx), timeout=remaining)
            remaining = max(0.1, min(8.0, deadline - time.monotonic() - REPORT_TIME_RESERVE))
            await asyncio.wait_for(verify_numbers(part, ctx), timeout=remaining)
            enforce_literal_result(part, ctx)
            if controlled:
                finalize_controlled_facts(part)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bounded repair must not discard prior facts
            attempt["failure"] = type(exc).__name__
            attempt["tool_calls_after"] = _tool_count(ctx)
            meta["attempts"].append(attempt)
            meta.update(status="unavailable", stop_reason="unavailable")
            return
        finally:
            ctx.scope = previous_scope

        added = _merge_verified_part(result, part)
        any_additions += added
        if added:
            result.meta.pop("requirement_support", None)
            from .qa_report_verification import review_requirement_support

            await review_requirement_support(result, ctx, rt, deadline)
            attempt["requirement_support_review"] = result.meta.get(
                "requirement_support_review", "unavailable")
        updated_coverage = build_coverage(result, task.question, ctx)
        result.meta["evidence_coverage"] = updated_coverage
        attempt.update(
            tool_calls_after=_tool_count(ctx), new_claims=added,
            coverage_after=_coverage_snapshot(updated_coverage),
        )
        before_missing = set(attempt["coverage_before"].get(
            "missing_evidence_requirement_ids") or [])
        after_missing = set(attempt["coverage_after"].get(
            "missing_evidence_requirement_ids") or [])
        newly_satisfied = sorted(before_missing - after_missing)
        attempt["newly_satisfied_requirements"] = newly_satisfied
        meta["attempts"].append(attempt)
        if not added:
            meta.update(status="no_new_verified_facts", stop_reason="no_new_verified_facts")
            break
        if updated_coverage.get("status") == "complete":
            snapshot = _coverage_snapshot(updated_coverage)
            reason = ("report_required" if snapshot["missing_synthesis_requirement_ids"]
                      else "coverage_complete")
            meta.update(status=reason, stop_reason=reason)
            break
        if not newly_satisfied:
            remaining_actions = [
                action for action in _actions(updated_coverage, result, task.question)
                if _query_key(str(action.get("query") or action.get("focus") or ""))
                not in attempted_queries
            ]
            if not remaining_actions:
                meta.update(status="no_requirement_progress",
                            stop_reason="no_requirement_progress")
                break
        if _tool_count(ctx) >= tool_limit:
            meta.update(status="tool_budget_exhausted", stop_reason="tool_budget_exhausted")
            break

    meta["added_claims"] = any_additions
    if meta.get("stop_reason") in {"coverage_complete", "report_required"} and any_additions:
        meta["status"] = "verified_facts_added"
    meta["tool_calls_used"] = max(0, _tool_count(ctx) - int(
        meta["attempts"][0].get("tool_calls_before", _tool_count(ctx))
        if meta["attempts"] else _tool_count(ctx)))
    meta["coverage_after"] = _coverage_snapshot(result.meta.get("evidence_coverage") or {})
    meta["remaining_tool_calls"] = max(0, tool_limit - _tool_count(ctx))
    meta["remaining_seconds"] = max(0.0, round(deadline - time.monotonic(), 3))
