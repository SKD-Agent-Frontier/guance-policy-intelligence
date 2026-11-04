"""Task-driven evidence coverage for multi-document QA.

Coverage is derived only from surviving quote-verified claims and their actual
EvidenceRefs.  Reading a document is not the same as using it; model-declared
coverage is never trusted here.
"""
from __future__ import annotations

from .research_quality import ANALYTICAL, INTENT_TERMS, requirements
from .research_requirements import (
    catalog_facet_matches_claim,
    claim_fingerprint,
    claim_matches_requirement_source,
    requirements_fingerprint,
    supports_metric_value,
    supports_numeric_value,
    supports_requirement,
)
from .routing import parse_areas, parse_planids, parse_titles

MULTI_DOC_MARKERS = (
    "两篇政策", "两篇规划", "多篇政策", "多篇规划", "两份文件", "多份文件",
    "至少两篇", "多部规划", "跨地区", "跨省", "跨市", "跨县",
    "不同地区的政策", "不同城市的政策", "各地政策", "其他地区的政策",
)
MAX_REQUIRED_DOCS = 4


def multi_document_required(question: str) -> bool:
    text = question or ""
    if len(parse_planids(text)) >= 2 or len(parse_titles(text)) >= 2 or len(parse_areas(text)) >= 2:
        return True
    return any(marker in text for marker in MULTI_DOC_MARKERS)


def _verified_claims(result) -> list:
    quote_check = result.meta.get("quote_verification") or {}
    verified = (isinstance(quote_check, dict)
                and quote_check.get("unverified") == 0
                and not result.meta.get("verification_incomplete"))
    refs = {ref.chunk_id for ref in result.evidence_refs}
    return [claim for claim in result.claims
            if verified and claim.chunk_ids and claim.quotes
            and set(claim.chunk_ids) <= refs]


def _claim_dimensions(claim, required_dimensions: list[str]) -> list[str]:
    text = claim.text or ""
    return [label for label in required_dimensions
            if any(term in text for term in INTENT_TERMS.get(label, ()))]


def _read_documents(ctx) -> dict[str, dict]:
    documents: dict[str, dict] = {}
    registry = getattr(ctx, "evidence", None) if ctx is not None else None
    ids = registry.ids() if registry is not None else []
    for chunk_id in ids:
        ref = registry.get(chunk_id)
        if ref is None or getattr(ref, "source", None) != "kb_read":
            continue
        planid = getattr(ref, "planid", None)
        if not planid:
            continue
        documents.setdefault(planid, {
            "planid": planid,
            "title": getattr(ref, "title", "") or "",
            "area": getattr(ref, "area", None),
            "fyp": getattr(ref, "fyp", None),
        })
    return documents


def _explicit_planids(ctx) -> list[str]:
    planids = []
    for planid in getattr(ctx, "scope", None) or ():
        if isinstance(planid, str) and planid:
            planids.append(planid)
    route = getattr(ctx, "route", None)
    for planid in getattr(route, "planids", None) or ():
        if isinstance(planid, str) and planid:
            planids.append(planid)
    return list(dict.fromkeys(planids))


