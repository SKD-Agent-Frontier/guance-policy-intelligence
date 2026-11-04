"""Bounded lifecycle and task-specific acceptance checks for policy research."""
from __future__ import annotations

import math
import time
from typing import Literal

from .kb import normalize_fyp
from .research_contract import (
    ResearchCoverage,
    ResearchEvaluation,
    ResearchPlan,
    ResearchScope,
    ResearchTaskType,
    TaskContract,
)
from .routing import parse_fyps
from .schemas import RunError

ResearchStatus = Literal[
    "created", "planning", "discovering", "ready", "executing", "verifying",
    "completed", "partial", "insufficient", "failed", "limit_reached",
]

_TERMINAL = frozenset({"completed", "partial", "insufficient", "failed", "limit_reached"})
_TRANSITIONS = {
    "created": {"planning", "insufficient", "failed", "limit_reached"},
    "planning": {"discovering", "ready", "partial", "insufficient", "failed", "limit_reached"},
    "discovering": {"ready", "planning", "partial", "insufficient", "failed", "limit_reached"},
    "ready": {"executing", "planning", "partial", "insufficient", "failed", "limit_reached"},
    "executing": {"verifying", "partial", "insufficient", "failed", "limit_reached"},
    "verifying": _TERMINAL,
}


class ResearchRuntimeError(ValueError):
    """Raised when a plan revision or lifecycle transition breaks the contract."""


