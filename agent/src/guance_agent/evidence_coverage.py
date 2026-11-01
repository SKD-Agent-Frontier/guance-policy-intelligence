"""Task-driven evidence coverage for multi-document QA.

Coverage is derived only from surviving quote-verified claims and their actual
EvidenceRefs.  Reading a document is not the same as using it; model-declared
coverage is never trusted here.
"""
from __future__ import annotations

from .research_quality import INTENT_TERMS, requirements

MULTI_DOC_MARKERS = (
    "比较", "对比", "异同", "分别", "归纳", "不同政策", "两篇", "多篇",
    "各自", "共同点", "差异", "多份", "至少两篇", "多部",
)
MAX_REQUIRED_DOCS = 4


def multi_document_required(question: str) -> bool:
    return any(marker in question for marker in MULTI_DOC_MARKERS)


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
    required = requirements(question)
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
            dims = _claim_dimensions(claim, required["dimensions"])
            if dims:
                document_dimensions.setdefault(planid, [])
                for dimension in dims:
                    if dimension not in document_dimensions[planid]:
                        document_dimensions[planid].append(dimension)
                    if dimension not in covered_dimensions:
                        covered_dimensions.append(dimension)
            break

    covered_planids = [pid for pid, count in document_claims.items() if count > 0]
    missing_dimensions = [label for label in required["dimensions"]
                          if label not in covered_dimensions]

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
    return {
        "status": status,
        "required_dimensions": required["dimensions"],
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
        "next_action": (
            {"query": next_query, "target_planid": target_planid,
             "target_title": target_title}
            if next_query else None
        ),
        "verified_claim_count": len(claims),
        "note": "只统计通过逐字核验且与 EvidenceRef 一致的 Claim；已读不等于已用。",
    }
