"""Deterministic last-mile projection for public research results.

The gateway does not create evidence or ask a model to review prose. It only
projects claims and report blocks that have already passed the verifier and,
for analysis, the existing semantic review.
"""
from __future__ import annotations

from copy import deepcopy

from .context import same_source
from .research_delivery import _render_structured_report, _signed_block, verify_summary
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


def _trusted_claims(result, task: TaskRequest, ctx):
    if ctx is None:
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
                    or not same_source(output_ref, registered_ref)
                    or (allowed_planids is not None
                        and registered_ref.planid not in allowed_planids)):
                valid = False
                break
            candidate_refs[chunk_id] = output_ref
        if valid and not is_number_verified_claim(result, claim):
            valid = False
        if valid:
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
            or set(chunks) != set(expected_chunks)
            or len(chunks) != len(set(chunks))):
        return None
    return dependencies


def _remap_report_blocks(structured: dict | None, index_map: dict[int, int]) -> dict | None:
    if not isinstance(structured, dict):
        return None
    report = deepcopy(structured)
    kept = []
    for block in report.get("blocks", []):
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


def _approved_report_blocks(result, ctx, claims: list) -> list[dict]:
    structured = result.structured
    verification = structured.get("verification") if isinstance(structured, dict) else None
    if (not isinstance(structured, dict)
            or structured.get("schema_version") != "qa-report/1"
            or not isinstance(verification, dict)
            or verification.get("facts") != "semantic_reviewed_against_quotes"):
        return []

    report = deepcopy(structured)
    original_blocks = [block for block in report.get("blocks", [])
                       if isinstance(block, dict)]
    report["blocks"] = original_blocks
    result.structured = report
    signed = [_signed_block(result, block) for block in original_blocks]
    summary = verify_summary(result, signed, require_semantic=True)
    approved_analysis = set(summary.get("approved") or [])

    accepted = []
    for block in original_blocks:
        block_id = block.get("id")
        kind = block.get("kind")
        dependencies = _block_claims(block, claims)
        if (not isinstance(block_id, str) or not block_id or dependencies is None
                or kind not in {"fact", "analysis"}):
            continue
        text = block.get("text")
        title_path = block.get("title_path")
        if (not isinstance(text, str) or not text.strip()
                or not isinstance(title_path, list)
                or any(not isinstance(title, str) for title in title_path)):
            continue
        if not block_numbers_valid("\n".join([*title_path, text]), dependencies, ctx, result):
            continue
        if kind == "analysis":
            if block_id not in approved_analysis or len(dependencies) < 2:
                continue
        else:
            # Fact blocks may summarize several claims, but cannot cite facts
            # outside the dependency set.
            supported_texts = {claim.text.strip() for claim in dependencies}
            joined_text = "；".join(claim.text.strip() for claim in dependencies)
            if text.strip() not in supported_texts and text.strip() != joined_text:
                continue
        accepted.append(block)
    return accepted


def _set_structured_report(result, original: dict, blocks: list[dict]) -> None:
    dimensions = [value for value in original.get("dimensions", [])
                  if isinstance(value, str)]
    coverage = deepcopy(original.get("coverage") or {})
    original_block_ids = {
        block.get("id") for block in original.get("blocks", [])
        if isinstance(block, dict)
    }
    accepted_ids = {block.get("id") for block in blocks}
    rejected = bool(original_block_ids - accepted_ids)
    if rejected:
        coverage["status"] = "partial"
        coverage["missing"] = list(dict.fromkeys([
            *(coverage.get("missing") or []),
            *(dimension for dimension in dimensions
              if not any(dimension in (block.get("required_dimensions") or
                                       [block.get("dimension")]) for block in blocks)),
        ]))
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": dimensions,
        "blocks": blocks,
        "coverage": coverage,
        "verification": {
            "facts": "semantic_reviewed_against_quotes" if blocks else "not_reviewed",
            "blocks": original.get("verification", {}).get("blocks", "not_reviewed"),
        },
    }
    core_delivery = result.meta.get("research_core_delivery")
    if isinstance(core_delivery, dict):
        core_delivery = deepcopy(core_delivery)
        core_delivery["blocks"] = blocks
        if rejected:
            core_delivery["semantic_review"] = "partial"
        result.meta["research_core_delivery"] = core_delivery


def _failure_answer(result) -> str:
    if result.error is not None:
        return result.error.message
    return "本轮未能完成政策研究，暂不发布未经最终核验的内容。"


