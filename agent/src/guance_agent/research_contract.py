"""Strict contracts for a unified policy research task and its executable plan."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from .routing import decide
from .schemas import DIMENSIONS

ResearchTaskType = Literal["qa", "extract", "organize", "compare", "compare_plans"]
Capability = Literal[
    "qa", "extract", "organize", "compare", "compare_plans", "policy_catalog",
    "kb_search", "kb_read", "evidence_verify",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ResearchScope(StrictModel):
    """Research bounds. Empty lists mean the user did not set that bound."""

    areas: list[StrictStr] = Field(default_factory=list)
    periods: list[StrictStr] = Field(default_factory=list)
    topics: list[StrictStr] = Field(default_factory=list)
    target_planids: list[StrictStr] = Field(default_factory=list)


class CompletionRequirements(StrictModel):
    required_dimensions: list[StrictStr] = Field(default_factory=list)
    min_documents: StrictInt = Field(default=0, ge=0)
    min_summarized_documents: StrictInt = Field(default=0, ge=0)
    min_subjects: StrictInt = Field(default=0, ge=0)
    required_scheme_fields: list[Literal["advantages", "limitations", "applicability"]] = Field(
        default_factory=list)
    require_answer: StrictBool = True
    require_verified_claims: StrictBool = True


class TaskContract(StrictModel):
    """Defines task identity, user constraints, evidence policy, and acceptance criteria."""

    task_type: ResearchTaskType
    research_goal: StrictStr = Field(min_length=1)
    scope: ResearchScope = Field(default_factory=ResearchScope)
    explicit_constraints: list[StrictStr] = Field(default_factory=list)
    completion: CompletionRequirements = Field(default_factory=CompletionRequirements)
    evidence_requirements: list[StrictStr] = Field(default_factory=list)
    mode_locked: StrictBool = True

    @classmethod
    def from_task(cls, task, decision=None) -> "TaskContract":
        task_type = task.task_type
        targets = list(task.targets or [])
        dimensions = list(dict.fromkeys(task.dimensions or []))
        decision = decision or decide(task.question)
        areas = list(getattr(decision, "areas", ()))
        periods = list(getattr(decision, "fyps", ()))
        if task_type == "extract" and not dimensions:
            dimensions = list(DIMENSIONS)
        completion = CompletionRequirements(
            required_dimensions=dimensions if task_type in ("qa", "extract", "compare", "compare_plans") else [],
            min_documents=(2 if task_type in ("compare", "compare_plans") else
                           1 if task_type == "extract" else 0),
            min_summarized_documents=2 if task_type == "organize" else 0,
            min_subjects=(2 if task_type in ("compare", "compare_plans")
                          else 1 if task_type == "extract" else 0),
            required_scheme_fields=(
                ["advantages", "limitations", "applicability"] if task_type == "compare_plans" else []),
        )
        return cls(
            task_type=task_type,
            research_goal=task.question,
            scope=ResearchScope(areas=areas, periods=periods, target_planids=targets),
            explicit_constraints=[f"task_type={task_type}", *(f"target={pid}" for pid in targets),
                                  *(f"parsed_area={area}" for area in areas),
                                  *(f"parsed_period={period}" for period in periods),
                                  *(f"dimension={item}" for item in dimensions)],
            completion=completion,
            evidence_requirements=[
                "事实结论只能引用本轮证据注册表中的来源。",
                "引文和数字须通过现有统一核验。",
                "分析结论须关联已经核验的事实依据。",
            ],
            mode_locked=True,
        )


class ResearchSubject(StrictModel):
    """A research entity, which may be supported by more than one policy document."""

    subject_id: StrictStr = Field(min_length=1)
    label: StrictStr = Field(min_length=1)
    kind: Literal["region", "organization", "program", "policy_topic", "other"] = "other"
    parent_subject_id: StrictStr | None = None
    document_ids: list[StrictStr] = Field(default_factory=list)


class PolicyDocument(StrictModel):
    """A catalog or KB document identity; candidate IDs are not trusted evidence."""

    planid: StrictStr = Field(min_length=1)
    document_id: StrictStr | None = None
    document_version_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    source_hash: StrictStr | None = None
    title: StrictStr = Field(min_length=1)
    area: StrictStr | None = None
    fyp: StrictStr | None = None
    admin_level: StrictStr | None = None
    plantype_label: StrictStr | None = None
    source: Literal["policy_catalog", "kb"] | None = None
    source_verified: StrictBool = False
    subject_ids: list[StrictStr] = Field(default_factory=list)


class ResearchStep(StrictModel):
    step_id: StrictStr = Field(min_length=1)
    description: StrictStr = Field(min_length=1)
    capability: Capability
    input_refs: list[StrictStr] = Field(default_factory=list)
    depends_on: list[StrictStr] = Field(default_factory=list)
    status: Literal["pending", "running", "completed", "partial", "failed", "skipped"] = "pending"


class ResearchCoverage(StrictModel):
    required_subject_ids: list[StrictStr] = Field(default_factory=list)
    covered_subject_ids: list[StrictStr] = Field(default_factory=list)
    subject_gaps: list[StrictStr] = Field(default_factory=list)
    coverage_limited_subjects: list[StrictStr] = Field(default_factory=list)
    discovered_planids: list[StrictStr] = Field(default_factory=list)
    read_planids: list[StrictStr] = Field(default_factory=list)
    required_dimensions: list[StrictStr] = Field(default_factory=list)
    supported_dimensions: list[StrictStr] = Field(default_factory=list)
    unsupported_dimensions: list[StrictStr] = Field(default_factory=list)
    verified_claim_count: StrictInt = Field(default=0, ge=0)
    identity_verified_planids: list[StrictStr] = Field(default_factory=list)
    identity_gaps: list[StrictStr] = Field(default_factory=list)


class ResearchPlan(StrictModel):
    """Executable plan data. It contains proposed steps, never completion authority."""

    task_type: ResearchTaskType
    research_goal: StrictStr = Field(min_length=1)
    scope: ResearchScope = Field(default_factory=ResearchScope)
    subjects: list[ResearchSubject] = Field(default_factory=list)
    requested_subjects: list[StrictStr] = Field(default_factory=list)
    documents: list[PolicyDocument] = Field(default_factory=list)
    dimensions: list[StrictStr] = Field(default_factory=list)
    queries: list[StrictStr] = Field(default_factory=list)
    evaluation_criteria: list[StrictStr] = Field(default_factory=list)
    steps: list[ResearchStep] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    coverage: ResearchCoverage = Field(default_factory=ResearchCoverage)
    constraints: list[StrictStr] = Field(default_factory=list)
    revision: StrictInt = Field(default=0, ge=0)
    status: Literal["draft", "ready", "executing", "completed", "partial", "rejected"] = "draft"

    @model_validator(mode="after")
    def validate_references(self):
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("capabilities must be unique")
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("dimensions must be unique")
        if len(self.requested_subjects) != len(set(self.requested_subjects)):
            raise ValueError("requested_subjects must be unique")
        subject_ids = [subject.subject_id for subject in self.subjects]
        if len(subject_ids) != len(set(subject_ids)):
            raise ValueError("subject_id must be unique")
        planids = [document.planid for document in self.documents]
        if len(planids) != len(set(planids)):
            raise ValueError("planid must be unique within a plan")
        if set(subject_ids) & set(planids):
            raise ValueError("research subjects and policy documents must use separate identities")
        known_documents = set(planids)
        for subject in self.subjects:
            if subject.parent_subject_id and subject.parent_subject_id not in set(subject_ids):
                raise ValueError("parent_subject_id must reference a plan subject")
            if not set(subject.document_ids) <= known_documents:
                raise ValueError("subject document_ids must reference plan documents")
        for document in self.documents:
            if not set(document.subject_ids) <= set(subject_ids):
                raise ValueError("document subject_ids must reference plan subjects")
            if document.source_verified and not (
                    document.document_id and document.generation_id and document.source):
                raise ValueError("verified documents require document_id, generation_id, and source")
        if not set(self.coverage.required_subject_ids) <= set(subject_ids):
            raise ValueError("required coverage subjects must reference plan subjects")
        if not set(self.coverage.covered_subject_ids) <= set(subject_ids):
            raise ValueError("covered subjects must reference plan subjects")
        if (not set(self.coverage.subject_gaps) <= set(self.requested_subjects)
                or not set(self.coverage.coverage_limited_subjects) <= set(self.requested_subjects)
                or set(self.coverage.subject_gaps) & set(self.coverage.coverage_limited_subjects)
                or (set(self.coverage.subject_gaps) | set(self.coverage.coverage_limited_subjects))
                & {subject.label for subject in self.subjects}):
            raise ValueError("subject gaps and coverage limits must name distinct unresolved requested subjects")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id must be unique")
        if any(not set(step.depends_on) <= set(step_ids) for step in self.steps):
            raise ValueError("step dependencies must reference plan steps")
        capability_set = set(self.capabilities)
        if any(step.capability not in capability_set for step in self.steps):
            raise ValueError("step capability must be enabled by the plan")
        execution = next((step for step in self.steps if step.step_id == "execute"), None)
        if execution:
            subject_refs = {ref.removeprefix("subject:") for ref in execution.input_refs
                            if ref.startswith("subject:")}
            document_refs = {ref.removeprefix("document:") for ref in execution.input_refs
                             if ref.startswith("document:")}
            if len(execution.input_refs) != len(subject_refs) + len(document_refs):
                raise ValueError("execution inputs must use known subject: or document: references")
            if not subject_refs <= set(self.requested_subjects):
                raise ValueError("execution subject references must come from requested_subjects")
            if not document_refs <= known_documents:
                raise ValueError("execution document references must reference plan documents")
            if document_refs and document_refs != set(self.scope.target_planids):
                raise ValueError("execution document references must match the plan target scope")
        dependencies = {step.step_id: set(step.depends_on) for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("research plan steps must be acyclic")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in dependencies:
            visit(step_id)
        return self

    @classmethod
    def for_contract(cls, contract: TaskContract) -> "ResearchPlan":
        capability: Capability = {
            "qa": "qa", "extract": "extract", "organize": "organize",
            "compare": "compare", "compare_plans": "compare_plans",
        }[contract.task_type]
        required = contract.completion.required_dimensions
        return cls(
            task_type=contract.task_type,
            research_goal=contract.research_goal,
            scope=contract.scope,
            dimensions=list(required),
            capabilities=["policy_catalog", "kb_search", "kb_read", capability, "evidence_verify"],
            coverage=ResearchCoverage(
                required_subject_ids=[], required_dimensions=list(required)),
            constraints=list(contract.explicit_constraints),
        )


class ResearchEvaluation(StrictModel):
    status: Literal["completed", "partial", "insufficient", "failed", "limit_reached"]
    reason: StrictStr
    required_units: list[StrictStr] = Field(default_factory=list)
    satisfied_units: list[StrictStr] = Field(default_factory=list)
    gaps: list[StrictStr] = Field(default_factory=list)
    verified_evidence_ids: list[StrictStr] = Field(default_factory=list)