def build_coverage(result, question: str, ctx) -> dict:
    """Return a serializable coverage dict with at most one bounded next action."""
    required = requirements(question, result.meta.get("research_requirements"))
    required_units = required["units"]
    # Analytical dimensions are judged only after an audited analysis block
    # exists; factual coverage must not pretend a literal claim satisfies them.
    factual_dimensions = [label for label in required["dimensions"]
                          if label not in ANALYTICAL]
    multi_required = multi_document_required(question)
    claims = _verified_claims(result)
    refs_by_chunk = {ref.chunk_id: ref for ref in result.evidence_refs}
    read_documents = _read_documents(ctx)
    explicit_planids = _explicit_planids(ctx)

    document_claims: dict[str, int] = {}
    document_dimensions: dict[str, list[str]] = {}
    covered_dimensions: list[str] = []
    for claim in claims:
        for chunk_id in claim.chunk_ids:
            ref = refs_by_chunk.get(chunk_id)
            if ref is None:
                continue
            planid = str(ref.planid)
            document_claims[planid] = document_claims.get(planid, 0) + 1
            dims = _claim_dimensions(claim, factual_dimensions)
            if dims:
                document_dimensions.setdefault(planid, [])
                for dimension in dims:
                    if dimension not in document_dimensions[planid]:
                        document_dimensions[planid].append(dimension)
                    if dimension not in covered_dimensions:
                        covered_dimensions.append(dimension)
            break

    covered_planids = [pid for pid, count in document_claims.items() if count > 0]
    if explicit_planids:
        required_planids = explicit_planids[:MAX_REQUIRED_DOCS]
        missing_planids = [pid for pid in required_planids
                           if pid not in covered_planids]
        multi_satisfied = not missing_planids
    elif multi_required:
        required_planids = []
        missing_planids = []
        multi_satisfied = len(covered_planids) >= 2
    else:
        required_planids = []
        missing_planids = []
        multi_satisfied = True

    gaps = []
    if not claims:
        gaps.append("verified_facts_missing")
    if explicit_planids and missing_planids:
        gaps.extend("target_document_missing:" + pid for pid in missing_planids)
    if multi_required and not multi_satisfied:
        gaps.append("multiple_documents_required")

    requirement_coverage = []
    verified_claim_ids = {id(claim) for claim in claims}
    verified_positions = [index for index, claim in enumerate(result.claims)
                          if id(claim) in verified_claim_ids]
    verified_text_by_index = {
        position: " ".join([claim.text, *claim.quotes, *[
            "〔章节〕" + str(getattr(refs_by_chunk.get(chunk_id), "section_path", "") or "")
            + "〔文献期〕" + str(getattr(refs_by_chunk.get(chunk_id), "fyp", "") or "")
            for chunk_id in claim.chunk_ids
        ]])
        for position, claim in zip(verified_positions, claims)
    }
    support_review = result.meta.get("requirement_support") or {}
    frozen_units = result.meta.get("research_requirements")
    support_requirements_fingerprint = requirements_fingerprint(
        frozen_units if isinstance(frozen_units, list) and frozen_units else required_units
    )
    approved_supports = (support_review.get("supports")
                         if isinstance(support_review, dict)
                         and support_review.get("verification") == "semantic_reviewed_against_quotes"
                         and support_review.get("claim_fingerprint") == claim_fingerprint(result.claims)
                         and support_review.get("requirements_fingerprint") == support_requirements_fingerprint
                         else {})
    catalog_sources = result.meta.get("catalog_requirement_sources") or {}
    for unit in required_units:
        phase = unit.get("phase", "evidence")
        actionable = bool(unit.get("actionable", True))
        if unit["kind"] in {"dimension", "analysis"}:
            evidence_indexes = [position for position, claim in zip(verified_positions, claims)
                                if unit["label"] in _claim_dimensions(claim, [unit["label"]])]
            if phase == "synthesis" or unit["kind"] == "analysis":
                evidence_indexes = []
                status = "pending"
        elif unit["kind"] == "comparison":
            prior_coverage = {item["id"]: item for item in requirement_coverage}
            left = prior_coverage.get(unit.get("left_requirement_id"), {})
            right = prior_coverage.get(unit.get("right_requirement_id"), {})
            left_indexes = list(left.get("evidence_claim_indexes") or [])
            right_indexes = list(right.get("evidence_claim_indexes") or [])
            both_sides_covered = (left.get("status") == "covered"
                                  and right.get("status") == "covered")
            evidence_indexes = left_indexes + right_indexes
            phase = "synthesis" if both_sides_covered else "evidence"
            actionable = False
            status = "pending" if both_sides_covered else "missing"
        elif unit["kind"] == "value_check":
            fact_requirement = {**unit, "kind": "metric", "expected_value": None}
            evidence_indexes = [position for position, text in verified_text_by_index.items()
                                if supports_requirement(fact_requirement, text)]
            if evidence_indexes:
                phase, actionable = "synthesis", False
                evidence_indexes = []
            else:
                phase, actionable = "evidence", True
                status = "missing"
        elif unit["kind"] == "source_citation":
            evidence_indexes = list(verified_positions)
            status = "covered" if evidence_indexes else "missing"
        elif unit["kind"] == "output_section":
            evidence_indexes = []
            status = "pending"
        else:
            evidence_indexes = [index for index, text in verified_text_by_index.items()
                                if supports_requirement(unit, text)
                                and claim_matches_requirement_source(
                                    unit, result.claims[index], refs_by_chunk,
                                    catalog_sources)]
        raw_indexes = (approved_supports.get(unit["id"], [])
                       if isinstance(approved_supports, dict)
                       and unit["kind"] not in {"comparison", "value_check"} else [])
        if isinstance(raw_indexes, list):
            for index in raw_indexes:
                text = verified_text_by_index.get(index)
                if text is None or type(index) is not int:
                    continue
                if not claim_matches_requirement_source(
                        unit, result.claims[index], refs_by_chunk, catalog_sources):
                    continue
                if not catalog_facet_matches_claim(unit, text, catalog_sources):
                    continue
                if unit["kind"] == "metric" and not supports_metric_value(unit, text):
                    continue
                if unit["kind"] == "numeric_fact" and not supports_numeric_value(text):
                    continue
                if unit["kind"] == "output_section" and unit["label"] not in str(result.answer or ""):
                    continue
                evidence_indexes.append(index)
        evidence_indexes = list(dict.fromkeys(evidence_indexes))
        if unit["kind"] == "comparison":
            # Side facts are evidence requirements; the comparison itself is
            # complete only after a separately audited analysis block.
            status = "pending" if phase == "synthesis" else "missing"
        elif unit["kind"] not in {"dimension", "output_section", "analysis", "value_check"}:
            status = "covered" if evidence_indexes else "missing"
        elif unit["kind"] == "dimension" and phase != "synthesis":
            status = "covered" if evidence_indexes else "missing"
        elif unit["kind"] == "analysis":
            status = "pending"
        elif unit["kind"] == "value_check" and phase == "synthesis":
            status = "pending"
        item = {
            **unit,
            "status": status,
            "phase": phase,
            "actionable": actionable,
            "evidence_claim_indexes": sorted(set(evidence_indexes)),
            "gap_label": unit["label"],
        }
        requirement_coverage.append(item)
        if status == "missing" and phase == "evidence":
            gaps.append("requirement_uncovered:" + unit["id"])

    for item in requirement_coverage:
        if (item["kind"] != "dimension" or item["status"] != "covered"
                or item["phase"] == "synthesis"):
            continue
        label = item["label"]
        if label not in covered_dimensions:
            covered_dimensions.append(label)
        for claim_index in item["evidence_claim_indexes"]:
            if type(claim_index) is not int or not 0 <= claim_index < len(result.claims):
                continue
            claim = result.claims[claim_index]
            for chunk_id in claim.chunk_ids:
                ref = refs_by_chunk.get(chunk_id)
                if ref is not None:
                    document_dimensions.setdefault(str(ref.planid), [])
                    if label not in document_dimensions[str(ref.planid)]:
                        document_dimensions[str(ref.planid)].append(label)
                    break

    missing_dimensions = [label for label in factual_dimensions
                          if label not in covered_dimensions]
    gaps.extend("dimension_uncovered:" + label for label in missing_dimensions)

    # Prefer a read-but-unused target document, then a missing explicit document,
    # then a missing dimension.  The program chooses the scope; the model never
    # supplies or invents a planid here.
    target_planid = None
    target_title = None
    next_query = None
    if explicit_planids and missing_planids:
        target_planid = missing_planids[0]
        target_title = (read_documents.get(target_planid) or {}).get("title")
    elif multi_required and not multi_satisfied:
        unused = [pid for pid in read_documents if pid not in covered_planids]
        target_planid = unused[0] if unused else None
        target_title = (read_documents.get(target_planid) or {}).get("title") if target_planid else None
    if missing_dimensions and target_planid is None:
        # A missing dimension is most useful when scoped to the single target
        # document (or the first covered document when there is no explicit list).
        target_planid = (explicit_planids[0] if explicit_planids else
                         (covered_planids[0] if covered_planids else None))
        target_title = (read_documents.get(target_planid) or {}).get("title") if target_planid else None
    if missing_dimensions:
        dimension = missing_dimensions[0]
        if target_title:
            next_query = f"{target_title} {dimension}"
        else:
            next_query = f"{dimension} {question[:120]}"
    elif target_title:
        next_query = f"{target_title} 主要措施 具体做法"
    elif multi_required and not multi_satisfied:
        next_query = "相关政策 具体措施 共同点 差异"

    status = ("complete" if claims and not gaps else
              "partial" if claims else "insufficient")
    next_actions = []
    target_for_gap = (missing_planids[0] if missing_planids else
                      explicit_planids[0] if explicit_planids else
                      covered_planids[0] if len(covered_planids) == 1 else None)
    for unit in requirement_coverage:
        if (unit["status"] != "missing" or unit["phase"] != "evidence"
                or not unit["actionable"]):
            continue
        # planids already constrain the source; repeating its title overwhelms
        # the missing topic in lexical ranking across that document's chunks.
        query_parts = [unit.get("subject"), unit["label"]]
        query = " ".join(dict.fromkeys(part for part in query_parts if part))
        action = {
            "requirement_id": unit["id"],
            "kind": unit["kind"],
            "query": query[:160],
            "focus": query[:120],
            "target_planid": target_for_gap,
            "gap_label": unit["gap_label"],
            "retrievable": True,
        }
        if action not in next_actions:
            next_actions.append(action)
    for planid in missing_planids:
        title = (read_documents.get(planid) or {}).get("title")
        query = title or question[:120]
        next_actions.append({
            "requirement_id": f"document:{planid}",
            "kind": "document_identity",
            "query": query,
            "focus": query,
            "target_planid": planid,
            "gap_label": f"目标文献 {planid}",
            "retrievable": True,
        })
    if multi_required and not multi_satisfied and not any(
        action.get("kind") in {"document_identity", "multi_document_search"}
        for action in next_actions
    ):
        unused = [pid for pid in read_documents if pid not in covered_planids]
        target_pid = unused[0] if unused else None
        target_title = (read_documents.get(target_pid) or {}).get("title") if target_pid else None
        query = target_title or question[:120]
        next_actions.append({
            "requirement_id": "multi_document:additional_source",
            "kind": "document_identity" if target_pid else "multi_document_search",
            "query": query,
            "focus": query,
            "target_planid": target_pid,
            "gap_label": "另一份相关政策文献",
            "retrievable": True,
        })
    return {
        "status": status,
        "required_dimensions": required["dimensions"],
        "factual_dimensions": factual_dimensions,
        "covered_dimensions": covered_dimensions,
        "missing_dimensions": missing_dimensions,
        "required_planids": required_planids,
        "covered_planids": covered_planids,
        "missing_planids": missing_planids,
        "read_planids": list(read_documents),
        "document_claim_counts": document_claims,
        "document_dimensions": document_dimensions,
        "multi_document_required": multi_required,
        "multi_document_satisfied": multi_satisfied,
        "gaps": gaps,
        "requirement_coverage": requirement_coverage,
        "missing_requirements": [unit["label"] for unit in requirement_coverage
                                 if unit["status"] == "missing"],
        "next_actions": next_actions,
        "next_action": (
            {"query": next_query, "target_planid": target_planid,
             "target_title": target_title}
            if next_query else None
        ),
        "verified_claim_count": len(claims),
        "note": "只统计通过逐字核验且与 EvidenceRef 一致的 Claim；已读不等于已用。",
    }
