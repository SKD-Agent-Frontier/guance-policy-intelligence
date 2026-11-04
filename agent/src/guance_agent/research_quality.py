"""Conservative, user-intent-based completion checks for QA research.

Only an audited QA report can satisfy analytical coverage. A model's
self-declared dimensions are never considered the user's requirements.
"""
from __future__ import annotations

import re

from .research_requirements import (
    _METRICS,
    _content_period_matches,
    _measurement_value,
    catalog_facet_matches_claim,
    claim_fingerprint,
    claim_matches_requirement_source,
    normalize_requirements,
    requirements_fingerprint,
    supports_metric_value,
    supports_numeric_value,
    supports_requirement,
)
from .research_targets import extract_targets

# Restrict inference to dimensions *explicitly named* in the user's request.
INTENT_TERMS = {
    "政策目标": ("政策目标", "发展目标", "主要目标"),
    "政策措施": ("政策措施", "支持措施", "扶持措施", "政策工具"),
    "地区差异": ("地区差异", "区域差异", "政策差异", "异同", "对比",
                  "差异", "共同点", "区别", "不同", "相比", "双方", "两地"),
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


def requirements(question: str, planned=None) -> dict:
    from .routing import current_question

    text = current_question(question or "")
    explicit = [label for label, terms in INTENT_TERMS.items()
                if any(term in text for term in terms)]
    from .routing import parse_areas

    if ("地区差异" in explicit and len(parse_areas(text)) < 2
            and not re.search(r"地区|区域|两地|各地|跨省|跨市|多地|两篇|多篇|多份|各文献|多文献", text)):
        explicit.remove("地区差异")
    planned_items = planned.get("requirements") if isinstance(planned, dict) else planned
    planned_has_unresolved = isinstance(planned_items, list) and any(
        isinstance(unit, dict) and unit.get("kind") == "unresolved_requirement"
        for unit in planned_items
    )
    units = normalize_requirements(text, planned)
    if (not planned and re.search(r"比较|对比|差异|共同", text)
            and any(term in text for term in ("两篇", "多篇", "多份", "各文献"))):
        units = [{**unit, "phase": "synthesis", "actionable": False}
                 if unit["kind"] == "unresolved_requirement" else unit for unit in units]
    if explicit and not planned_has_unresolved:
        units = [unit for unit in units if unit["kind"] != "unresolved_requirement"]
    existing_ids = {unit.get("id") for unit in units}
    units.extend({
        "id": f"dimension:{label}", "kind": "dimension", "label": label,
        "subject": None, "metric": None, "expected_value": None,
        "phase": "synthesis" if label in ANALYTICAL else "evidence",
        "actionable": label not in ANALYTICAL,
    } for label in explicit if f"dimension:{label}" not in existing_ids)
    return {
        "dimensions": explicit,
        "units": units,
        "needs_analysis": any(term in text for term in ANALYSIS_WORDS)
                          or any(label in ANALYTICAL for label in explicit)
                          or any(unit["kind"] in {"analysis", "comparison"} for unit in units),
        "needs_report": (any(term in text for term in ANALYSIS_WORDS)
                         or len(explicit) >= 2
                         or any(unit.get("phase") == "synthesis"
                                or unit.get("kind") in {"analysis", "output_section"}
                                for unit in units)),
    }


def _matching(block: dict, label: str) -> bool:
    terms = INTENT_TERMS.get(label, (label,))
    # Only audited content, not the model's dimension declaration, counts.
    content = " ".join([str(block.get("dimension") or ""),
                        *[str(t) for t in block.get("title_path", [])],
                        str(block.get("text") or "")])
    return any(term in content for term in terms)


def _requirement_delivered(unit: dict, indexes: list[int], result, blocks,
                           catalog_sources: dict) -> bool:
    """Require a supported requirement to appear in an audited report block."""
    if not indexes:
        return False
    supported_indexes = set(indexes)
    answer = str(getattr(result, "answer", "") or "")
    if unit.get("comparison_relation_ids"):
        return any(
            block.get("kind") == "analysis"
            and str(block.get("text") or "") in answer
            and bool(supported_indexes & set(block.get("supporting_claim_indexes") or []))
            and bool({chunk_id for index in supported_indexes
                      & set(block.get("supporting_claim_indexes") or [])
                      for chunk_id in result.claims[index].chunk_ids}
                     & set(block.get("supporting_chunk_ids") or []))
            for block in blocks
        )
    for block in blocks:
        block_indexes = set(block.get("supporting_claim_indexes") or [])
        if not supported_indexes & block_indexes:
            continue
        block_text = str(block.get("text") or "")
        if not block_text or block_text not in answer:
            continue
        text = " ".join([
            str(block.get("dimension") or ""),
            *[str(value) for value in block.get("required_dimensions") or []],
            *[str(value) for value in block.get("title_path") or []],
            block_text,
        ])
        if unit.get("kind") == "metric":
            metric_id = unit.get("metric")
            if not supports_metric_value(unit, text) or not supports_metric_value(unit, answer):
                continue
            for index in supported_indexes & block_indexes:
                claim_value = _measurement_value(result.claims[index].text, metric_id)
                if (claim_value and _measurement_value(text, metric_id) == claim_value
                        and _measurement_value(answer, metric_id) == claim_value):
                    return True
        elif unit.get("kind") == "numeric_fact":
            for index in supported_indexes & block_indexes:
                claim = result.claims[index]
                if any(_measurement_value(claim.text, metric_id)
                       and _measurement_value(text, metric_id)
                       == _measurement_value(claim.text, metric_id)
                       and _measurement_value(answer, metric_id)
                       == _measurement_value(claim.text, metric_id)
                       for metric_id in _METRICS if metric_id != "planning_year"):
                    return True
        elif unit.get("kind") == "dimension":
            if unit.get("label") in (block.get("required_dimensions") or []) \
                    or _matching(block, str(unit.get("label") or "")):
                return True
        elif unit.get("id") in catalog_sources:
            if (catalog_facet_matches_claim(unit, text, catalog_sources)
                    and catalog_facet_matches_claim(unit, answer, catalog_sources)):
                return True
        elif unit.get("subject"):
            from .research_targets import mentions_subject

            if (mentions_subject(text, str(unit["subject"]))
                    and mentions_subject(answer, str(unit["subject"]))):
                return True
        elif unit.get("label") and unit["label"] in text and unit["label"] in answer:
            return True
    return False


def evaluate(result, question: str) -> dict:
    """Report evidence/analytical completeness separately from run success."""
    required = requirements(question, result.meta.get("research_requirements"))
    structured = result.structured if isinstance(result.structured, dict) else {}
    is_report = structured.get("schema_version") == "qa-report/1"
    report_verified = (is_report and
                       structured.get("verification", {}).get("facts")
                       == "semantic_reviewed_against_quotes")
    blocks = (structured.get("blocks") or []) if report_verified else []
    blocks = [block for block in blocks if isinstance(block, dict)
              and block.get("verification") == "model_reviewed_evidence_linked"]
    refs = {ref.chunk_id for ref in result.evidence_refs}
    refs_by_chunk = {ref.chunk_id: ref for ref in result.evidence_refs}
    quote_check = result.meta.get("quote_verification") or {}
    quotes_verified = (isinstance(quote_check, dict)
                       and quote_check.get("unverified") == 0
                       and not result.meta.get("verification_incomplete"))
    verified_positions = [index for index, claim in enumerate(result.claims)
                          if quotes_verified and claim.chunk_ids and claim.quotes
                          and set(claim.chunk_ids) <= refs]
    verified_claims = [result.claims[index] for index in verified_positions]
    analytical_blocks = [block for block in blocks if block.get("kind") == "analysis"]
    report_audited = bool(is_report and report_verified and blocks)
    verified_texts = [
        " ".join([claim.text, *claim.quotes, *[
            "〔章节〕" + str(getattr(refs_by_chunk.get(chunk_id), "section_path", "") or "")
            for chunk_id in claim.chunk_ids
        ]]) for claim in verified_claims
    ]
    verified_by_position = dict(zip(verified_positions, verified_texts))
    verified_chunks = {chunk_id for claim in verified_claims for chunk_id in claim.chunk_ids}
    support_review = result.meta.get("requirement_support") or {}
    frozen_units = result.meta.get("research_requirements")
    support_requirements_fingerprint = requirements_fingerprint(
        frozen_units if isinstance(frozen_units, list) and frozen_units else required["units"]
    )
    approved_supports = (support_review.get("supports")
                         if isinstance(support_review, dict)
                         and support_review.get("verification") == "semantic_reviewed_against_quotes"
                         and support_review.get("claim_fingerprint") == claim_fingerprint(result.claims)
                         and support_review.get("requirements_fingerprint") == support_requirements_fingerprint
                         else {})
    catalog_sources = result.meta.get("catalog_requirement_sources") or {}

    def mapped_indexes(unit: dict) -> list[int]:
        if (unit.get("kind") in {"comparison", "value_check"}
                and support_review.get("basis") != "audited_report"):
            return []
        raw_indexes = approved_supports.get(unit["id"], []) if isinstance(approved_supports, dict) else []
        if not isinstance(raw_indexes, list):
            return []
        output = []
        for index in raw_indexes:
            if type(index) is not int or index not in verified_by_position:
                continue
            if not claim_matches_requirement_source(
                    unit, result.claims[index], refs_by_chunk, catalog_sources):
                continue
            text = verified_by_position[index]
            if (unit.get("content_period")
                    and not _content_period_matches(unit, text)):
                continue
            if not catalog_facet_matches_claim(unit, text, catalog_sources):
                continue
            if (unit.get("kind") in {"subject_fact", "numeric_fact"}
                    and not unit.get("comparison_relation_ids")
                    and not supports_requirement(unit, text)):
                continue
            if unit["kind"] == "metric" and not supports_metric_value(unit, text):
                continue
            if unit["kind"] == "numeric_fact" and not supports_numeric_value(text):
                continue
            if unit["kind"] == "output_section":
                answer = str(getattr(result, "answer", "") or "")
                if unit["label"] not in answer:
                    continue
            output.append(index)
        return list(dict.fromkeys(output))

    covered = [label for label in required["dimensions"] if any(
        _matching(block, label) and (
            label not in ANALYTICAL or block.get("kind") == "analysis"
        ) for block in blocks) or (label not in ANALYTICAL and any(
            _matching({"text": text}, label) for text in verified_texts))
        or bool(mapped_indexes({"id": f"dimension:{label}", "kind": "dimension", "label": label}))]
    missing = [item for item in required["dimensions"] if item not in covered]
    subjects = list(extract_targets(question))
    unit_coverage = []
    for unit in required["units"]:
        block_supported = False
        phase = unit.get("phase", "evidence")
        actionable = bool(unit.get("actionable", True))
        if unit["kind"] in {"dimension", "analysis"}:
            terms_known = unit["label"] in INTENT_TERMS
            evidence_indexes = [verified_positions[index]
                                for index, text in enumerate(verified_texts)
                                if terms_known and _matching({"text": text}, unit["label"])]
            block_supported = any(
                ( _matching(block, unit["label"]) if terms_known else
                  unit["label"] in " ".join(
                      [str(block.get("dimension") or ""),
                       *[str(value) for value in block.get("title_path") or []],
                       str(block.get("text") or "")]
                  ))
                and (phase != "synthesis" or block.get("kind") == "analysis")
                and bool(set(block.get("supporting_chunk_ids") or []) & verified_chunks)
                for block in blocks
            )
            if phase == "synthesis":
                evidence_indexes = []
            block_chunks = {chunk_id for block in blocks
                            if _matching(block, unit["label"])
                            and (unit["label"] not in ANALYTICAL or block.get("kind") == "analysis")
                            for chunk_id in block.get("supporting_chunk_ids") or []}
            evidence_indexes.extend(
                position for position, claim in zip(verified_positions, verified_claims)
                if set(claim.chunk_ids) & block_chunks
            )
        elif unit["kind"] == "source_citation":
            evidence_indexes = list(verified_positions)
        elif unit["kind"] == "output_section":
            answer = str(getattr(result, "answer", "") or "")
            section_evidence = any(unit["label"] in text for text in verified_texts)
            evidence_indexes = [verified_positions[index]
                                for index, text in enumerate(verified_texts)
                                if unit["label"] in text and unit["label"] in answer]
            block_supported = any(
                unit["label"] in " ".join(
                    [str(item) for item in block.get("title_path", [])]
                    + [str(block.get("text") or "")]
                )
                and bool(set(block.get("supporting_chunk_ids") or []) & verified_chunks)
                for block in blocks
            )
            block_chunks = {chunk_id for block in blocks
                            if unit["label"] in " ".join(
                                [str(item) for item in block.get("title_path", [])]
                                + [str(block.get("text") or "")]
                            )
                            for chunk_id in block.get("supporting_chunk_ids") or []}
            evidence_indexes.extend(
                position for position, claim in zip(verified_positions, verified_claims)
                if set(claim.chunk_ids) & block_chunks
            )
            if section_evidence:
                phase, actionable = "synthesis", False
            else:
                phase, actionable = "evidence", True
        elif unit["kind"] == "comparison":
            prior_coverage = {item["id"]: item for item in unit_coverage}
            left = prior_coverage.get(unit.get("left_requirement_id"), {})
            right = prior_coverage.get(unit.get("right_requirement_id"), {})
            left_indexes = list(left.get("evidence_claim_indexes") or [])
            right_indexes = list(right.get("evidence_claim_indexes") or [])
            both_sides_covered = (left.get("status") == "covered"
                                  and right.get("status") == "covered")
            left_chunks = {chunk_id for index in left_indexes
                           for chunk_id in result.claims[index].chunk_ids}
            right_chunks = {chunk_id for index in right_indexes
                            for chunk_id in result.claims[index].chunk_ids}
            block_supported = bool(both_sides_covered and any(
                block.get("kind") == "analysis"
                and left_chunks & set(block.get("supporting_chunk_ids") or [])
                and right_chunks & set(block.get("supporting_chunk_ids") or [])
                for block in blocks
            ))
            if both_sides_covered:
                phase, actionable = "synthesis", False
            else:
                phase, actionable = "evidence", False
            evidence_indexes = left_indexes + right_indexes if block_supported else []
        elif unit["kind"] == "value_check":
            fact_requirement = {**unit, "kind": "metric", "expected_value": None}
            fact_indexes = [verified_positions[index]
                            for index, text in enumerate(verified_texts)
                            if supports_requirement(fact_requirement, text)]
            if fact_indexes:
                phase, actionable = "synthesis", False
                fact_chunks = {chunk_id for index in fact_indexes
                               for chunk_id in result.claims[index].chunk_ids}
                aliases = _METRICS.get(unit.get("metric"), ("", ("",)))[1]
                block_supported = any(
                    block.get("kind") == "analysis"
                    and bool(fact_chunks & set(block.get("supporting_chunk_ids") or []))
                    and any(alias and alias in " ".join(
                        [str(block.get("dimension") or ""),
                         *[str(value) for value in block.get("title_path") or []],
                         str(block.get("text") or "")]
                    ) for alias in aliases)
                    for block in blocks
                )
                evidence_indexes = fact_indexes if block_supported else []
            else:
                phase, actionable = "evidence", True
                evidence_indexes = []
        else:
            evidence_indexes = [verified_positions[index]
                                for index, text in enumerate(verified_texts)
                                if supports_requirement(unit, text)
                                and claim_matches_requirement_source(
                                    unit, verified_claims[index], refs_by_chunk,
                                    catalog_sources)]
        if unit.get("kind") == "comparison":
            pass
        elif phase == "synthesis" or unit.get("kind") in {"analysis", "value_check"}:
            reviewed_indexes = mapped_indexes(unit)
            supporting_chunks = {cid for index in reviewed_indexes
                                 for cid in result.claims[index].chunk_ids}
            block_supported = block_supported or bool(
                support_review.get("basis") == "audited_report"
                and supporting_chunks and any(
                    set(block.get("supporting_chunk_ids") or []) & supporting_chunks
                    and (unit.get("kind") == "output_section" or block.get("kind") == "analysis")
                    for block in blocks))
            if block_supported:
                evidence_indexes.extend(reviewed_indexes)
        else:
            evidence_indexes.extend(mapped_indexes(unit))
        delivery_missing = bool(
            report_audited and phase == "evidence" and evidence_indexes
            and unit.get("kind") in {"metric", "numeric_fact", "subject_fact", "dimension"}
            and not _requirement_delivered(unit, evidence_indexes, result, blocks,
                                           catalog_sources)
        )
        if delivery_missing:
            phase, actionable = "synthesis", False
        unit_coverage.append({
            **unit,
            "status": "covered" if (evidence_indexes or block_supported)
            and not delivery_missing else "missing",
            "phase": phase,
            "actionable": actionable,
            "delivery_status": "missing" if delivery_missing else "delivered",
            "evidence_claim_indexes": sorted(set(evidence_indexes)),
            "gap_label": unit["label"],
        })
    covered_requirements = [item["label"] for item in unit_coverage
                            if item["status"] == "covered"]
    missing_requirements = [item["label"] for item in unit_coverage
                            if item["status"] == "missing"]
    missing_subjects = [item["subject"] for item in unit_coverage
                        if item["status"] == "missing" and item.get("subject")]
    missing_numeric_subjects = [item["subject"] for item in unit_coverage
                                if item["status"] == "missing"
                                and item.get("kind") == "numeric_fact"
                                and isinstance(item.get("subject"), str)
                                and item["subject"].strip()]
    simple_fact = (not required["needs_report"] and not required["needs_analysis"]
                   and bool(unit_coverage) and not missing_requirements)
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
    gaps.extend("subject_uncovered:" + name for name in missing_subjects)
    gaps.extend("subject_numeric_missing:" + name for name in missing_numeric_subjects)
    gaps.extend("requirement_uncovered:" + item["id"] for item in unit_coverage
                if item["status"] == "missing")
    gaps.extend("delivery_requirement_missing:" + item["id"] for item in unit_coverage
                if item.get("delivery_status") == "missing")
    evidence_gaps = [item for item in unit_coverage
                     if item["status"] == "missing" and item["phase"] == "evidence"]
    synthesis_gaps = [item for item in unit_coverage
                      if item["status"] == "missing" and item["phase"] == "synthesis"]

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
    # The audited report only proves individual blocks were reviewed. It may
    # still lack a required document after claim pruning; final coverage must
    # remain an independent completion gate.
    coverage = result.meta.get("evidence_coverage") or {}
    if isinstance(coverage, dict) and coverage:
        for gap in coverage.get("gaps") or []:
            if gap not in gaps:
                gaps.append("coverage:" + gap)
        if coverage.get("status") == "insufficient":
            gaps.append("evidence_coverage_insufficient")
    # For requested cross-document comparisons, a reviewed analytical block
    # must itself rely on verified facts from distinct policy identities.
    from .evidence_coverage import multi_document_required

    if multi_document_required(question):
        chunk_planids = {ref.chunk_id: ref.planid for ref in result.evidence_refs}
        cross_document_analysis = any(
            len({chunk_planids.get(cid) for cid in block.get("supporting_chunk_ids") or []
                 if chunk_planids.get(cid)}) >= 2
            for block in analytical_blocks
        )
        if required["needs_analysis"] and not cross_document_analysis:
            gaps.append("cross_document_analysis_missing")
    if result.status != "completed" or result.error is not None:
        gaps.append("execution_not_completed")
    gaps = list(dict.fromkeys(gaps))
    status = ("complete" if not gaps and result.status == "completed"
              and result.error is None and result.evidence_status == "verified"
              and (not coverage or coverage.get("status") == "complete") else
              "partial" if verified_claims else "insufficient")
    return {
        "status": status, "required_dimensions": required["dimensions"],
        "requirement_coverage": unit_coverage,
        "covered_requirements": covered_requirements,
        "missing_requirements": missing_requirements,
        "gap_labels": list(dict.fromkeys(missing_requirements + missing)),
        "evidence_gaps": evidence_gaps,
        "synthesis_gaps": synthesis_gaps,
        "required_subjects": subjects,
        "missing_subjects": missing_subjects,
        "missing_numeric_subjects": missing_numeric_subjects,
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
    """Compatibility wrapper returning the first item from all actionable gaps."""
    gaps = followup_gaps(result, question)
    return gaps[0] if gaps else None


def followup_gaps(result, question: str) -> list[str]:
    """Return every unmet requirement as a bounded, independently queryable hint."""
    if result.meta.get("verification_incomplete"):
        return []
    quote_check = result.meta.get("quote_verification") or {}
    refs = {ref.chunk_id for ref in getattr(result, "evidence_refs", []) or []}
    quotes_verified = (isinstance(quote_check, dict)
                       and quote_check.get("unverified") == 0)
    verified = [claim for claim in result.claims if quotes_verified
                and claim.quotes and claim.chunk_ids
                and set(claim.chunk_ids) <= refs]
    texts = [" ".join([claim.text, *claim.quotes]) for claim in verified]
    units = requirements(question)["units"]
    gaps = []
    for unit in units:
        if unit.get("phase") == "synthesis" or not unit.get("actionable", True):
            continue
        if unit["kind"] == "dimension":
            present = any(_matching({"text": text}, unit["label"]) for text in texts)
        elif unit["kind"] == "comparison":
            present = any(unit["left"] in text for text in texts) and any(
                unit["right"] in text for text in texts)
        elif unit["kind"] == "value_check":
            present = any(supports_requirement(
                {**unit, "kind": "metric", "expected_value": None}, text
            ) for text in texts)
        elif unit["kind"] == "output_section":
            present = any(unit["label"] in text for text in texts)
        else:
            present = any(supports_requirement(unit, text) for text in texts)
        if not present:
            focus = " ".join(dict.fromkeys(
                item for item in (unit.get("subject"), unit.get("label")) if item
            ))
            if focus and focus not in gaps:
                gaps.append(focus)

    drafts = list(getattr(result, "_qa_report_candidates", []) or [])
    for label in requirements(question)["dimensions"]:
        if label in ANALYTICAL:
            continue
        present = any(_matching(block, label)
                      and (label not in ANALYTICAL or block.get("kind") == "analysis")
                      for block in drafts) or (label not in ANALYTICAL and any(
                          _matching({"text": text}, label) for text in texts))
        if not present and label not in gaps:
            gaps.append(label)
    return gaps
