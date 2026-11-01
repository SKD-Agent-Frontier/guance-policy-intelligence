"""At most one budgeted, evidence-verified QA gap-repair execution."""
from __future__ import annotations

import asyncio
import time

from .literal_keywords import enforce_literal_result
from .output import build_result
from .research_quality import followup_gap
from .verify import verify_numbers, verify_quotes


async def supplement_one(result, task, s, ctx, rt, deadline, execute_once, recover):
    """Use the same scoped QA tools and verification gates; never replace a good run."""
    meta = {"attempted": False, "status": "not_needed"}
    result.meta["research_supplement"] = meta
    if (task.task_type != "qa" or result.status != "completed"
            or result.error or ctx is None or not result.claims
            or getattr(ctx.route, "kind", None) != "open"
            or result.meta.get("verification_incomplete")):
        meta["status"] = "ineligible"
        return
    gap = followup_gap(result, task.question)
    if gap is None:
        return
    meta["gap"] = gap
    if (deadline - time.monotonic() < 95 or
            rt.hooks.turns >= s.max_turns - 1 or
            len(task.question) > s.max_input_chars - 200):
        meta["status"] = "skipped_budget"
        return
    meta["attempted"] = True
    meta["status"] = "running"
    followup = task.model_copy(update={"question": (
        task.question + "\n【本轮补充研究】只补充尚缺的研究维度：「"
        + gap + "」。必须调用真实知识库搜集证据，严格区分已查得与未查得。"
        "不得改变用户原始对象、地区、时期范围；不重复此前事实。"
    )})
    followup_deadline = min(deadline - 40, time.monotonic() + 48)
    try:
        outcome = await asyncio.wait_for(
            execute_once(followup, s, ctx, rt, followup_deadline),
            timeout=max(0.1, followup_deadline - time.monotonic()))
        if outcome.error_code is not None:
            meta["status"] = "execution_unavailable"
            return
        outcome, _ = await recover(
            outcome, s, rt, followup_deadline,
            repair_budget={"remaining": 0})
        part = build_result(followup, outcome, ctx, rt.meta)
        if part.status != "completed" or part.error:
            meta["status"] = "no_verified_output"
            return
        remaining = max(0.1, min(8.0, deadline - time.monotonic() - 35))
        await asyncio.wait_for(verify_quotes(part, ctx), timeout=remaining)
        remaining = max(0.1, min(8.0, deadline - time.monotonic() - 35))
        await asyncio.wait_for(verify_numbers(part, ctx), timeout=remaining)
        enforce_literal_result(part, ctx)
    except (Exception, asyncio.CancelledError) as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        meta["status"] = "unavailable"
        meta["reason"] = type(exc).__name__
        return
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
