"""Bounded multi-agent execution with existing evidence gates and explicit failure stages.

Flow: Lead plan -> independent SDK/HYPER workers -> evidence ledger -> Lead
synthesis -> independent Critic -> trusted structured answer.
No unapproved raw worker or model text is ever promoted to the final answer.
"""
from __future__ import annotations

import asyncio
import re
import time

from ..context import RunContext
from ..kb import get_kb
from ..output import ERRORS, build_result
from ..qa_context import build_trusted_turn
from ..routing import decide
from ..schemas import RunError, RunResult
from . import critic, lead, worker
from .contracts import WorkerOutcome
from .workspace import EvidenceLedger, Workspace

_EXHAUSTIVE = re.compile(r"所有|全部|全量|完整清单|准确篇数|穷举")
_FOLLOWUP = re.compile(r"上述|上面|刚才|上一轮|前面几轮|此前讨论|你提到|这篇|这些政策")


def _finish_without_evidence(task, rt, message, workspace, *, stage):
    result = RunResult(
        run_id=task.run_id, session_id=task.session_id, status="completed",
        evidence_status="none", answer=message, claims=[], evidence_refs=[],
        dropped_chunk_ids=[], error=None,
        meta={
            **rt.meta, "research_core": {"status": "partial", "stop_reason": stage},
            "research_completion": {"status": "partial"},
            "research_delivery": {"status": "partial"},
            "research_quality": {"status": "partial"},
            "multi_agent": {
                "status": "partial", "stop_reason": stage,
                "trace_available": not workspace.failed_write,
                "workers": [], "covered_requirement_ids": [],
                "missing_requirement_ids": [],
            },
        },
        structured={"schema_version": "qa-report/1", "blocks": [],
                    "coverage": {"covered": [], "missing": [], "status": "partial"}},
    )
    workspace.record("finished", reason=stage, facts=0)
    rt.finish(result, None)
    return result


def _worker_summary(outcome: WorkerOutcome, count: int) -> dict:
    result = outcome.result
    core = result.meta.get("research_core") if result is not None else None
    return {
        "worker_id": outcome.assignment.id,
        "status": outcome.status,
        "research_status": (core or {}).get("status"),
        "elapsed_seconds": outcome.elapsed_seconds,
        "model_calls": outcome.model_calls,
        "accepted_blocks": count,
        "error_type": outcome.error_type,
        "requirement_ids": list(outcome.assignment.requirement_ids),
    }


