"""Evidence-linked QA synthesis (v0.1).

Analysis is a *derived interpretation*, not a quote and not a verified policy fact.
Accept only when every dependency survives the existing quote/number/keyword checks.
Deterministic checks establish provenance, not semantic entailment or causal validity.
"""

from .schemas import Claim, RunResult

MAX_ANALYSES = 3
MAX_ANALYSIS_CHARS = 300
MAX_SCOPE_CHARS = 160
KINDS = {"synthesis", "comparison", "implication"}


def _signature(claim: Claim) -> tuple[str, tuple[str, ...]]:
    return claim.text, tuple(claim.chunk_ids)


def prepare_candidates(analyses: list[dict], original: list[Claim], retained: list[Claim]) -> list[dict]:
    """Capture original claim dependencies; never remap a missing claim to its successor."""
    accepted = {_signature(claim) for claim in retained if len(claim.chunk_ids) > 0}
    candidates: list[dict] = []
    for value in analyses[:MAX_ANALYSES * 2]:
        if not isinstance(value, dict):
            continue
        text, scope, kind = value.get("text"), value.get("scope_note"), value.get("kind")
        indexes = value.get("supporting_claim_indexes")
        if (not isinstance(text, str) or not 8 <= len(text.strip()) <= MAX_ANALYSIS_CHARS
                or not isinstance(scope, str) or not 4 <= len(scope.strip()) <= MAX_SCOPE_CHARS
                or kind not in KINDS
                or not isinstance(indexes, list) or len(indexes) < 2 or len(indexes) > 8
                or any(type(i) is not int or not 0 <= i < len(original) for i in indexes)
                or len(set(indexes)) != len(indexes)):
            continue
        signatures = [_signature(original[i]) for i in indexes]
        if len(set(signatures)) != len(signatures) or any(sig not in accepted for sig in signatures):
            continue
        candidates.append({
            "kind": kind, "text": text.strip(), "scope_note": scope.strip(),
            "support": signatures,
        })
    return candidates


def finalize_qa_analysis(result: RunResult) -> RunResult:
    """Publish only dependency-complete synthesis after verification and final QA filtering."""
    if result.task_type != "qa":
        return result
    candidates = result._qa_analysis_candidates
    result._qa_analysis_candidates = []
    if not candidates or result.status != "completed" or result.error:
        return result

    # No downstream renderer may receive pending/unverified draft analysis.
    counts = result.meta.get("quote_verification") or {}
    verified_quotes = bool(counts and counts.get("unverified", 0) == 0)
    if not verified_quotes or result.meta.get("finish_failed"):
        result.meta["analysis_contract"] = {"accepted": 0, "rejected": len(candidates),
                                             "reason": "quote_verification_unavailable"}
        return result

    current = {_signature(claim): i for i, claim in enumerate(result.claims)
               if claim.quotes and claim.chunk_ids}
    # Duplicate identical claims cannot be uniquely indexed for interpretation.
    duplicates = {sig for sig in current if sum(_signature(c) == sig for c in result.claims) > 1}
    analyses = []
    for candidate in candidates:
        supports = candidate["support"]
        if any(sig not in current or sig in duplicates for sig in supports):
            continue
        indexes = [current[sig] for sig in supports]
        linked = [result.claims[i] for i in indexes]
        if len(indexes) < 2 or len(set(indexes)) != len(indexes):
            continue
        # No analysis without a published original citation for each supporting fact.
        live_refs = {ref.chunk_id for ref in result.evidence_refs}
        if any(not set(claim.chunk_ids) <= live_refs for claim in linked):
            continue
        chunk_ids = list(dict.fromkeys(cid for claim in linked for cid in claim.chunk_ids))
        analyses.append({
            "kind": candidate["kind"], "text": candidate["text"],
            "scope_note": candidate["scope_note"],
            "supporting_claim_indexes": indexes,
            "supporting_chunk_ids": chunk_ids,
            "verification": "evidence_linked_only",
        })
    if analyses:
        result.structured = {
            "schema_version": "qa-analysis/1",
            "analyses": analyses,
            "verification": {
                "policy_facts": "existing_quote_and_number_checks",
                "analysis": "dependency_links_checked_semantics_not_verified",
            },
        }
    result.meta["analysis_contract"] = {
        "accepted": len(analyses), "rejected": len(candidates) - len(analyses),
        "reason": "dependency_validation",
    }
    # A model-written answer can still contain discarded analyses. Rebuild a
    # fact-only lead-in whenever an analysis draft was proposed.
    if result.meta.get("answer_rebuilt") is None:
        result.answer = (
            "以下为本轮取得可追溯来源的政策事实，分析判断另列于下方：\n"
            + "\n".join(f"- {claim.text}" for claim in result.claims)
            if result.claims else "本轮未取得足以形成研究分析的政策事实证据。"
        )
    return result
