"""Deterministic last-mile projection for public research results.

The gateway does not create evidence or ask a model to review prose. It only
projects claims and report blocks that have already passed the verifier and,
for analysis, the existing semantic review.
"""
from __future__ import annotations

from copy import deepcopy

from .business.delivery import business_notice, project_business
from .qa_boundary import boundary_reply
from .research_delivery import _render_structured_report
from .review_receipts import fact_signature
from .schemas import TaskRequest
from .verify import block_numbers_valid, is_number_verified_claim, is_quote_verified_claim

_EMPTY_ANSWER = "本轮没有取得足够可信的政策事实，无法形成研究结论。"


def _allowed_planids(result, task: TaskRequest, ctx) -> set[str] | None:
    bounds: list[set[str]] = []
    if task.targets is not None:
        bounds.append(set(task.targets))
    scope = getattr(ctx, "scope", None) if ctx is not None else None
    if scope is not None:
        bounds.append(set(scope))
    route = getattr(ctx, "route", None) if ctx is not None else None
    if route is None and isinstance(result.route, dict):
        route_kind = result.route.get("kind")
        route_ids = result.route.get("planids")
    else:
        route_kind = getattr(route, "kind", None)
        route_ids = getattr(route, "planids", None)
    if route_kind == "objects" and route_ids:
        bounds.append(set(route_ids))
    if not bounds:
        return None
    allowed = bounds[0]
    for bound in bounds[1:]:
        allowed &= bound
    return allowed


def _trusted_claims(result, task: TaskRequest, ctx, receipts, state, runtime):
    if ctx is None:
        return [], {}
    if (getattr(ctx, "run_id", task.run_id) != task.run_id
            or result.run_id != task.run_id or result.session_id != task.session_id):
        return [], {}
    result_refs = {ref.chunk_id: ref for ref in result.evidence_refs}
    allowed_planids = _allowed_planids(result, task, ctx)
    accepted = []
    refs = {}
    for claim in result.claims:
        if (not claim.chunk_ids or not claim.quotes
                or not is_quote_verified_claim(result, claim)):
            continue
        candidate_refs = {}
        valid = True
        for chunk_id in dict.fromkeys(claim.chunk_ids):
            output_ref = result_refs.get(chunk_id)
            registered_ref = ctx.evidence.get(chunk_id)
            if (output_ref is None or registered_ref is None
                    or output_ref != registered_ref or registered_ref.source != "kb_read"
                    or not registered_ref.observed_ranges
                    or (runtime is not None and not runtime.document_ref_matches(registered_ref))
                    or (allowed_planids is not None
                        and registered_ref.planid not in allowed_planids)):
                valid = False
                break
            candidate_refs[chunk_id] = output_ref
        if valid and state is not None:
            from .research_core import _identity_matches

            valid = all(
                (state.authorized_scope is None or ref.planid in state.authorized_scope)
                and ref.planid not in state.disallowed_planids
                and _identity_matches(state, ref.area, ref.fyp)
                for ref in candidate_refs.values())
        committed_draft = state is not None and (receipts.blocks or (state.check_result or {}).get("audit_performed"))
        verbatim = (not committed_draft and len(claim.chunk_ids) == 1
                    and claim.text.strip() in claim.quotes)
        reviewed = fact_signature(claim, candidate_refs) in receipts.facts if valid else False
        if valid and (verbatim or reviewed) and is_number_verified_claim(result, claim):
            accepted.append(claim)
            refs.update(candidate_refs)
    return accepted, refs


def _block_claims(block: dict, claims: list) -> list | None:
    indexes = block.get("supporting_claim_indexes")
    if (not isinstance(indexes, list) or not indexes
            or any(type(index) is not int or index < 0 or index >= len(claims)
                   for index in indexes)
            or len(set(indexes)) != len(indexes)):
        return None
    dependencies = [claims[index] for index in indexes]
    expected_chunks = list(dict.fromkeys(
        chunk_id for claim in dependencies for chunk_id in claim.chunk_ids))
    chunks = block.get("supporting_chunk_ids")
    if (not isinstance(chunks, list)
            or any(not isinstance(chunk, str) for chunk in chunks)
            or set(chunks) != set(expected_chunks)
            or len(chunks) != len(set(chunks))):
        return None
    return dependencies