def _business_structured(task: TaskRequest, claims: list, refs_by_id: dict) -> dict | None:
    """Rebuild conservative business views solely from surviving claims/refs."""
    if not claims:
        return None
    refs_by_planid = {}
    for ref in refs_by_id.values():
        refs_by_planid.setdefault(ref.planid, ref)
    if task.task_type == "extract":
        from .schemas import DIMENSIONS

        dimensions = task.dimensions or list(DIMENSIONS)
        covered = {claim.dimension for claim in claims if claim.dimension in dimensions}
        claims = [claim for claim in claims if claim.dimension in dimensions]
        return {
            "dimensions": dimensions,
            "documents": [{key: getattr(ref, key) for key in
                           ("planid", "title", "area", "fyp")}
                          for ref in refs_by_planid.values()],
            "elements": [claim.model_dump(mode="json") for claim in claims],
            "missing": [{"dimension": name, "reason": "not_verified"}
                        for name in dimensions if name not in covered],
        }
    if task.task_type == "organize":
        indexes_by_planid = {}
        topic_map = {}
        for index, claim in enumerate(claims):
            cited_refs = [refs_by_id[cid] for cid in claim.chunk_ids if cid in refs_by_id]
            topic = "已核验事实"
            topic_entry = topic_map.setdefault(topic, {"planids": [], "claim_indexes": []})
            topic_entry["claim_indexes"].append(index)
            for ref in cited_refs:
                indexes_by_planid.setdefault(ref.planid, []).append(index)
                if ref.planid not in topic_entry["planids"]:
                    topic_entry["planids"].append(ref.planid)
        policies = []
        for planid, indexes in indexes_by_planid.items():
            ref = refs_by_planid[planid]
            policies.append({
                "planid": planid, "title": ref.title, "area": ref.area, "fyp": ref.fyp,
                "read_status": "summarized",
                "summary": "；".join(claims[index].text for index in indexes),
                "claim_indexes": indexes, "topics": [], "relevance": None,
                "relevance_note": "未审核的相关性与分类已省略",
                "full_text_covered": False,
            })
        return {
            "schema_version": "organize/1",
            "scope": {"targets": task.targets},
            "coverage": {"documents_summarized": len(policies), "note":
                         "仅含通过共享原文核验的事实；语义分类已省略。"},
            "policies": policies,
            "groups": [{"topic": topic, **entry} for topic, entry in topic_map.items()],
            "verification": {"policy_facts": "claims_only", "analysis_fields": [],
                             "note": "未审核的分类和相关性分析已省略。"},
        }
    if task.task_type in {"compare", "compare_plans"}:
        dimensions = list(dict.fromkeys([
            *(task.dimensions or []),
            *(claim.dimension for claim in claims if claim.dimension),
        ]))
        planids = list(dict.fromkeys([*(task.targets or []), *refs_by_planid]))
        matrix = []
        for dimension_index, dimension in enumerate(dimensions):
            cells = []
            for planid in planids:
                found = [(index, claim) for index, claim in enumerate(claims)
                         if claim.dimension == dimension and any(
                             refs_by_id[cid].planid == planid
                             for cid in claim.chunk_ids if cid in refs_by_id)]
                if found:
                    cells.append({
                        "cell_id": f"d{dimension_index}:{planid}", "planid": planid,
                        "status": "found",
                        "text": "；".join(claim.text for _, claim in found),
                        "chunk_ids": list(dict.fromkeys(
                            cid for _, claim in found for cid in claim.chunk_ids)),
                        "quotes": list(dict.fromkeys(
                            quote for _, claim in found for quote in claim.quotes)),
                    })
                else:
                    cells.append({"cell_id": f"d{dimension_index}:{planid}",
                                  "planid": planid, "status": "insufficient"})
            matrix.append({"dimension": dimension, "cells": cells,
                           "difference": {"text": "当前核验事实不足以判断异同。",
                                          "kind": "analysis", "status": "insufficient",
                                          "supporting_cells": []},
                           "comparability": {"comparable": False,
                                             "reason": "未完成口径审核。"}})
        output = {
            "objects": [{"planid": planid,
                         "title": getattr(refs_by_planid.get(planid), "title", None),
                         "area": getattr(refs_by_planid.get(planid), "area", None),
                         "fyp": getattr(refs_by_planid.get(planid), "fyp", None)}
                        for planid in planids],
            "dimensions": dimensions, "matrix": matrix,
            "task_completion": {"status": "partial", "supported_dimensions": [],
                                "unsupported_dimensions": dimensions},
            "comparability": {"comparable": False,
                              "reason": "未发布未通过共享语义审核的比较判断。"},
            "coverage_notes": ["仅保留通过共享原文核验的事实。"],
            "analysis_gaps": [{"reason": "semantic_review_missing"}],
        }
        if task.task_type == "compare_plans":
            output.update(
                scheme_analysis=[{"planid": planid, "advantages": [],
                                  "limitations": [], "applicability": []}
                                 for planid in planids],
                evaluation_criteria=[],
            )
        return output
    return None


