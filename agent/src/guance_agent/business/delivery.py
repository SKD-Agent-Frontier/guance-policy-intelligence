"""Build the public business schemas using only gateway-approved facts and analyses."""
from ..schemas import TaskRequest
from .analysis import analysis_candidates
from .compare.result import _comparability


def extract_source_lines(claims, refs_by_id) -> str:
    lines = []
    for claim in claims:
        if len(claim.chunk_ids) == len(claim.quotes) == 1:
            ref = refs_by_id.get(claim.chunk_ids[0])
            if ref and claim.quotes[0]:
                lines.append(f"《{ref.title}》{ref.section_path or ''}：「{claim.quotes[0]}」")
    return "\n".join(dict.fromkeys(lines))


def project_business(task: TaskRequest, claims: list, refs_by_id: dict,
                         research_state=None, approved_blocks=()) -> dict | None:
    """Rebuild conservative business views solely from surviving claims/refs."""
    kind = task.task_type
    builtin = research_state is not None and kind in research_state.builtin_capabilities
    candidate = research_state.capability_structured.get(kind, {}) if builtin else {}
    targets = (task.targets or (research_state.capability_targets.get(kind)
                               if research_state is not None else None)) or None
    if not claims and kind != "extract":
        if research_state is None or kind == "qa":
            return None
        if research_state.capability_fact_ids.get(kind) or research_state.capability_dropped.get(kind):
            return {}
        return research_state.capability_documents.get(kind, {})
    refs_by_planid = {}
    for ref in refs_by_id.values():
        refs_by_planid.setdefault(ref.planid, ref)
    if task.task_type == "extract":
        from ..schemas import DIMENSIONS

        dimensions = task.dimensions or list(DIMENSIONS)
        covered = {claim.dimension for claim in claims if claim.dimension in dimensions}
        claims = [claim for claim in claims if claim.dimension in dimensions]
        return {
            "dimensions": dimensions,
            "documents": [{key: getattr(ref, key) for key in
                           ("planid", "title", "area", "fyp")}
                          for ref in refs_by_planid.values()],
            "elements": [claim.model_dump(mode="json") for claim in claims],
            "missing": [{"dimension": name, "reason": ("retrieval_unavailable" if research_state is not None and any(
                            call.get("status") in {"timeout", "backend_error"}
                            for call in research_state.run.tool_calls) else "not_verified")}
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
        # Scope, acquisition diagnostics and source identity are produced by the
        # built-in collection code. Model-produced summaries and group labels
        # must be reconstructed from the surviving facts.
        original = {p["planid"]: p for p in candidate.get("policies", [])}
        for planid in dict.fromkeys(targets or [*original, *indexes_by_planid]):
            indexes = indexes_by_planid.get(planid, [])
            item = original.get(planid, {})
            ref = refs_by_planid.get(planid)
            policy = {key: item[key] for key in (
                "read_attempts", "chunk_ids_hit", "chunks_read", "plantype_label",
                "admin_level") if key in item}
            status = item.get("read_status")
            if status not in {"no_text", "read_failed", "not_retrieved", "read_no_valid_points"}:
                status = "read_no_valid_points" if item.get("chunks_read") else "not_retrieved"
            policy.update(planid=planid, title=getattr(ref, "title", item.get("title")),
                          area=getattr(ref, "area", item.get("area")),
                          fyp=getattr(ref, "fyp", item.get("fyp")),
                          read_status="summarized" if indexes else status,
                          summary="；".join(claims[i].text for i in indexes) or None,
                          claim_indexes=indexes, topics=[], relevance=None,
                          relevance_note=None, full_text_covered=False)
            policies.append(policy)
        summarized = len(indexes_by_planid)
        observed = candidate.get("coverage", {})
        coverage = {key: observed[key] for key in (
            "documents_found", "documents_after_filter", "documents_read", "chunks_read",
            "note", "limits") if key in observed}
        scope = {key: candidate["scope"][key] for key in (
            "topic", "areas", "fyps", "plantypes", "categories", "dimensions", "effective",
            "post_filters", "unapplied") if key in candidate.get("scope", {})}
        coverage["documents_summarized"] = summarized
        return {
            "schema_version": "organize/1",
            "scope": {**scope, "targets": targets},
            "coverage": coverage, "policies": policies,
            "groups": [{"topic": topic, **entry} for topic, entry in topic_map.items()],
            "verification": {"policy_facts": "claims_only", "analysis_fields": [],
                             "note": "未审核的分类和相关性分析已省略。"},
        }
    if task.task_type in {"compare", "compare_plans"}:
        dimensions = list(dict.fromkeys([
            *(task.dimensions or candidate.get("dimensions", [])),
            *(claim.dimension for claim in claims if claim.dimension),
        ]))
        planids = list(targets or refs_by_planid)
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
        if candidate:
            project_comparison(output, candidate, task, research_state, approved_blocks)
        return output
    return None



def project_comparison(output, candidate, task, state, approved_blocks):
    approved = {block["id"]: block for block in approved_blocks if block.get("kind") == "analysis"}
    accepted = set()
    for item in analysis_candidates(state, task.task_type):
        block = approved.get(item["id"])
        fact_ids = {fact_id for fact_id, handle in state.fact_handles.items()
                    if handle in item["evidence_ids"]}
        if (block and block.get("text") == item["text"]
                and fact_ids == set(block.get("supporting_evidence_ids", []))):
            accepted.add(item["id"])

    meta = {obj["planid"]: obj for obj in candidate.get("objects", ())}
    for obj in output["objects"]:
        source = meta.get(obj["planid"], {})
        obj.update({key: source[key] for key in (
            "title", "area", "fyp", "plantype_label", "admin_level") if key in source})
    gaps = list(candidate.get("analysis_gaps", ()))
    for i, row in enumerate(output["matrix"]):
        original = next((r for r in candidate["matrix"] if r["dimension"] == row["dimension"]), {})
        for cell in row["cells"]:
            source = next((c for c in original.get("cells", ()) if c["planid"] == cell["planid"]), {})
            if source.get("status") == "not_retrieved" and cell.get("status") != "found":
                cell.update(status="not_retrieved", reason="本轮未取得相关正文")
        row["comparability"] = _comparability(output["objects"], row["cells"], None)
        if (f"{task.task_type}-row-{i}" in accepted and original
                and all(cell["status"] == "found" for cell in row["cells"])):
            row["difference"] = dict(original["difference"])
        elif original.get("difference", {}).get("status") == "supported":
            gaps.append(dict(field="difference", dimension=row["dimension"], reason="semantic_review_missing"))
    if task.task_type == "compare_plans":
        output["evaluation_criteria"] = [criterion for criterion in candidate.get("evaluation_criteria", ())
                                          if criterion in task.question]
        for i, scheme in enumerate(candidate.get("scheme_analysis", ())):
            public = next((s for s in output["scheme_analysis"] if s["planid"] == scheme["planid"]), None)
            if public:
                for field in ("advantages", "limitations", "applicability"):
                    public[field] = [dict(judgment) for j, judgment in enumerate(scheme[field])
                                     if f"{task.task_type}-scheme-{i}-{field}-{j}" in accepted]
                    if scheme[field] and not public[field]:
                        gaps.append(dict(field=field, planid=scheme["planid"], reason="semantic_review_missing"))
    supported = [row["dimension"] for row in output["matrix"] if row["difference"]["status"] == "supported"]
    output["comparability"] = {
        "comparable": bool(output["matrix"]) and all(row["comparability"]["comparable"] for row in output["matrix"]),
        "reason": "；".join(dict.fromkeys(row["comparability"]["reason"] for row in output["matrix"])),
    }
    output["coverage_notes"] = list(candidate.get("coverage_notes", ()))
    output["analysis_gaps"] = gaps
    output["task_completion"] = {
        "status": "complete" if len(supported) == len(output["dimensions"]) and not gaps else "partial",
        "requested_dimensions": output["dimensions"], "supported_dimensions": supported,
        "unsupported_dimensions": [dim for dim in output["dimensions"] if dim not in supported],
    }

def business_notice(task, state, structured):
    if state is None or task.task_type == "qa":
        return ""
    kind = task.task_type
    if kind not in state.capabilities_used:
        return ""
    messages = []
    if not state.capability_fact_ids.get(kind):
        statuses = {call["status"] for call in state.run.tool_calls if call.get("name") in {"kb_search", "kb_read"}}
        if statuses & {"timeout", "backend_error"}:
            messages.append("知识库检索或回读未能完成，当前缺少可核验的原文。")
        elif statuses == {"empty"}:
            messages.append("本轮未检索到相关政策原文。")
        else:
            messages.append("本轮未能从已检索的文献中形成可核验事实。")
    if structured and kind == "organize":
        if structured.get("scope", {}).get("post_filters"):
            messages.append("文献类型后过滤仅对本轮候选生效。")
        if state.capability_insufficient.get(kind):
            messages.append("有效摘要文献不足，本轮跨文献梳理的证据仍不充分。")
    if structured and kind in {"compare", "compare_plans"}:
        if any(gap.get("reason") == "source_evidence_missing" for gap in structured.get("analysis_gaps", ())):
            messages.append("部分比较对象的相关原文证据不足，未完成的维度见比较矩阵。")
    return "\n".join(messages)