def _remap_report_blocks(structured: dict | None, index_map: dict[int, int]) -> dict | None:
    if not isinstance(structured, dict):
        return None
    report = deepcopy(structured)
    kept = []
    blocks = report.get("blocks")
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        indexes = block.get("supporting_claim_indexes")
        if (not isinstance(indexes, list) or not indexes
                or any(type(index) is not int or index not in index_map for index in indexes)):
            continue
        block["supporting_claim_indexes"] = [index_map[index] for index in indexes]
        kept.append(block)
    report["blocks"] = kept
    return report


def _approved_report_blocks(result, ctx, claims, refs, receipts) -> list[dict]:
    structured = result.structured
    verification = structured.get("verification") if isinstance(structured, dict) else None
    if (not isinstance(structured, dict)
            or structured.get("schema_version") != "qa-report/1"
            or not isinstance(verification, dict)
            or verification.get("facts") != "semantic_reviewed_against_quotes"):
        return []

    accepted = []
    for block in structured["blocks"]:
        block_id = block.get("id")
        kind = block.get("kind")
        dependencies = _block_claims(block, claims)
        if (not isinstance(block_id, str) or not block_id or dependencies is None
                or kind not in ("fact", "analysis")):
            continue
        text = block.get("text")
        title_path = block.get("title_path")
        if (not isinstance(text, str) or not text.strip()
                or not isinstance(title_path, list)
                or any(not isinstance(title, str) for title in title_path)):
            continue
        if not block_numbers_valid("\n".join([*title_path, text]), dependencies, ctx, result):
            continue
        if receipts.block_signature(block, dependencies, refs) not in receipts.blocks:
            continue
        if kind == "fact":
            # Fact blocks may summarize several claims, but cannot cite facts
            # outside the dependency set.
            supported_texts = {claim.text.strip() for claim in dependencies}
            joined_text = "；".join(claim.text.strip() for claim in dependencies)
            if text.strip() not in supported_texts and text.strip() != joined_text:
                continue
        accepted.append(block)
    return accepted


def _set_structured_report(result, original: dict, blocks: list[dict], state) -> None:
    dimensions = list(dict.fromkeys(block["dimension"] for block in blocks))
    if state is not None:
        requirements = [item.id for item in state.requirements]
        covered_set = {rid for block in blocks for rid in block.get("requirement_ids", [])}
    else:
        requirements = result._qa_report_dimensions or dimensions
        covered_set = {dim for block in blocks for dim in
                       (block.get("required_dimensions") or [block["dimension"]])}
    covered = [item for item in requirements if item in covered_set]
    missing = [item for item in requirements if item not in covered_set]
    rejected = len(blocks) != len(original["blocks"])
    original_coverage = original.get("coverage")
    complete = (isinstance(original_coverage, dict)
                and original_coverage.get("status") == "complete"
                and not rejected and not missing)
    result.structured = {
        "schema_version": "qa-report/1", "dimensions": dimensions, "blocks": blocks,
        "coverage": {"covered": covered, "missing": missing,
                     "status": "complete" if complete else "partial"},
        "verification": {"facts": "semantic_reviewed_against_quotes",
                         "blocks": ("block_reviewed" if state is not None else
                                    "model_reviewed_evidence_linked_semantics_not_proven")},
    }
    core_delivery = result.meta.get("research_core_delivery")
    if isinstance(core_delivery, dict):
        core_delivery = deepcopy(core_delivery)
        core_delivery["blocks"] = blocks
        if rejected:
            core_delivery["semantic_review"] = "partial"
        result.meta["research_core_delivery"] = core_delivery