async def finalize_public_delivery(result, task: TaskRequest, ctx, runtime, *,
                                   research_state=None):
    """Rebuild the outward payload from verifier-authorized state.

    Call after execution, semantic review, runtime completion checks, and every
    result mutation. The function performs no model calls or KB reads; every
    surviving claim must carry current quote- and number-verifier receipts.
    """
    _ = runtime, research_state

    original_structured = (deepcopy(result.structured)
                           if isinstance(result.structured, dict) else None)
    original_claims = list(result.claims)
    claims, refs_by_id = _trusted_claims(result, task, ctx)
    if task.task_type == "extract":
        from .schemas import DIMENSIONS

        allowed_dimensions = set(task.dimensions or DIMENSIONS)
        claims = [claim for claim in claims if claim.dimension in allowed_dimensions]
        used_ids = {chunk_id for claim in claims for chunk_id in claim.chunk_ids}
        refs_by_id = {chunk_id: ref for chunk_id, ref in refs_by_id.items()
                      if chunk_id in used_ids}
    claim_indexes = {id(claim): index for index, claim in enumerate(original_claims)}
    index_map = {claim_indexes[id(claim)]: index for index, claim in enumerate(claims)}

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
        result.meta["research_delivery"] = {
            "format": "verbatim_excerpts_or_no_results",
            "status": "partial" if claims else "insufficient",
            "audited_analysis": False,
            "source_planids": list(dict.fromkeys(ref.planid for ref in refs_by_id.values())),
        }
        return result

    result.claims = claims
    result.evidence_refs = list(refs_by_id.values())
    result.structured = _remap_report_blocks(original_structured, index_map)
    approved_blocks = (_approved_report_blocks(result, ctx, claims)
                       if ctx is not None and original_structured is not None else [])
    audited_report = bool(approved_blocks)
    if audited_report:
        # Keep only claims that the accepted report actually depends on. This
        # prevents a rejected block's claim from reappearing through a fallback.
        used_indexes = sorted({index for block in approved_blocks
                               for index in block.get("supporting_claim_indexes", [])})
        result.claims = [claims[index] for index in used_indexes]
        used_ids = {chunk_id for claim in result.claims for chunk_id in claim.chunk_ids}
        result.evidence_refs = [ref for ref in refs_by_id.values() if ref.chunk_id in used_ids]
        reviewed_answer = _render_structured_report(result.structured)
        if task.task_type == "qa":
            _set_structured_report(result, original_structured, approved_blocks)
            if result.status != "completed" or result.error is not None:
                result.structured["coverage"]["status"] = "partial"
            result.answer = reviewed_answer
        else:
            # Keep task-specific API shapes. The semantically reviewed report
            # remains in answer/internal delivery metadata; structured facts are
            # rebuilt only from claims and cannot imply the omitted analysis.
            result.structured = _business_structured(task, result.claims, refs_by_id)
            result.answer = reviewed_answer
            result.evidence_status = "insufficient"
        coverage = (result.structured.get("coverage", {})
                    if isinstance(result.structured, dict) else {})
        if coverage.get("status") != "complete":
            result.evidence_status = "insufficient"
        result.meta["research_delivery"] = {
            "format": "evidence_linked_report",
            "status": (result.meta.get("research_quality") or {}).get("status", "partial"),
            "audited_analysis": any(block.get("kind") == "analysis"
                                     for block in approved_blocks),
            "source_planids": list(dict.fromkeys(
                ref.planid for ref in result.evidence_refs)),
        }
        result.meta["research_delivery"]["status"] = (
            "complete" if coverage.get("status") == "complete"
            and result.evidence_status == "verified" else "partial")
        if task.task_type != "qa" or result.status != "completed" or result.error is not None:
            result.meta["research_delivery"]["status"] = "partial"
        return result

    # Business structure is reconstructed from surviving facts; its unreviewed
    # prose and derived analysis are not passed through.
    result.structured = (None if task.task_type == "qa" else
                         _business_structured(task, claims, refs_by_id))
    if claims:
        result.answer = "\n".join("- " + claim.text for claim in claims)
    else:
        resolution = result.meta.get("qa_context_resolution")
        clarification = (resolution.get("clarification")
                        if isinstance(resolution, dict)
                        and resolution.get("status") in {"ambiguous", "no_prior"}
                        else None)
        if result.error is not None:
            result.answer = _failure_answer(result)
        elif (result.meta.get("qa_execution_subtype") == "boundary_response"
              and isinstance(result.answer, str)):
            # This is deterministic policy-agent boundary text from qa_boundary,
            # not model-authored research prose.
            pass
        elif isinstance(clarification, str) and clarification.strip():
            result.answer = clarification
        elif result.status != "completed":
            result.answer = _failure_answer(result)
        else:
            result.answer = _EMPTY_ANSWER
    quality = result.meta.get("research_quality")
    quality_status = quality.get("status") if isinstance(quality, dict) else None
    if not claims:
        result.evidence_status = "none"
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