class ResearchRuntime:
    """In-memory task state; completion is decided after the existing evidence checks."""

    def __init__(self, contract: TaskContract, plan: ResearchPlan, deadline: float,
                 max_revisions: int = 1):
        if type(max_revisions) is not int or max_revisions not in (0, 1):
            raise ValueError("max_revisions must be 0 or 1")
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise ValueError("deadline must be a finite monotonic timestamp")
        contract_copy = TaskContract.model_validate(contract.model_dump(mode="python"))
        plan_copy = ResearchPlan.model_validate(plan.model_dump(mode="python"))
        self._validate_contract_plan(contract_copy, plan_copy)
        self._contract = contract_copy.model_copy(deep=True)
        self._plan = plan_copy.model_copy(deep=True)
        self.deadline = float(deadline)
        self.max_revisions = max_revisions
        self.revision_count = 0
        self._status: ResearchStatus = "created"
        self.reason: str | None = None
        self._trusted_refs: dict[str, object] = {}
        self._verified_claim_count = 0
        self._supported_dimensions: list[str] = []
        self.started_at = time.monotonic()
        self.state_history = [{"status": "created", "reason": None, "elapsed_ms": 0}]
        self.step_events: list[dict] = []
        self.plan_versions = [self._plan_record(self._plan, "initial")]

    @property
    def contract(self) -> TaskContract:
        return self._contract.model_copy(deep=True)

    @property
    def status(self) -> ResearchStatus:
        return self._status

    @property
    def plan(self) -> ResearchPlan:
        return self._plan.model_copy(deep=True)

    @staticmethod
    def _validate_contract_plan(contract: TaskContract, plan: ResearchPlan) -> None:
        if plan.task_type != contract.task_type:
            raise ResearchRuntimeError("plan task_type does not match locked TaskContract")
        if plan.research_goal != contract.research_goal:
            raise ResearchRuntimeError("plan research_goal does not match TaskContract")
        if not ResearchRuntime._scope_within(plan.scope, contract.scope):
            raise ResearchRuntimeError("plan scope exceeds TaskContract scope")
        fixed_dimensions = set(contract.completion.required_dimensions)
        if fixed_dimensions and not fixed_dimensions <= set(plan.dimensions):
            raise ResearchRuntimeError("plan dropped a required TaskContract dimension")
        fixed_targets = set(contract.scope.target_planids)
        if fixed_targets and any(doc.planid not in fixed_targets for doc in plan.documents):
            raise ResearchRuntimeError("plan contains a document outside explicit targets")
        allowed_capabilities = {
            "qa": {"qa", "policy_catalog", "kb_search", "kb_read", "evidence_verify"},
            "extract": {"extract", "policy_catalog", "kb_search", "kb_read", "evidence_verify"},
            "organize": {"organize", "policy_catalog", "kb_search", "kb_read", "evidence_verify"},
            "compare": {"compare", "policy_catalog", "kb_search", "kb_read", "evidence_verify"},
            "compare_plans": {"compare_plans", "policy_catalog", "kb_search", "kb_read", "evidence_verify"},
        }[contract.task_type]
        capabilities = set(plan.capabilities)
        if not {"kb_search", "kb_read", "evidence_verify"} <= capabilities:
            raise ResearchRuntimeError("plan must retain kb_search, kb_read, and evidence_verify")
        if not capabilities <= allowed_capabilities:
            raise ResearchRuntimeError("plan capability is incompatible with locked task_type")
        for document in plan.documents:
            if not document.source_verified or not (
                    document.document_id and document.generation_id and document.source):
                raise ResearchRuntimeError("selected documents require verified catalog/KB identity")
            if contract.scope.periods:
                metadata_periods = {normalize_fyp(value) for value in parse_fyps(document.fyp or "")}
                title_periods = {normalize_fyp(value) for value in parse_fyps(document.title)}
                period = normalize_fyp(document.fyp)
                if period in contract.scope.periods:
                    metadata_periods.add(period)
                observed_periods = metadata_periods | title_periods
                if not observed_periods:
                    raise ResearchRuntimeError("selected document period cannot be verified")
                if not observed_periods & set(contract.scope.periods):
                    raise ResearchRuntimeError("selected document period violates TaskContract")

    @staticmethod
    def _scope_within(candidate: ResearchScope, bounds: ResearchScope) -> bool:
        for name in ("areas", "periods", "topics", "target_planids"):
            allowed = set(getattr(bounds, name))
            proposed = set(getattr(candidate, name))
            if allowed and (not proposed or not proposed <= allowed):
                return False
        return True

    def transition(self, status: ResearchStatus, reason: str | None = None) -> None:
        if self.status in _TERMINAL:
            raise ResearchRuntimeError(f"terminal status {self.status} cannot transition")
        if status not in _TRANSITIONS.get(self.status, set()):
            raise ResearchRuntimeError(f"invalid transition: {self.status} -> {status}")
        if status in {"partial", "insufficient", "failed", "limit_reached"} and not (reason and reason.strip()):
            raise ResearchRuntimeError(f"terminal status {status} requires a reason")
        self._status = status
        self.reason = reason.strip() if reason else None
        self.state_history.append({"status": status, "reason": self.reason,
                                   "elapsed_ms": round((time.monotonic() - self.started_at) * 1000)})

    def revise(self, plan: ResearchPlan, reason: str) -> ResearchPlan:
        if self.status not in {"planning", "discovering", "ready"}:
            raise ResearchRuntimeError("plan can only be revised before execution")
        if not isinstance(reason, str) or not reason.strip():
            raise ResearchRuntimeError("plan revision requires a trigger reason")
        if self.revision_count >= self.max_revisions:
            raise ResearchRuntimeError("plan revision budget exhausted")
        candidate = ResearchPlan.model_validate(plan.model_dump(mode="python"))
        self._validate_contract_plan(self._contract, candidate)
        if candidate.requested_subjects != self._plan.requested_subjects:
            raise ResearchRuntimeError("plan revision cannot change requested research subjects")
        if candidate.revision != self._plan.revision + 1:
            raise ResearchRuntimeError("plan revision must increment by exactly one")
        if not self._scope_within(candidate.scope, self._plan.scope):
            raise ResearchRuntimeError("plan revision cannot expand its previous scope")
        old_capabilities = set(self._plan.capabilities)
        if not set(candidate.capabilities) <= old_capabilities:
            raise ResearchRuntimeError("plan revision cannot add capabilities")
        fixed_targets = set(self._contract.scope.target_planids)
        if fixed_targets and {doc.planid for doc in candidate.documents} - fixed_targets:
            raise ResearchRuntimeError("plan revision cannot replace explicit target documents")
        self._plan = candidate
        self.revision_count += 1
        self.reason = reason.strip()
        self._status = "planning"
        self._trusted_refs.clear()
        self.state_history.append({"status": "planning", "reason": self.reason,
                                   "elapsed_ms": round((time.monotonic() - self.started_at) * 1000)})
        self.plan_versions.append(self._plan_record(self._plan, self.reason))
        return self.plan

    def accept_discovery(self, plan: ResearchPlan) -> ResearchPlan:
        """Accept a deterministic catalog/KB discovery result without spending a revision."""
        if self.status not in {"planning", "discovering"}:
            raise ResearchRuntimeError("initial discovery is only accepted while planning/discovering")
        candidate = ResearchPlan.model_validate(plan.model_dump(mode="python"))
        if candidate.revision != 0:
            raise ResearchRuntimeError("initial discovery must keep revision 0")
        self._validate_contract_plan(self._contract, candidate)
        self._plan = candidate
        self.plan_versions.append(self._plan_record(candidate, "trusted_discovery"))
        return self.plan

    def capture_execution_plan(self, dimensions, queries, evaluation_criteria=()) -> ResearchPlan:
        """Record the existing compare module plan; this does not call a model or revise it."""
        if self._contract.task_type not in {"compare", "compare_plans"}:
            raise ResearchRuntimeError("comparison execution plans require a compare TaskContract")
        if self.status != "executing":
            raise ResearchRuntimeError("execution plan can only be captured during execution")
        selected_dimensions = list(dict.fromkeys(
            item.strip() for item in dimensions if isinstance(item, str) and item.strip()))
        selected_queries = list(dict.fromkeys(
            item.strip() for item in queries if isinstance(item, str) and item.strip()))
        selected_criteria = list(dict.fromkeys(
            item.strip() for item in evaluation_criteria
            if isinstance(item, str) and item.strip()))
        required = self._contract.completion.required_dimensions
        if not selected_dimensions:
            raise ResearchRuntimeError("comparison execution plan requires dimensions")
        if len(selected_queries) > 4 or any(len(query) > 200 for query in selected_queries):
            raise ResearchRuntimeError("comparison queries exceed the existing bounded search contract")
        if required and selected_dimensions != list(required):
            raise ResearchRuntimeError("comparison execution plan changed explicit dimensions")
        if any(criterion not in self._contract.research_goal for criterion in selected_criteria):
            raise ResearchRuntimeError("evaluation criteria must come from the user request")
        updated = ResearchPlan.model_validate({
            **self._plan.model_dump(mode="python"),
            "dimensions": selected_dimensions,
            "queries": selected_queries,
            "evaluation_criteria": selected_criteria,
        })
        self._plan = updated.model_copy(deep=True)
        self.plan_versions.append(self._plan_record(self._plan, "existing_business_planner"))
        return self.plan

    def set_step_status(self, step_id: str, status: str, reason: str | None = None) -> ResearchPlan:
        """Record one bounded step update without allowing plan identity changes."""
        allowed = {
            "pending": {"running", "skipped"},
            "running": {"completed", "partial", "failed", "skipped"},
            "completed": set(), "partial": set(), "failed": set(), "skipped": set(),
        }
        steps = [step.model_dump(mode="python") for step in self._plan.steps]
        selected = next((step for step in steps if step["step_id"] == step_id), None)
        if selected is None:
            raise ResearchRuntimeError("unknown research step")
        if status not in allowed[selected["status"]]:
            raise ResearchRuntimeError(f"invalid step transition: {selected['status']} -> {status}")
        selected["status"] = status
        new_plan = ResearchPlan.model_validate({**self._plan.model_dump(mode="python"), "steps": steps})
        self._plan = new_plan.model_copy(deep=True)
        event = {"step_id": step_id, "status": status, "reason": reason}
        self.step_events.append(event)
        return self.plan

    @staticmethod
    def _plan_record(plan: ResearchPlan, reason: str) -> dict:
        return {"revision": plan.revision, "reason": reason,
                "scope": plan.scope.model_dump(mode="json"),
                "document_planids": [doc.planid for doc in plan.documents],
                "requested_subjects": list(plan.requested_subjects),
                "subject_gaps": list(plan.coverage.subject_gaps),
                "coverage_limited_subjects": list(plan.coverage.coverage_limited_subjects),
                "capabilities": list(plan.capabilities),
                "step_statuses": {step.step_id: step.status for step in plan.steps}}

    def observe(self, ctx) -> ResearchCoverage:
        """Refresh coverage only from EvidenceRegistry entries accepted by the harness."""
        refs: dict[str, object] = {}
        registry = getattr(ctx, "evidence", None)
        if registry is not None:
            for evidence_id in registry.ids():
                ref = registry.get(evidence_id)
                if ref is not None:
                    refs[evidence_id] = ref
        self._trusted_refs = refs
        planids = list(dict.fromkeys(ref.planid for ref in refs.values()))
        read_planids = list(dict.fromkeys(ref.planid for ref in refs.values()
                                          if getattr(ref, "source", None) == "kb_read"))
        identity_verified = []
        identity_gaps = []
        for document in self._plan.documents:
            source_refs = [ref for ref in refs.values() if ref.planid == document.planid]
            if source_refs and all(self._document_ref_matches(ref) for ref in source_refs):
                identity_verified.append(document.planid)
            else:
                identity_gaps.append(document.planid)
        return ResearchCoverage(
            required_subject_ids=list(self._plan.coverage.required_subject_ids),
            covered_subject_ids=[subject.subject_id for subject in self._plan.subjects
                                 if any(doc.planid in read_planids for doc in self._plan.documents
                                        if doc.planid in subject.document_ids)],
            discovered_planids=planids,
            read_planids=read_planids,
            required_dimensions=list(self._contract.completion.required_dimensions),
            supported_dimensions=list(self._supported_dimensions),
            unsupported_dimensions=[dimension for dimension in self._contract.completion.required_dimensions
                                    if dimension not in self._supported_dimensions],
            verified_claim_count=self._verified_claim_count,
            identity_verified_planids=identity_verified,
            identity_gaps=identity_gaps,
        )

    def evaluate(self, result, ctx) -> ResearchEvaluation:
        self.observe(ctx)
        refs = self._trusted_refs
        claims = list(getattr(result, "claims", None) or [])
        trusted_claims = [claim for claim in claims if claim.chunk_ids
                          and set(claim.chunk_ids) <= set(refs)
                          and all(self._document_ref_matches(refs[cid]) for cid in claim.chunk_ids)]
        refs_by_id = {ref.chunk_id: ref for ref in getattr(result, "evidence_refs", None) or []}
        result_ids = {chunk_id for chunk_id, ref in refs_by_id.items()
                      if chunk_id in refs and self._same_source(refs[chunk_id], ref)}
        trusted_claims = [claim for claim in trusted_claims if set(claim.chunk_ids) <= result_ids]
        used_ids = list(dict.fromkeys(cid for claim in trusted_claims for cid in claim.chunk_ids))
        self._verified_claim_count = len(trusted_claims)
        answer = getattr(result, "answer", "")
        answer_ok = isinstance(answer, str) and bool(answer.strip())
        evidence_verified = (getattr(result, "evidence_status", "none") == "verified"
                             and bool(trusted_claims) and bool(used_ids))
        required: list[str] = []
        satisfied: list[str] = []
        gaps: list[str] = []

        result_status = getattr(result, "status", None)
        error = getattr(result, "error", None)
        if result_status == "limit_reached":
            return self._evaluation("limit_reached", "执行预算或时限已达到", required, satisfied,
                                    [getattr(error, "code", None) or "执行受限"], used_ids)
        if time.monotonic() >= self.deadline:
            return self._evaluation("limit_reached", "研究任务总时限已达到", required, satisfied,
                                    ["deadline_exceeded", *gaps], used_ids)
        if result_status == "failed" or error is not None:
            return self._evaluation("failed", "任务执行失败", required, satisfied,
                                    [getattr(error, "code", None) or "任务执行失败"], used_ids)

        if self._contract.completion.require_answer:
            required.append("answer")
            if answer_ok:
                satisfied.append("answer")
            else:
                gaps.append("answer_missing")
        if self._contract.completion.require_verified_claims:
            required.append("verified_claims")
            if evidence_verified:
                satisfied.append("verified_claims")
            elif trusted_claims:
                gaps.append("claims_not_fully_verified")
            else:
                gaps.append("verified_claims_missing")

        structured = getattr(result, "structured", None)
        structured = structured if isinstance(structured, dict) else {}
        task_type: ResearchTaskType = self._contract.task_type
        if task_type == "qa":
            # Model-declared dimensions cannot replace original user requirements.
            quality = (getattr(result, "meta", {}) or {}).get("research_quality")
            quality = quality if isinstance(quality, dict) else {}
            quality_units = quality.get("requirement_coverage")
            if isinstance(quality_units, list):
                trusted_indexes = {
                    index for index, claim in enumerate(claims)
                    if claim in trusted_claims
                }
                mapped_dimensions = set()
                for item in quality_units:
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    unit = "requirement:" + str(item["id"])
                    required.append(unit)
                    if item.get("kind") == "dimension":
                        mapped_dimensions.add(str(item.get("label") or ""))
                    support_indexes = item.get("evidence_claim_indexes") or []
                    support_valid = (not support_indexes
                                     or any(index in trusted_indexes
                                            for index in support_indexes
                                            if type(index) is int))
                    if (item.get("status") == "covered" and evidence_verified
                            and support_valid):
                        satisfied.append(unit)
                    else:
                        gaps.append(f"research_requirement_uncovered:{item['id']}")

                # Preserve explicit task dimensions when the parser omitted a unit.
                required_dimensions = list(dict.fromkeys([
                    *self._contract.completion.required_dimensions,
                    *(quality.get("required_dimensions") or []),
                ]))
                covered_dimensions = {
                    str(item.get("label")) for item in quality_units
                    if isinstance(item, dict) and item.get("kind") == "dimension"
                    and item.get("status") == "covered"
                }
                for dimension in required_dimensions:
                    if dimension in mapped_dimensions:
                        continue
                    unit = f"dimension:{dimension}"
                    required.append(unit)
                    if dimension in covered_dimensions and evidence_verified:
                        satisfied.append(unit)
                    else:
                        gaps.append(f"research_dimension_uncovered:{dimension}")
            else:
                # Legacy results without itemized coverage cannot use a Claim's
                # model-supplied dimension label as completion authority.
                required_dimensions = list(dict.fromkeys([
                    *self._contract.completion.required_dimensions,
                    *(quality.get("required_dimensions") or []),
                ]))
                covered = set(quality.get("covered_dimensions") or [])
                for dimension in required_dimensions:
                    unit = f"dimension:{dimension}"
                    required.append(unit)
                    if evidence_verified and dimension in covered:
                        satisfied.append(unit)
                    else:
                        gaps.append(f"research_dimension_uncovered:{dimension}")
            if quality:
                if quality.get("status") in {"partial", "insufficient"}:
                    gaps.append("research_quality_incomplete")
                for field, unit in (("needs_report", "audited_report"),
                                    ("needs_analysis", "audited_analysis")):
                    if quality.get(field):
                        required.append(unit)
                        count_field = ("audited_report_blocks" if field == "needs_report"
                                       else "audited_analysis_blocks")
                        if quality.get(count_field, 0) > 0:
                            satisfied.append(unit)
                        else:
                            gaps.append(f"{unit}_missing")
        elif task_type == "extract":
            requested = list(self._contract.completion.required_dimensions)
            supported = {claim.dimension for claim in trusted_claims if claim.dimension}
            for dimension in requested:
                unit = f"dimension:{dimension}"
                required.append(unit)
                if dimension in supported:
                    satisfied.append(unit)
                else:
                    gaps.append(f"dimension_not_evidence_backed:{dimension}")
        elif task_type == "organize":
            minimum = self._contract.completion.min_summarized_documents
            coverage_data = structured.get("coverage")
            policies = structured.get("policies")
            summarized = ({p.get("planid") for p in policies
                           if isinstance(p, dict) and p.get("read_status") == "summarized"}
                          if isinstance(policies, list) else set())
            supported_planids = {refs_by_id[cid].planid for cid in used_ids if cid in refs_by_id}
            summarized &= supported_planids
            reported = (coverage_data.get("documents_summarized")
                        if isinstance(coverage_data, dict) else None)
            count = min(len(summarized), reported) if type(reported) is int else len(summarized)
            required.append(f"summarized_documents>={minimum}")
            if count >= minimum:
                satisfied.append(f"summarized_documents>={minimum}")
            else:
                gaps.append(f"summarized_documents:{count}/{minimum}")
        elif task_type in {"compare", "compare_plans"}:
            dimensions = list(self._contract.completion.required_dimensions)
            if not dimensions:
                dimensions = list(self._plan.dimensions)
            for label in self._plan.coverage.subject_gaps:
                gaps.append(f"requested_subject_uncovered:{label}")
            for label in self._plan.coverage.coverage_limited_subjects:
                gaps.append(f"requested_subject_coverage_limited:{label}")
            matrix = structured.get("matrix")
            if not dimensions:
                gaps.append("comparison_dimensions_missing")
            if isinstance(matrix, list):
                matrix_dimensions = [row.get("dimension") for row in matrix if isinstance(row, dict)]
                if (len(matrix_dimensions) != len(set(matrix_dimensions))
                        or set(matrix_dimensions) != set(dimensions)):
                    gaps.append("comparison_matrix_dimensions_mismatch")
            valid_cells: dict[str, dict] = {}
            invalid_cell_ids: set[str] = set()
            if isinstance(matrix, list):
                for row in matrix:
                    if not isinstance(row, dict) or not isinstance(row.get("cells"), list):
                        continue
                    for cell in row["cells"]:
                        if isinstance(cell, dict) and self._valid_cell(cell, refs, result_ids, used_ids):
                            cell_id = cell["cell_id"]
                            if cell_id in valid_cells:
                                valid_cells.pop(cell_id, None)
                                invalid_cell_ids.add(cell_id)
                            elif cell_id not in invalid_cell_ids:
                                valid_cells[cell_id] = cell
            document_subjects = {
                document.planid: set(document.subject_ids)
                for document in self._plan.documents
            }
            for subject in self._plan.subjects:
                for planid in subject.document_ids:
                    document_subjects.setdefault(planid, set()).add(subject.subject_id)
            targets = set(self._contract.scope.target_planids) or {
                planid for planid, subject_ids in document_subjects.items() if subject_ids
            }
            if not self._plan.subjects:
                gaps.append("comparison_subjects_missing")
            supported_planids = {cell.get("planid") for cell in valid_cells.values()}
            subject_ids = {subject.subject_id for subject in self._plan.subjects
                           if set(subject.document_ids) & supported_planids}
            if len(subject_ids) < self._contract.completion.min_subjects:
                gaps.append(f"subjects_below_minimum:{len(subject_ids)}/"
                            f"{self._contract.completion.min_subjects}")
            minimum_documents = self._contract.completion.min_documents
            if len(supported_planids) < minimum_documents:
                gaps.append(f"documents_below_minimum:{len(supported_planids)}/"
                            f"{minimum_documents}")
            if not targets:
                gaps.append("comparison_documents_missing")
            for dimension in dimensions:
                unit = f"comparison:{dimension}"
                required.append(unit)
                row = next((item for item in matrix if isinstance(item, dict)
                            and item.get("dimension") == dimension), None) if isinstance(matrix, list) else None
                cells = row.get("cells") if isinstance(row, dict) else None
                cell_planids = {cell.get("planid") for cell in cells
                                if isinstance(cell, dict) and cell.get("cell_id") in valid_cells
                                } if isinstance(cells, list) else set()
                difference = row.get("difference") if isinstance(row, dict) else None
                row_cell_ids = {cell.get("cell_id") for cell in cells if isinstance(cell, dict)} \
                    if isinstance(cells, list) else set()
                supported_cells = set(difference.get("supporting_cells") or []) \
                    if isinstance(difference, dict) else set()
                comparable = row.get("comparability") if isinstance(row, dict) else None
                complete_row = (row is not None and isinstance(cells, list) and bool(targets)
                                and targets <= cell_planids and isinstance(difference, dict)
                                and difference.get("status") == "supported"
                                and supported_cells == row_cell_ids
                                and row_cell_ids <= set(valid_cells)
                                and isinstance(comparable, dict)
                                and comparable.get("comparable") is True)
                if complete_row:
                    satisfied.append(unit)
                else:
                    gaps.append(f"comparison_dimension_incomplete:{dimension}")
            if task_type == "compare_plans":
                schemes = structured.get("scheme_analysis")
                schemes_by_id = {item.get("planid"): item for item in schemes
                                 if isinstance(item, dict)} if isinstance(schemes, list) else {}
                expected_ids = targets or set(schemes_by_id)
                for planid in sorted(expected_ids):
                    scheme = schemes_by_id.get(planid, {})
                    for field in self._contract.completion.required_scheme_fields:
                        unit = f"scheme:{planid}:{field}"
                        required.append(unit)
                        entries = scheme.get(field)
                        if isinstance(entries, list) and any(
                                isinstance(entry, dict)
                                and self._valid_analysis_entry(entry, planid, field, valid_cells)
                                for entry in entries):
                            satisfied.append(unit)
                        else:
                            gaps.append(f"scheme_analysis_missing:{planid}:{field}")

        if gaps:
            self._supported_dimensions = [unit.split(":", 1)[1] for unit in satisfied
                                          if unit.startswith("comparison:") or unit.startswith("dimension:")]
            status = "partial" if satisfied and (trusted_claims or satisfied != ["answer"]) else "insufficient"
            return self._evaluation(status, "未满足全部任务契约条件", required, satisfied, gaps, used_ids)
        if not evidence_verified:
            return self._evaluation("insufficient", "没有可核验的事实证据", required, satisfied,
                                    ["verified_evidence_missing"], used_ids)
        self._supported_dimensions = [unit.split(":", 1)[1] for unit in satisfied
                                      if unit.startswith("comparison:") or unit.startswith("dimension:")]
        return self._evaluation("completed", "任务契约条件与证据核验均通过", required, satisfied,
                                [], used_ids)

    @staticmethod
    def _same_source(left, right) -> bool:
        if getattr(left, "planid", None) != getattr(right, "planid", None):
            return False
        for field in ("document_id", "document_version_id", "generation_id", "source_hash"):
            a, b = getattr(left, field, None), getattr(right, field, None)
            if a is not None and b is not None and a != b:
                return False
        return True

    def _document_ref_matches(self, ref) -> bool:
        document = next((item for item in self._plan.documents if item.planid == ref.planid), None)
        if document is None:
            return True
        return (document.source_verified and ref.document_id == document.document_id
                and ref.generation_id == document.generation_id
                and (not document.document_version_id
                     or ref.document_version_id == document.document_version_id)
                and (not document.source_hash or ref.source_hash == document.source_hash))

    def _valid_cell(self, cell, registry_refs, result_ids, used_ids) -> bool:
        chunk_ids = cell.get("chunk_ids")
        planid = cell.get("planid")
        return (cell.get("status") == "found" and isinstance(cell.get("cell_id"), str)
                and isinstance(planid, str)
                and isinstance(chunk_ids, list) and bool(chunk_ids)
                and set(chunk_ids) <= set(result_ids) and set(chunk_ids) <= set(used_ids)
                and all(registry_refs[cid].planid == planid
                        and self._document_ref_matches(registry_refs[cid]) for cid in chunk_ids))

    @staticmethod
    def _valid_analysis_entry(entry, planid, field, valid_cells) -> bool:
        ids = entry.get("supporting_cells")
        if not (isinstance(ids, list) and bool(ids) and len(ids) == len(set(ids))
                and set(ids) <= set(valid_cells)):
            return False
        supporting_planids = {valid_cells[cell_id].get("planid") for cell_id in ids}
        if field == "advantages":
            return planid in supporting_planids
        if field == "limitations":
            return len(ids) == 1 and supporting_planids == {planid}
        if field == "applicability":
            return supporting_planids == {planid}
        return False

    def finish(self, result, ctx) -> ResearchEvaluation:
        """Evaluate the verified result and append only additive metadata to the public result."""
        evaluation = self.evaluate(result, ctx)
        if evaluation.status in {"partial", "insufficient"}:
            evidence_gaps = {"verified_claims_missing", "claims_not_fully_verified",
                             "verified_evidence_missing"}
            if (getattr(result, "evidence_status", None) == "verified"
                    and evidence_gaps.intersection(evaluation.gaps)):
                result.evidence_status = "insufficient"
            warning = "研究任务未满足全部交付条件；请结合 research 状态和缺口限定结论范围。"
            if hasattr(result, "warnings") and warning not in result.warnings:
                result.warnings.append(warning)
        elif evaluation.status == "limit_reached":
            result.status = "limit_reached"
            timed_out = (time.monotonic() >= self.deadline
                         or "deadline_exceeded" in evaluation.gaps)
            if timed_out:
                result.error = RunError(code="RUN_TIMEOUT", message="本次任务超时，已停止")
                result.evidence_status = "insufficient"
        if self.status not in _TERMINAL:
            if evaluation.status in {"completed", "partial"} and self.status != "verifying":
                if self.status == "executing":
                    self.transition("verifying")
                else:
                    raise ResearchRuntimeError("verified result must finish from verifying state")
            self.transition(evaluation.status, evaluation.reason)
        coverage = self.observe(ctx)
        research_meta = {
            "task_type": self._contract.task_type,
            "status": evaluation.status,
            "reason": evaluation.reason,
            "plan_revision": self._plan.revision,
            "revision_count": self.revision_count,
            "max_plan_revisions": self.max_revisions,
            "capabilities": list(self._plan.capabilities),
            "coverage": coverage.model_dump(mode="json"),
            "required_units": list(evaluation.required_units),
            "satisfied_units": list(evaluation.satisfied_units),
            "gaps": list(evaluation.gaps),
            "verified_evidence_ids": list(evaluation.verified_evidence_ids),
            "deadline_exceeded": time.monotonic() >= self.deadline,
            "state_history": list(self.state_history),
            "plan_versions": list(self.plan_versions),
            "step_events": list(self.step_events),
            "steps": [step.model_dump(mode="json") for step in self._plan.steps],
        }
        if isinstance(getattr(result, "meta", None), dict):
            result.meta["research"] = research_meta
        return evaluation

    @staticmethod
    def _evaluation(status, reason, required, satisfied, gaps, evidence_ids):
        return ResearchEvaluation(
            status=status,
            reason=reason,
            required_units=list(dict.fromkeys(required)),
            satisfied_units=list(dict.fromkeys(satisfied)),
            gaps=list(dict.fromkeys(gap for gap in gaps if isinstance(gap, str) and gap)),
            verified_evidence_ids=list(dict.fromkeys(evidence_ids)),
        )