def _sync_research_coverage(result, state, blocks, index_map):
    """Keep API coverage and claim indexes aligned with the actual delivery."""
    if state is None:
        return
    quality = result.meta.get("research_quality") or {}
    units = quality.get("requirement_coverage") or []
    links = {}
    for block in blocks:
        for requirement_id in block.get("requirement_ids", []):
            links.setdefault(requirement_id, set()).update(block["supporting_claim_indexes"])
    for unit in units:
        retained = {index_map[i] for i in unit.get("evidence_claim_indexes", []) if i in index_map}
        retained.intersection_update(links.get(unit["id"], ()))
        unit.update(status="covered" if retained else "missing",
                    evidence_claim_indexes=sorted(retained))
    quality["audited_report_blocks"] = len(blocks)
    quality["audited_analysis_blocks"] = sum(block["kind"] == "analysis" for block in blocks)
    for req in state.requirements:
        if req.id not in links:
            req.status = "open"
    if not blocks or any(req.status != "delivered" for req in state.requirements):
        status = "partial" if result.claims else "insufficient"
        quality["status"] = status
        result.meta["research_completion"] = {"status": status}
        if core := result.meta.get("research_core"):
            core["status"] = "partial"
            core["stop_reason"] = core.get("stop_reason") or "有研究要求缺少通过交付核验的证据"
    result.meta["research_quality"] = quality


def _failure_answer(result) -> str:
    if result.error is not None:
        return result.error.message
    return "本轮未能完成政策研究，暂不发布未经最终核验的内容。"