def _assemble(task, rt, ledger, plan, outcomes, accepted, critic_review, workspace,
              *, deadline: float) -> RunResult:
    facts = ledger.facts()
    by_id = {fact["id"]: fact for fact in facts}
    eligible_blocks = []
    approved = set(critic_review.approved_ids)
    for block in accepted:
        if block.id not in approved:
            continue
        indexes = tuple(by_id[fid]["index"] for fid in block.fact_ids if fid in by_id)
        if len(indexes) != len(block.fact_ids) or not indexes:
            continue
        eligible_blocks.append({
            "id": block.id, "title_path": [block.title],
            "dimension": "研究综合", "kind": "analysis", "text": block.text,
            "supporting_claim_indexes": list(indexes),
            "supporting_chunk_ids": list(dict.fromkeys(
                chunk for idx in indexes for chunk in ledger.claims[idx].chunk_ids
            )),
            "requirement_ids": list(dict.fromkeys(
                rid for finding in ledger.findings
                if any(idx in finding.claim_indexes for idx in indexes)
                for rid in finding.requirement_ids
            )),
        })
    seen_text = set()
    verified_blocks = []
    for finding in ledger.findings:
        key = (finding.text, finding.claim_indexes)
        if key in seen_text:
            continue
        seen_text.add(key)
        verified_blocks.append({
            "id": finding.id, "title_path": [finding.title],
            "dimension": "研究发现", "kind": finding.kind,
            "text": finding.text,
            "supporting_claim_indexes": list(finding.claim_indexes),
            "supporting_chunk_ids": list(dict.fromkeys(
                chunk for i in finding.claim_indexes for chunk in ledger.claims[i].chunk_ids
            )),
            "requirement_ids": list(finding.requirement_ids),
        })
    requirement_ids = {req.id for req in plan.requirements}
    covered = set()
    for outcome in outcomes:
        result = outcome.result
        # A partial worker result is useful evidence but is not full coverage.
        if (result is not None and result.evidence_status == "verified"
                and (result.meta.get("research_quality") or {}).get("status") == "complete"
                and any(f.worker_id == outcome.assignment.id for f in ledger.findings)):
            covered.update(outcome.assignment.requirement_ids)
    gaps = sorted(requirement_ids - covered)
    exhaustive = bool(_EXHAUSTIVE.search(task.question))
    full = bool(
        ledger.claims and not gaps and critic_review.performed
        and len(eligible_blocks) == len(accepted) and eligible_blocks
        and not exhaustive
    )
    blocks = (eligible_blocks + verified_blocks)[:50]
    if blocks:
        answer = "\n\n".join(
            "### " + block["title_path"][0] + "\n" + block["text"]
            for block in blocks
        )[:15000]
    else:
        answer = "本轮未取得能够通过原文与分析审核的政策研究结论，不能据此回答。"
    warnings = []
    if gaps:
        warnings.append("部分研究要求未完成核验；本次结果仅代表已经批准的研究发现。")
    if exhaustive:
        warnings.append("本轮未进行可证明的全库穷举，不得将候选政策当作全部政策或准确篇数。")
        answer += "\n\n覆盖说明：当前政策发现不是经过全库枚举核对的完整名单。"
    if not critic_review.performed:
        warnings.append("独立 Critic 未完成审核，仅交付各 Worker 已通过原有核验的内容。")
    if ledger.conflicts:
        warnings.append("部分 Worker 来源版本存在冲突，冲突证据已被排除。")
    if not full:
        if gaps:
            names = [req.label for req in plan.requirements if req.id in gaps]
            answer += "\n\n尚未充分完成的研究要求：" + "；".join(names)[:520]
        elif not exhaustive:
            answer += "\n\n完成说明：部分综合判断未通过独立审核或仍需进一步验证。"
    result = RunResult(
        run_id=task.run_id, session_id=task.session_id,
        status="completed",
        evidence_status="verified" if ledger.claims else "none",
        answer=answer,
        claims=list(ledger.claims),
        evidence_refs=list(ledger.refs),
        dropped_chunk_ids=[], error=None,
        task_type=task.task_type,
        meta={
            **rt.meta, "qa_execution_mode": "multi_agent_research",
            "research_engine": "multi",
            "research_core": {
                "status": "complete" if full else "partial",
                "model_calls": sum(o.model_calls for o in outcomes),
                "facts_reread": len(ledger.claims),
                "stop_reason": None if full else (
                    "unverified_full_corpus" if exhaustive else
                    "unfulfilled_requirements" if gaps else "critic_or_synthesis_incomplete"
                ),
            },
            "research_quality": {
                "status": "complete" if full else "partial",
                "requirement_coverage": [
                    {"id": req.id, "label": req.label,
                     "status": "covered" if req.id in covered else "missing"}
                    for req in plan.requirements
                ],
                "audited_analysis_blocks": len(eligible_blocks),
            },
            "research_completion": {"status": "complete" if full else "partial"},
            "research_delivery": {"status": "complete" if full else "partial"},
            "multi_agent": {
                "schema_version": "multi-agent/1", "status": "complete" if full else "partial",
                "planner": plan.source,
                "workers": [_worker_summary(o, sum(
                    finding.worker_id == o.assignment.id for finding in ledger.findings
                )) for o in outcomes],
                "critic": {
                    "performed": critic_review.performed,
                    "approved_synthesis_blocks": len(eligible_blocks),
                    "gaps": list(critic_review.gaps),
                },
                "covered_requirement_ids": sorted(covered),
                "missing_requirement_ids": gaps,
                "facts": len(ledger.claims),
                "sources": len({ref.planid for ref in ledger.refs}),
                "conflicting_source_blocks_rejected": ledger.conflicts,
                "rejected_worker_blocks": ledger.rejected_blocks,
                "trace_available": not workspace.failed_write,
                "time_remaining_seconds": round(max(0.0, deadline - time.monotonic()), 2),
            },
        },
        warnings=warnings,
        structured={
            "schema_version": "qa-report/1",
            "dimensions": ["研究综合", "研究发现"],
            "blocks": blocks,
            "coverage": {
                "covered": sorted(covered), "missing": gaps,
                "status": "complete" if full else "partial",
            },
            "verification": {
                "facts": "per_worker_quote_and_number_verified",
                "blocks": "worker_semantic_reviewed_and_synthesis_critic_reviewed",
            },
        } if blocks else {
            "schema_version": "qa-report/1", "blocks": [],
            "coverage": {"covered": sorted(covered), "missing": gaps,
                         "status": "partial"},
        },
    )
    return result


