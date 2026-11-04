"""Web adapter that resolves trusted catalog documents for the agent runtime."""
from __future__ import annotations

import asyncio
import time

from guance_agent.research_contract import ResearchPlan, TaskContract
from guance_agent.research_planner import ResearchDiscovery, ResearchPlanner

from .policy_catalog import PolicyCatalogService
from .research_planning import resolve_research_internal

# Catalog resolution reasons are a separate public API vocabulary. Never pass
# them unchecked to the strict ResearchDiscovery contract (the previous code
# raised ValidationError/INTERNAL for valid insufficient-peer results).
_RESOLUTION_REASONS = {
    "user_scope_missing": "user_scope_missing",
    "user_period_missing": "user_scope_missing",
    "ambiguous_document_identity": "ambiguous_document_identity",
    "anchor_document_missing": "catalog_evidence_missing",
    "comparison_document_missing": "catalog_evidence_missing",
    "insufficient_peer_coverage": "insufficient_document_coverage",
    "insufficient_document_coverage": "insufficient_document_coverage",
    "requested_document_missing": "requested_document_missing",
    "catalog_evidence_missing": "catalog_evidence_missing",
    "catalog_unavailable": "catalog_unavailable",
}


def _discovery_reason(reason: str | None) -> str:
    """Translate a resolver detail into the shared, validated failure category."""
    return _RESOLUTION_REASONS.get(reason, "catalog_evidence_missing")


def _catalog_region_within_scope(catalog: PolicyCatalogService, record, allowed_areas: list[str]) -> bool:
    """Verify record location against user geography using catalog lineage, not city strings.

    Province-level bounds cover verified descendants. A record with no reliable
    mapping may only satisfy an exact explicit region name, never a claimed
    parent/child relationship based on text similarity.
    """
    if not allowed_areas:
        return True
    mapping = (catalog.region_mapping or {}).get(record.planid) or {}
    code = mapping.get("region_code") if mapping.get("region_status") == "mapped" else None
    if code and code in catalog._region_nodes:
        node = catalog._region_nodes[code]
        # Disagreement between the mapped administrative name and the original
        # record is not sufficient to assert a scope match.
        if record.area and node["name"] != record.area:
            return False
        while code is not None:
            ancestor = catalog._region_nodes.get(code)
            if ancestor and ancestor["name"] in allowed_areas:
                return True
            code = catalog._region_parent.get(code)
        return False
    return bool(record.area and record.area in allowed_areas)


