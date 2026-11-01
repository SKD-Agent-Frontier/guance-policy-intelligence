"""Build executable, model-free research plans around the existing capabilities."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .research_contract import (
    PolicyDocument,
    ResearchCoverage,
    ResearchPlan,
    ResearchScope,
    ResearchStep,
    ResearchSubject,
    TaskContract,
)


class ResearchDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: Literal["ready", "fallback", "needs_clarification", "insufficient", "unavailable"]
    reason_code: Literal[
        "user_scope_missing", "ambiguous_document_identity", "catalog_evidence_missing",
        "requested_document_missing", "insufficient_document_coverage", "catalog_unavailable",
        "kb_fallback", "organize_fallback",
    ] | None = None
    requested_subjects: list[StrictStr] = Field(default_factory=list)
    coverage_limited_subjects: list[StrictStr] = Field(default_factory=list)
    plan: ResearchPlan
    message: StrictStr | None = None


class ResearchPlanner:
    """Deterministic planning adapter; it never makes an additional model call."""

    @staticmethod
    def initial(contract: TaskContract) -> ResearchPlan:
        plan = ResearchPlan.for_contract(contract)
        capability = contract.task_type
        discovery_capability = "kb_search" if capability in ("qa", "organize") else "policy_catalog"
        steps = [
            ResearchStep(step_id="discover", description="发现并核验研究对象身份",
                         capability=discovery_capability),
            ResearchStep(step_id="execute", description=f"执行{capability}研究能力",
                         capability=capability, depends_on=["discover"]),
            ResearchStep(step_id="verify", description="核验来源、引文与任务完成条件",
                         capability="evidence_verify", depends_on=["execute"]),
        ]
        initial = plan.model_dump(mode="python")
        initial.update({"steps": steps, "status": "draft"})
        return ResearchPlan.model_validate(initial)

    @staticmethod
    def with_catalog_documents(
        contract: TaskContract,
        plan: ResearchPlan,
        policies: list[dict],
        *,
        status: Literal["ready", "fallback", "needs_clarification", "insufficient", "unavailable"] = "ready",
        message: str | None = None,
        reason_code: str | None = None,
        requested_subjects: list[str] | None = None,
        subject_gaps: list[str] | None = None,
        coverage_limited_subjects: list[str] | None = None,
    ) -> ResearchDiscovery:
        """Attach only identities returned by the trusted catalog resolver."""
        documents: list[PolicyDocument] = []
        subject_labels: list[str] = []
        document_subjects: dict[str, list[str]] = {}
        # Comparison subjects may be administrative regions OR distinct policies.
        # For same-region comparisons (and all scheme comparisons), using the
        # region label alone would collapse two different policy subjects into one.
        regions = {policy.get("area") for policy in policies
                   if isinstance(policy, dict) and policy.get("area")}
        by_policy = (contract.task_type == "compare_plans"
                     or (contract.task_type == "compare" and len(regions) <= 1))
        for policy in policies:
            if not isinstance(policy, dict):
                raise ValueError("catalog result must contain document objects")
            planid = policy.get("planid")
            title = policy.get("title")
            if not isinstance(planid, str) or not planid or not isinstance(title, str) or not title.strip():
                raise ValueError("catalog document identity is incomplete")
            if not isinstance(policy.get("document_id"), str) or not isinstance(policy.get("generation_id"), str):
                raise ValueError("verified catalog documents require source identity")
            area = policy.get("area") if isinstance(policy.get("area"), str) else None
            label = f"{title}（planid={planid}）" if by_policy else (area or title)
            if label not in subject_labels:
                subject_labels.append(label)
            subject_id = f"subject_{subject_labels.index(label) + 1}"
            document_subjects.setdefault(planid, []).append(subject_id)
            documents.append(PolicyDocument(
                planid=planid,
                document_id=policy.get("document_id"),
                document_version_id=policy.get("document_version_id"),
                generation_id=policy.get("generation_id"),
                source_hash=policy.get("source_hash"),
                title=title,
                area=area,
                fyp=policy.get("fyp") if isinstance(policy.get("fyp"), str) else None,
                admin_level=policy.get("admin_level") if isinstance(policy.get("admin_level"), str) else None,
                plantype_label=policy.get("plantype_label") if isinstance(policy.get("plantype_label"), str) else None,
                source="policy_catalog",
                source_verified=True,
                subject_ids=list(dict.fromkeys(document_subjects[planid])),
            ))
        documents_by_id = {document.planid: document for document in documents}
        subjects: list[ResearchSubject] = []
        for index, label in enumerate(subject_labels, start=1):
            subject_id = f"subject_{index}"
            subjects.append(ResearchSubject(
                subject_id=subject_id,
                label=label,
                kind="region" if any(doc.area == label for doc in documents) else "policy_topic",
                document_ids=[planid for planid, ids in document_subjects.items()
                              if subject_id in ids],
            ))
        discovered_planids = list(dict.fromkeys(document.planid for document in documents))
        targets = list(discovered_planids)
        if contract.scope.target_planids:
            targets = list(contract.scope.target_planids)
        areas = list(dict.fromkeys(doc.area for doc in documents if doc.area))
        periods = list(dict.fromkeys(doc.fyp for doc in documents if doc.fyp))
        # scope records what the user allowed, not the regions of every located
        # policy. A province bound permits its verified prefectures; expanding the
        # bound with those city names would falsely violate the TaskContract.
        # The actual discovered areas/periods remain in PolicyDocument and subjects.
        scope = ResearchScope(
            areas=list(contract.scope.areas) or areas,
            periods=list(contract.scope.periods) or periods,
            topics=list(contract.scope.topics),
            target_planids=targets,
        )
        coverage = ResearchCoverage(
            required_subject_ids=[subject.subject_id for subject in subjects],
            subject_gaps=list(dict.fromkeys(subject_gaps or [])),
            coverage_limited_subjects=list(dict.fromkeys(coverage_limited_subjects or [])),
            discovered_planids=discovered_planids,
            required_dimensions=list(contract.completion.required_dimensions),
        )
        requested_labels = list(dict.fromkeys(
            label.strip() for label in (requested_subjects or [])
            if isinstance(label, str) and label.strip()
        ))
        unresolved = set(subject_gaps or [])
        execution_inputs = []
        if status == "ready":
            execution_inputs = (
                [f"subject:{label}" for label in requested_labels]
                if unresolved else [f"document:{planid}" for planid in discovered_planids]
            )
        steps = [
            ResearchStep(step_id="discover", description="从可信发布目录发现政策文献",
                         capability="policy_catalog", status="completed" if status == "ready" else "pending"),
            ResearchStep(step_id="execute", description=f"执行{contract.task_type}研究能力",
                         capability=contract.task_type, input_refs=execution_inputs,
                         depends_on=["discover"]),
            ResearchStep(step_id="verify", description="核验来源、引文与任务完成条件",
                         capability="evidence_verify", depends_on=["execute"]),
        ]
        discovered_data = plan.model_dump(mode="python")
        discovered_data.update({
            "scope": scope,
            "subjects": subjects,
            "documents": list(documents_by_id.values()),
            "dimensions": list(plan.dimensions or contract.completion.required_dimensions),
            "steps": steps,
            "coverage": coverage,
            "requested_subjects": requested_labels,
            "status": "ready" if status == "ready" else "draft",
        })
        discovered = ResearchPlan.model_validate(discovered_data)
        return ResearchDiscovery(status=status, plan=discovered,
                                 requested_subjects=list(discovered.requested_subjects),
                                 coverage_limited_subjects=list(
                                     discovered.coverage.coverage_limited_subjects),
                                 message=message,
                                 reason_code=reason_code)

    @staticmethod
    def fallback(plan: ResearchPlan, message: str | None = None,
                 reason_code: Literal["kb_fallback", "organize_fallback"] = "kb_fallback") -> ResearchDiscovery:
        return ResearchDiscovery(status="fallback", reason_code=reason_code,
                                 plan=plan, message=message)
