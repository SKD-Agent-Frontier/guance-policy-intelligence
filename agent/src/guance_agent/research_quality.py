"""Conservative, user-intent-based completion checks for QA research.

Only an audited QA report can satisfy analytical coverage. A model's
self-declared dimensions are never considered the user's requirements.
"""
from __future__ import annotations

# Restrict inference to dimensions *explicitly named* in the user's request.
INTENT_TERMS = {
    "政策目标": ("政策目标", "发展目标", "主要目标"),
    "政策措施": ("政策措施", "支持措施", "扶持措施", "政策工具"),
    "地区差异": ("地区差异", "区域差异", "政策差异", "异同", "对比", "比较",
                  "差异", "共同点", "区别"),
    "政策优势": ("政策优势", "优势"),
    "发展机遇": ("发展机遇", "战略机遇", "机遇"),
    "问题挑战": ("问题挑战", "挑战", "不足", "难点", "瓶颈", "制约", "困难"),
    "演变趋势": ("演变", "变化趋势", "发展趋势", "政策变化"),
    "产业发展方向": ("产业发展方向", "产业布局", "产业发展重点"),
    "重点措施": ("重点措施", "主要措施", "主要部署", "措施"),
    "政策意义": ("政策意义", "政策影响", "发展意义"),
    "产业扶持": ("产业扶持", "产业支持", "产业补贴"),
    "基础设施建设": ("基础设施建设", "基础设施", "算力设施"),
    "人才培养": ("人才培养", "人才引进", "人才支持"),
    "政策作用": ("积极作用", "政策作用", "政策影响", "预期作用"),
    "政策局限": ("局限", "局限性", "制约因素"),
    "政策建议": ("政策建议", "对策建议", "改进建议"),
}
ANALYTICAL = frozenset({"地区差异", "政策优势", "发展机遇", "问题挑战", "演变趋势",
                        "政策建议", "政策作用", "政策局限", "政策意义"})
ANALYSIS_WORDS = ("分析", "研究", "研判", "评估", "报告", "比较", "对比", "异同",
                  "归纳", "共同点", "差异", "区别", "综合")


def requirements(question: str) -> dict:
    explicit = [label for label, terms in INTENT_TERMS.items()
                if any(term in question for term in terms)]
    return {
        "dimensions": explicit,
        "needs_analysis": any(term in question for term in ANALYSIS_WORDS)
                          or any(label in ANALYTICAL for label in explicit),
        "needs_report": any(term in question for term in ANALYSIS_WORDS)
                        or len(explicit) >= 2,
    }


def _matching(block: dict, label: str) -> bool:
    terms = INTENT_TERMS[label]
    # Only audited content, not the model's dimension declaration, counts.
    content = " ".join([str(block.get("dimension") or ""),
                        *[str(t) for t in block.get("title_path", [])],
                        str(block.get("text") or "")])
    return any(term in content for term in terms)