async def finalize_public_delivery(result, task: TaskRequest, ctx, runtime, *,
                                   research_state=None):
    """Rebuild the outward payload from verifier-authorized state.

    Call after execution, semantic review, runtime completion checks, and every
    result mutation. The function performs no model calls or KB reads; every
    surviving claim must carry current quote- and number-verifier receipts.
    """
    receipts = (research_state.review_receipts if research_state is not None
                else result._review_receipts)
    original_structured = result.structured
    original_claims = list(result.claims)
    claims, refs_by_id = _trusted_claims(result, task, ctx, receipts, research_state, runtime)
    if task.task_type == "extract":
        from .schemas import DIMENSIONS

        allowed_dimensions = set(task.dimensions or DIMENSIONS)
        claims = [claim for claim in claims if claim.dimension in allowed_dimensions]
        used_ids = {cid for claim in claims for cid in claim.chunk_ids}
        refs_by_id = {cid: ref for cid, ref in refs_by_id.items() if cid in used_ids}
    indexes = {id(claim): index for index, claim in enumerate(original_claims)}
    index_map = {indexes[id(claim)]: index for index, claim in enumerate(claims)}
    result.claims = claims
    result.evidence_refs = list(refs_by_id.values())
    result.structured = _remap_report_blocks(original_structured, index_map)
    approved_blocks = _approved_report_blocks(result, ctx, claims, refs_by_id, receipts)
    _sync_research_coverage(result, research_state, approved_blocks, index_map)
    if approved_blocks:
        _set_structured_report(result, original_structured, approved_blocks, research_state)
        result.answer = _render_structured_report(result.structured)
        included = {index for block in approved_blocks
                    for index in block["supporting_claim_indexes"]}
        extra = ["- " + claim.text for index, claim in enumerate(claims)
                 if index not in included]
        if extra:
            result.answer += "\n\n" + "\n".join(extra)
        coverage = result.structured["coverage"]
        if result.error or result.status != "completed":
            coverage["status"] = "partial"
        complete = (coverage.get("status") == "complete"
                    and (result.meta.get("research_quality") or {}).get("status") == "complete")
        if task.task_type != "qa":
            result.structured = project_business(task, claims, refs_by_id, research_state,
                                                 approved_blocks)
            if research_state is not None:
                completion = (result.structured or {}).get("task_completion", {})
                complete = (complete and not research_state.capability_insufficient.get(task.task_type)
                            and completion.get("status") not in {"partial", "insufficient"})
                if notice := business_notice(task, research_state, result.structured):
                    result.answer += "\n\n" + notice
        result.evidence_status = "verified"
        result.meta["research_delivery"] = {
            "format": "evidence_linked_report",
            "status": "complete" if complete else "partial",
            "audited_analysis": any(block["kind"] == "analysis" for block in approved_blocks),
            "source_planids": list(dict.fromkeys(ref.planid for ref in refs_by_id.values())),
        }
        if research_state is not None:
            missing = set(coverage.get("missing") or ())
            labels = [req.label for req in research_state.requirements if req.id in missing]
            if labels:
                result.answer += "\n\n尚未完成的要求：\n" + "\n".join("- " + label for label in labels)
        return result

    recovery = result.meta.get("execution_recovery") if task.task_type == "qa" else None
    if recovery:
        result.claims = claims
        result.evidence_refs = list(refs_by_id.values())
        result.structured = None
        if recovery.get("kind") == "verified_verbatim_excerpts" and claims:
            result.answer = (
                "本轮模型未能完成研究；以下仅为已逐字核验的原文节选，不构成研究分析。\n\n"
                + "\n".join("- " + claim.text for claim in claims)
            )
        elif result.error is not None and not claims:
            result.answer = _failure_answer(result)
        elif recovery.get("kind") == "empty_search_only" and not claims:
            result.answer = (
                "本轮检索未取得可引用的政策正文；这不代表相关政策不存在。"
            )
        else:
            result.answer = _EMPTY_ANSWER
            result.structured = None
        result.evidence_status = "insufficient" if claims else "none"
        result.meta["research_delivery"] = {
            "format": "verbatim_excerpts_or_no_results",
            "status": "partial" if claims else "insufficient",
            "audited_analysis": False,
            "source_planids": list(dict.fromkeys(ref.planid for ref in refs_by_id.values())),
        }
        return result

    # Business structure is reconstructed from surviving facts; its unreviewed
    # prose and derived analysis are not passed through.
    result.structured = (None if task.task_type == "qa" else
                         project_business(task, claims, refs_by_id, research_state))
    if claims:
        result.answer = "\n".join("- " + claim.text for claim in claims)
    else:
        if result.error is not None:
            result.answer = _failure_answer(result)
        elif notice := getattr(ctx, "delivery_notice", None):
            result.answer = notice
        elif (result.meta.get("qa_execution_subtype") == "boundary_response"
              and (boundary := boundary_reply(task.question))):
            result.answer = boundary
        elif result.status != "completed":
            result.answer = _failure_answer(result)
        else:
            result.answer = _EMPTY_ANSWER
    if claims and task.task_type == "qa" and research_state is not None:
        result.answer = "本轮研究未完成，以下为已核验事实或逐字原文。\n\n" + result.answer
        gaps = [req.label for req in research_state.requirements if req.status != "delivered"]
        if gaps:
            result.answer += "\n\n尚未完成的要求：\n" + "\n".join("- " + label for label in gaps)
    if (task.task_type != "qa" and research_state is not None and result.error is None
            and not getattr(ctx, "delivery_notice", None)):
        if notice := business_notice(task, research_state, result.structured):
            result.answer = result.answer + "\n\n" + notice if claims else notice
    quality = result.meta.get("research_quality")
    quality_status = quality.get("status") if isinstance(quality, dict) else None
    if not claims:
        attempted = (research_state is not None and task.task_type != "qa"
                     and bool(research_state.capabilities_used))
        result.evidence_status = "insufficient" if attempted else "none"
    elif (result.status != "completed" or result.error is not None
          or task.task_type != "qa" or quality_status != "complete"):
        result.evidence_status = "insufficient"
    core_delivery = result.meta.get("research_core_delivery")
    if isinstance(core_delivery, dict):
        core_delivery = deepcopy(core_delivery)
        core_delivery["blocks"] = []
        core_delivery["semantic_review"] = "not_reviewed"
        result.meta["research_core_delivery"] = core_delivery
    if isinstance(quality, dict):
        quality = dict(quality)
        if not claims:
            quality["status"] = "insufficient"
        elif quality.get("needs_report") or quality.get("needs_analysis"):
            quality["status"] = "partial"
        quality_status = quality.get("status")
        quality["audited_report_blocks"] = 0
        quality["audited_analysis_blocks"] = 0
        result.meta["research_quality"] = quality
    result.meta["research_delivery"] = {
        "format": "fact_answer" if claims else "no_results",
        "status": ("complete" if claims and result.status == "completed"
                   and task.task_type == "qa" and quality_status == "complete"
                   else "partial" if claims else "insufficient"),
        "audited_analysis": False,
        "source_planids": list(dict.fromkeys(ref.planid for ref in result.evidence_refs)),
    }
    return result