async def run_multi_task(task, settings, rt, *,
                         initial_decision=None, research_discoverer=None,
                         research_runtime=None):
    """Bounded execution. Fallbacks are explicit; all children use the existing SDK verifier."""
    from ..orchestrator import run_sdk_task

    decision = initial_decision or decide(task.question)
    # The original single-agent path owns conversational ellipsis/antecedent handling.
    if (_FOLLOWUP.search(task.question) or len(task.question) > settings.max_input_chars - 400):
        result = await run_sdk_task(
            task, settings, rt, initial_decision=decision,
            research_discoverer=research_discoverer,
            research_runtime=research_runtime, experimental_hyper=True,
        )
        result.meta["multi_agent"] = {
            "status": "single_agent_fallback", "reason": "followup_or_long_question",
        }
        return result

    deadline = (
        research_runtime.deadline if research_runtime is not None
        else time.monotonic() + settings.run_timeout
    )
    workspace = Workspace(
        task.run_id, settings.run_log.parent / "multi-agent-traces", task.question
    )
    workspace.record("started", budget_seconds=settings.run_timeout)
    if not settings.llm_api_key or not settings.llm_base_url:
        workspace.record("model_unavailable")
        result = build_result(
            task, None, None, rt.meta,
            RunError(code="MODEL_UNAVAILABLE", message=ERRORS["MODEL_UNAVAILABLE"]),
        )
        result.meta["multi_agent"] = {"status": "unavailable"}
        rt.finish(result, None)
        return result

    try:
        plan = await lead.build_plan(
            task.question, task.targets, rt, deadline, max_workers=2
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        workspace.record("lead_plan_error", reason=type(exc).__name__)
        return _finish_without_evidence(
            task, rt, "研究任务规划异常，未开始执行子任务。", workspace,
            stage="lead_plan_error",
        )
    workspace.record("plan_ready", assignments=len(plan.assignments),
                     requirements=len(plan.requirements))
    ledger = EvidenceLedger()
    outcomes = []
    for index, assignment in enumerate(plan.assignments):
        remaining = deadline - time.monotonic()
        workers_left = len(plan.assignments) - index
        duration = min(65.0, max(0.0, (remaining - 44.0) / workers_left))
        if duration < 3:
            outcome = WorkerOutcome(assignment=assignment, status="budget_exhausted")
        else:
            outcome = await worker.run_worker(
                assignment, task, settings, seconds=duration,
                discoverer=research_discoverer,
            )
        outcomes.append(outcome)
        imported = ledger.ingest(outcome)
        workspace.record(
            "worker_returned", worker=assignment.id, status=outcome.status,
            model_calls=outcome.model_calls, accepted_blocks=imported,
            total_facts=len(ledger.claims),
        )
    if not ledger.claims:
        workspace.record("no_verified_facts", workers=len(outcomes))
        result = _assemble(
            task, rt, ledger, plan, outcomes, [], critic.Review(), workspace,
            deadline=deadline,
        )
        rt.finish(result, None)
        workspace.record("finished", facts=0, reason="no_verified_facts")
        return result

    summary_blocks = []
    reviews = critic.Review(gaps=("synthesis_not_attempted",))
    for revision in range(2):
        if deadline - time.monotonic() < 28:
            break
        workspace.record("lead_synthesis", revision=revision + 1,
                         facts=len(ledger.claims))
        try:
            summary_blocks = await lead.synthesize(
                task.question, ledger.facts(), ledger.finding_inputs(), rt,
                deadline, feedback="；".join(reviews.gaps),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            workspace.record("lead_synthesis_failed", reason=type(exc).__name__)
            summary_blocks = []
            break
        reviews = await critic.review(
            summary_blocks, {x["id"]: x for x in ledger.facts()}, rt, deadline
        )
        workspace.record("critic_reviewed", performed=reviews.performed,
                         approved=len(reviews.approved_ids),
                         proposed=len(summary_blocks))
        if reviews.performed and len(reviews.approved_ids) == len(summary_blocks):
            break

    result = _assemble(
        task, rt, ledger, plan, outcomes, summary_blocks, reviews, workspace,
        deadline=deadline,
    )
    # The workers have already verified all exported claims against the same
    # published KB; save only surviving provenance in the parent's context.
    try:
        parent_ctx = RunContext(
            settings, get_kb(settings), task.run_id, task.session_id,
            question=task.question, route=decision,
            scope=tuple(task.targets) if task.targets is not None else None,
        )
        for ref in ledger.refs:
            parent_ctx.evidence.register(ref)
        rt.save_trusted_turn(build_trusted_turn(task.question, parent_ctx, result))
        rt.finish(result, parent_ctx)
    except Exception as exc:
        result.meta["multi_agent"]["parent_context_error"] = type(exc).__name__
        rt.finish(result, None)
    workspace.record(
        "finished", facts=len(ledger.claims),
        completed=result.meta["research_completion"]["status"] == "complete",
    )
    result.meta["multi_agent"]["trace_available"] = not workspace.failed_write
    return result
