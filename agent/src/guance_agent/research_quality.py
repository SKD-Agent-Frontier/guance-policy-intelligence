"""Conservative, user-intent-based completion checks for QA research.

Only an audited QA report can satisfy analytical coverage. A model's
self-declared dimensions are never considered the user's requirements.
"""
from __future__ import annotations


# Restrict inference to dimensions *explicitly named* in the user's request.
INTENT_TERMS = {
    "政策目标": ("政策目标", "发展目标", "主要目标"),
    "政策措施": ("政策措施", "支持措施", "扶持措施", "政策工具"),
    "地区差异": ("地区差异", "区域差异", "政策差异", "异同", "对比", "比较"),
    "政策优势": ("政策优势", "优势"),
    "问题挑战": ("问题挑战", "挑战", "不足", "难点"),
    "演变趋势": ("演变", "变化趋势", "发展趋势", "政策变化"),
    "政策建议": ("政策建议", "对策建议", "改进建议"),
}
ANALYTICAL = frozenset({"地区差异", "政策优势", "问题挑战", "演变趋势", "政策建议"})
ANALYSIS_WORDS = ("分析", "研究", "研判", "评估", "报告", "比较", "对比", "异同")


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
    covered = [label for label in required["dimensions"] if any(
        _matching(block, label) and (
            label not in ANALYTICAL or block.get("kind") == "analysis"
        ) for block in blocks)]
    missing = [item for item in required["dimensions"] if item not in covered]
    gaps = []
    if not verified_claims or result.meta.get("verification_incomplete"):
        gaps.append("verified_facts_missing")
    if required["needs_report"] and not blocks:
        gaps.append("audited_report_missing")
    if required["needs_analysis"] and not analytical_blocks:
        gaps.append("audited_analysis_missing")
    gaps.extend("dimension_uncovered:" + label for label in missing)
    status = ("complete" if not gaps and result.status == "completed"
              and result.error is None else
              "partial" if verified_claims else "insufficient")
    return {
        "status": status, "required_dimensions": required["dimensions"],
        "covered_dimensions": covered, "missing_dimensions": missing,
        "needs_analysis": required["needs_analysis"],
        "needs_report": required["needs_report"], "gaps": gaps,
        "verified_claim_count": len(verified_claims),
        "audited_report_blocks": len(blocks),
        "audited_analysis_blocks": len(analytical_blocks),
        "note": "完成度是保守的结构/覆盖检查，不证明推理在语义上正确或全库检索充分。",
    }


def followup_gap(result, question: str) -> str | None:
    """Select one explicit unanswered dimension before the final report review."""
    if not result.claims or result.meta.get("verification_incomplete"):
        return None
    wanted = requirements(question)["dimensions"]
    if not wanted:
        return None
    drafts = list(getattr(result, "_qa_report_candidates", []) or [])
    present = [label for label in wanted if any(_matching(block, label)
               and (label not in ANALYTICAL or block.get("kind") == "analysis")
               for block in drafts)]
    missing = [label for label in wanted if label not in present]
    return missing[0] if missing else None