def evaluate(result, question: str) -> dict:
    """Report evidence/analytical completeness separately from run success."""
    required = requirements(question)
    structured = result.structured if isinstance(result.structured, dict) else {}
    is_report = structured.get("schema_version") == "qa-report/1"
    report_verified = (is_report and
                       structured.get("verification", {}).get("facts")
                       == "semantic_reviewed_against_quotes")
    blocks = (structured.get("blocks") or []) if report_verified else []
    blocks = [block for block in blocks if isinstance(block, dict)
              and block.get("verification") == "model_reviewed_evidence_linked"]
    refs = {ref.chunk_id for ref in result.evidence_refs}
    quote_check = result.meta.get("quote_verification") or {}
    quotes_verified = (isinstance(quote_check, dict)
                       and quote_check.get("unverified") == 0
                       and not result.meta.get("verification_incomplete"))
    verified_claims = [claim for claim in result.claims if quotes_verified
                       and claim.chunk_ids and claim.quotes
                       and set(claim.chunk_ids) <= refs]
    analytical_blocks = [block for block in blocks if block.get("kind") == "analysis"]
    report_audited = bool(is_report and report_verified and blocks)
    # Simple factual QA does not require a report to count as evidence-backed.
    # For a single explicitly named factual facet, surviving quote-verified
    # claims establish *structural* coverage, not semantic sufficiency.
    simple_fact = (not required["needs_report"] and not required["needs_analysis"]
                   and len(required["dimensions"]) == 1 and bool(verified_claims))
    covered = [label for label in required["dimensions"] if any(
        _matching(block, label) and (
            label not in ANALYTICAL or block.get("kind") == "analysis"
        ) for block in blocks) or (simple_fact and label not in ANALYTICAL)]
    missing = [item for item in required["dimensions"] if item not in covered]
    report_complete = bool(
        report_audited
        and (structured.get("coverage") or {}).get("status") == "complete"
        and not missing
    )
    gaps = []
    if not verified_claims or result.meta.get("verification_incomplete"):
        gaps.append("verified_facts_missing")
    if result.meta.get("execution_recovery"):
        gaps.append("source_excerpts_only")
    if required["needs_report"] and not blocks:
        gaps.append("audited_report_missing")
    if required["needs_analysis"] and not analytical_blocks:
        gaps.append("audited_analysis_missing")
    gaps.extend("dimension_uncovered:" + label for label in missing)

    # Round 4: execution success is not research completion. These checks are
    # derived from program-verified facts/coverage, never model declarations.
    if result.evidence_status in {"insufficient", "none"}:
        gaps.append("evidence_insufficient")
    if ((result.meta.get("answer_rebuilt") and not report_complete)
            or (isinstance(quote_check, dict) and quote_check.get("removed_claims")
                and not report_complete)):
        gaps.append("claims_removed_or_answer_rebuilt")
    scope_filter = result.meta.get("object_scope_filter") or {}
    if isinstance(scope_filter, dict) and scope_filter.get("dropped_claims"):
        gaps.append("object_scope_mismatch")
    coverage = result.meta.get("evidence_coverage") or {}
    if report_audited:
        # The final audited report has its own coverage computed after semantic
        # review; a pre-report retrieval coverage snapshot must not invalidate it.
        coverage = {}
    if isinstance(coverage, dict) and coverage:
        for gap in coverage.get("gaps") or []:
            if gap not in gaps:
                gaps.append("coverage:" + gap)
        if coverage.get("status") == "insufficient":
            gaps.append("evidence_coverage_insufficient")
    if result.status != "completed" or result.error is not None:
        gaps.append("execution_not_completed")
    gaps = list(dict.fromkeys(gaps))
    status = ("complete" if not gaps and result.status == "completed"
              and result.error is None and result.evidence_status == "verified"
              and (not coverage or coverage.get("status") == "complete") else
              "partial" if verified_claims else "insufficient")
    return {
        "status": status, "required_dimensions": required["dimensions"],
        "covered_dimensions": covered, "missing_dimensions": missing,
        "needs_analysis": required["needs_analysis"],
        "needs_report": required["needs_report"], "gaps": gaps,
        "verified_claim_count": len(verified_claims),
        "factual_answer_coverage": bool(simple_fact),
        "audited_report_blocks": len(blocks),
        "audited_analysis_blocks": len(analytical_blocks),
        "required_planids": coverage.get("required_planids", []) if isinstance(coverage, dict) else [],
        "covered_planids": coverage.get("covered_planids", []) if isinstance(coverage, dict) else [],
        "missing_planids": coverage.get("missing_planids", []) if isinstance(coverage, dict) else [],
        "evidence_coverage_status": coverage.get("status") if isinstance(coverage, dict) else None,
        "note": "完成度由程序核验的 Claim、EvidenceRef、覆盖结构和状态一致性共同决定，不证明推理在语义上正确或全库检索充分。",
    }


def followup_gap(result, question: str) -> str | None:
    """Select one explicit unanswered dimension before the final report review."""
    if not result.claims or result.meta.get("verification_incomplete"):
        return None
    wanted = requirements(question)["dimensions"]
    if not wanted:
        return None
    drafts = list(getattr(result, "_qa_report_candidates", []) or [])
    facts = [{"text": claim.text, "dimension": ""} for claim in result.claims
             if claim.quotes and claim.chunk_ids]
    present = [label for label in wanted if any(_matching(block, label)
               and (label not in ANALYTICAL or block.get("kind") == "analysis")
               for block in drafts)
               or (label not in ANALYTICAL and any(
                   _matching(fact, label) for fact in facts))]
    missing = [label for label in wanted if label not in present]
    # Analytical gaps (e.g. "地区差异") are not useful literal RAG queries.
    # Prefer an explicit missing factual dimension for the one extra read.
    factual = [label for label in missing if label not in ANALYTICAL]
    return (factual or missing)[0] if missing else None