async def discover_run_plan(task, contract: TaskContract, plan: ResearchPlan,
                           deadline: float, catalog: PolicyCatalogService) -> ResearchDiscovery:
    """Use the frozen catalog within the same run deadline; IDs are never model-created."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    records = {record.planid: record for record in catalog.snapshot.records}
    if task.targets is not None:
        missing = [target for target in task.targets if target not in records]
        if missing:
            return ResearchPlanner.with_catalog_documents(
                contract, plan, [], status="insufficient", reason_code="requested_document_missing",
                message="指定的政策文献不在当前发布目录中，不能执行该研究。",
            )
        selected = [records[target] for target in task.targets]
        required = max(contract.completion.min_documents,
                       contract.completion.min_subjects,
                       contract.completion.min_summarized_documents)
        if len(selected) < required:
            return ResearchPlanner.with_catalog_documents(
                contract, plan, [], status="insufficient", reason_code="insufficient_document_coverage",
                message="当前明确的政策文献数量不足以满足任务要求。",
            )
        resolution = {
            "status": "ready",
            "request": {"dimensions": task.dimensions or None},
            "policies": [{"planid": item.planid} for item in selected],
        }
    else:
        resolution = await asyncio.wait_for(
            asyncio.to_thread(resolve_research_internal, task.task_type, task.question, catalog),
            timeout=remaining,
        )
    policies = resolution.get("policies", [])
    trusted = []
    for policy in policies:
        record = records.get(policy.get("planid")) if isinstance(policy, dict) else None
        if record is None:
            continue
        if not isinstance(record.title, str) or not record.title.strip():
            return ResearchPlanner.with_catalog_documents(
                contract, plan, [], status="insufficient", reason_code="catalog_evidence_missing",
                message="发布目录中的文献缺少可核验标题，不能作为研究对象。",
            )
        trusted.append({
            "planid": record.planid,
            "title": record.title or "",
            "area": record.area,
            "fyp": record.fyp,
            "admin_level": record.admin_level,
            "plantype_label": record.plantype_label,
            "document_id": record.document_id,
            "generation_id": catalog.snapshot.generation_id,
            "source": "policy_catalog",
        })
    # Do not validate regions by copying discovered city names into user
    # scope. Check the catalog's verified parent chain before binding any plan.
    if trusted and contract.scope.areas:
        out_of_scope = [entry["planid"] for entry in trusted
                        if not _catalog_region_within_scope(
                            catalog, records[entry["planid"]], contract.scope.areas)]
        if out_of_scope:
            return ResearchPlanner.with_catalog_documents(
                contract, plan, [], status="insufficient",
                reason_code="catalog_evidence_missing",
                message="发现的政策文献地区与用户指定的研究范围不一致，已停止执行。",
            )
    if task.targets is not None:
        trusted_ids = {record["planid"] for record in trusted}
        if not set(task.targets) <= trusted_ids:
            missing = [target for target in task.targets if target not in trusted_ids]
            resolution = {
                "status": "insufficient",
                "reason": "requested_document_missing",
                "message": "指定的政策文献不在当前发布目录中，不能执行该研究。",
                "request": None,
                "policies": [],
            }
            trusted = []
        else:
            trusted = [record for record in trusted if record["planid"] in task.targets]
    if resolution.get("status") != "ready":
        if resolution.get("status") == "fallback" and task.task_type in ("qa", "organize"):
            return ResearchPlanner.fallback(
                plan, resolution.get("message"),
                reason_code="organize_fallback" if task.task_type == "organize" else "kb_fallback")
        if task.task_type == "organize" and task.targets is None:
            return ResearchPlanner.fallback(plan, resolution.get("message"), reason_code="organize_fallback")
        return ResearchPlanner.with_catalog_documents(
            contract, plan, trusted if resolution.get("status") == "insufficient" else [],
            status="needs_clarification" if resolution.get("status") == "needs_clarification" else "insufficient",
            message=resolution.get("message") or "当前发布目录不能确认足够的研究对象。",
            reason_code=_discovery_reason(resolution.get("reason")),
            requested_subjects=resolution.get("requested_subjects", []),
            subject_gaps=resolution.get("subject_gaps", []),
            coverage_limited_subjects=resolution.get("coverage_limited_subjects", []),
        )
    if not trusted:
        if task.task_type == "qa":
            return ResearchPlanner.fallback(plan, "目录未确认单篇文献，继续使用现有 RAG 检索能力。")
        return ResearchPlanner.with_catalog_documents(
            contract, plan, [], status="insufficient", reason_code="catalog_evidence_missing",
            message="当前发布目录未确认可执行的政策文献。",
        )
    result = ResearchPlanner.with_catalog_documents(
        contract, plan, trusted,
        requested_subjects=resolution.get("requested_subjects", []),
        subject_gaps=resolution.get("subject_gaps", []),
        coverage_limited_subjects=resolution.get("coverage_limited_subjects", []),
    )
    dimensions = resolution.get("request", {}).get("dimensions") if resolution.get("request") else None
    if dimensions:
        # Explicit dimensions are the locked completion criteria. Catalog
        # hints may fill an empty plan, but cannot expand a user-specified list.
        selected_dimensions = (
            [*result.plan.dimensions, *dimensions]
            if not contract.completion.required_dimensions else result.plan.dimensions
        )
        merged_dimensions = list(dict.fromkeys(selected_dimensions))
        result = result.model_copy(update={"plan": result.plan.model_copy(
            update={"dimensions": merged_dimensions})})
    return result
