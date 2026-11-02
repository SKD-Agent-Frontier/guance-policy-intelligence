"""At most one budgeted, evidence-verified QA gap-repair execution."""
from __future__ import annotations

import asyncio
import time

from .literal_keywords import enforce_literal_result
from .output import build_result
from .qa_controlled import execute_controlled, finalize_controlled_facts
from .research_quality import followup_gap
from .research_targets import extract_targets
from .verify import verify_numbers, verify_quotes


async def supplement_one(result, task, s, ctx, rt, deadline, execute_once, recover):
    """Use the same scoped QA tools and verification gates; never replace a good run."""
    meta = {"attempted": False, "status": "not_needed"}
    result.meta["research_supplement"] = meta
    coverage = result.meta.get("evidence_coverage") or {}
    action = coverage.get("next_action") or {}
    route_kind = getattr(ctx.route, "kind", None) if ctx is not None else None
    route_planids = list(getattr(ctx.route, "planids", ()) or ()) if ctx is not None else []
    multi_scope = route_kind == "objects" and len(route_planids) > 1
    if (task.task_type != "qa" or result.status != "completed"
            or result.error or ctx is None or not result.claims
            or (route_kind != "open" and not multi_scope)
            or result.meta.get("verification_incomplete")):
        meta["status"] = "ineligible"
        return
    gap = action.get("query") or followup_gap(result, task.question)
    if gap is None:
        return
    target_planid = action.get("target_planid")
    meta["gap"] = gap
    meta["target_planid"] = target_planid
    meta["coverage_before"] = {
        "status": coverage.get("status"),
        "missing_dimensions": coverage.get("missing_dimensions"),
        "missing_planids": coverage.get("missing_planids"),
        "multi_document_required": coverage.get("multi_document_required"),
        "multi_document_satisfied": coverage.get("multi_document_satisfied"),
    }
    if (deadline - time.monotonic() < (70 if extract_targets(task.question) else 95) or
            rt.hooks.turns >= s.max_turns - 1 or
            len(task.question) > s.max_input_chars - 200):
        meta["status"] = "skipped_budget"
        return
    meta["attempted"] = True
    meta["status"] = "running"
    # The user question and trusted scope remain authoritative. The focused
    # program search receives the gap separately, not as a user instruction.
    followup = task.model_copy(update={"question": task.question})
    controlled_mode = (getattr(s, "kb_backend", None) == "rag"
                       and rt.meta.get("qa_execution_mode") == "bounded_retrieval")
    # In real-RAG research, preserve a meaningful window for report drafting
    # and semantic review after this single supplemental attempt.
    if controlled_mode and deadline - time.monotonic() < 75:
        meta["status"] = "skipped_budget"
        meta["attempted"] = False
        return
    followup_deadline = min(
        deadline - (35 if controlled_mode else 40),
        time.monotonic() + (35 if controlled_mode else 48),
    )
    previous_scope = getattr(ctx, "scope", None)
    if target_planid:
        # Supplement must stay inside the measured missing document; never
        # let a global search satisfy a document-specific coverage gap.
        ctx.scope = (target_planid,)
    try:
        # Real-RAG QA uses the same bounded program-side retrieval as the
        # initial answer. The legacy SDK path remains for mock/backward tests.
        controlled = controlled_mode
        if controlled:
            seen = frozenset(ref.chunk_id for ref in result.evidence_refs)
            outcome = await asyncio.wait_for(
                execute_controlled(
                    followup, s, ctx, rt, followup_deadline, focus=gap,
                    exclude_chunks=seen,
                ),
                timeout=max(0.1, followup_deadline - time.monotonic()),
            )
        else:
            outcome = await asyncio.wait_for(
                execute_once(followup, s, ctx, rt, followup_deadline),
                timeout=max(0.1, followup_deadline - time.monotonic()),
            )
        if outcome.error_code is not None:
            meta["status"] = "execution_unavailable"
            meta["error_code"] = outcome.error_code
            if controlled:
                meta["retrieval"] = dict(rt.meta.get("qa_supplement_retrieval", {}))
            return
        outcome, _ = await recover(
            outcome, s, rt, followup_deadline,
            repair_budget={"remaining": 0})
        part = build_result(followup, outcome, ctx, rt.meta)
        if part.status != "completed" or part.error:
            meta["status"] = "no_verified_output"
            return
        if controlled:
            # A source present in the shared registry is NOT newly retrieved
            # evidence. Only this controlled follow-up's read chunks qualify.
            details = dict(rt.meta.get("qa_supplement_retrieval", {}))
            meta["retrieval"] = details
            fresh = set(details.get("read_chunks", []))
            part.claims = [claim for claim in part.claims
                           if claim.chunk_ids and set(claim.chunk_ids) <= fresh]
            part.evidence_refs = [ref for ref in part.evidence_refs
                                  if ref.chunk_id in fresh]
            if not part.claims:
                meta["status"] = "no_new_read_claims"
                return
        remaining = max(0.1, min(8.0, deadline - time.monotonic() - 35))
        await asyncio.wait_for(verify_quotes(part, ctx), timeout=remaining)
        remaining = max(0.1, min(8.0, deadline - time.monotonic() - 35))
        await asyncio.wait_for(verify_numbers(part, ctx), timeout=remaining)
        enforce_literal_result(part, ctx)
        if controlled:
            finalize_controlled_facts(part)
    except (Exception, asyncio.CancelledError) as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        meta["status"] = "unavailable"
        meta["reason"] = type(exc).__name__
        return
    finally:
        ctx.scope = previous_scope
    existing = {(claim.text, tuple(claim.chunk_ids)) for claim in result.claims}
    additions = [claim for claim in part.claims if claim.quotes and claim.chunk_ids
                 and (claim.text, tuple(claim.chunk_ids)) not in existing]
    active = {ref.chunk_id for ref in part.evidence_refs}
    additions = [claim for claim in additions if set(claim.chunk_ids) <= active]
    if not additions:
        meta["status"] = "no_new_verified_facts"
        return
    accepted = {(claim.text, tuple(claim.chunk_ids)) for claim in additions}
    result.claims.extend(additions)
    existing_refs = {ref.chunk_id for ref in result.evidence_refs}
    result.evidence_refs.extend(ref for ref in part.evidence_refs
                                if ref.chunk_id not in existing_refs
                                and any(ref.chunk_id in claim.chunk_ids for claim in additions))
    if part._qa_report_supplied:
        candidates = [dict(item) for item in part._qa_report_candidates
                      if all(signature in accepted
                             for signature in item["support_signatures"])]
        dims = list(dict.fromkeys(result._qa_report_dimensions
                                  + list(part._qa_report_dimensions)))
        count = len(result._qa_report_candidates) + len(candidates)
        if len(dims) <= 8 and count <= 12:
            old_ids = {item["id"] for item in result._qa_report_candidates}
            for item in candidates:
                item["id"] = "supplement:" + item["id"]
                if item["id"] in old_ids:
                    continue
                result._qa_report_candidates.append(item)
            result._qa_report_dimensions = dims
            result._qa_report_supplied = True
            report_meta = result.meta.setdefault("qa_report", {})
            report_meta["received"] = len(result._qa_report_candidates)
    meta.update(status="verified_facts_added", added_claims=len(additions))
