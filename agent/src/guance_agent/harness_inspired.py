"""Opt-in Hyperresearch-inspired research mechanisms.

Borrows design ideas, not Hyperresearch implementation code. Stores only a
redacted run manifest: never a raw user prompt, policy body or model output.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from .research_requirements import parse_requirements

_EXHAUSTIVE = re.compile(
    r"所有|全部|全量|完整列出|一共(?:有)?多少|准确篇数|穷举|every\s+policy|all\s+polic", re.I
)
_SPLIT = re.compile(r"[；;。]|\s*(?:并且|同时还要|此外还要)\s*")


class HyperMechanisms:
    """Atomic constraints, pre-draft gap review, critique, patch and workspace."""

    def __init__(self, run_id: str, question: str, workspace_dir: Path):
        self.run_id = re.sub(r"[^a-zA-Z0-9_-]", "", run_id)[:80] or uuid.uuid4().hex
        self.question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
        self.exhaustive_requested = bool(_EXHAUSTIVE.search(question))
        self.workspace_dir = Path(workspace_dir)
        self.events: list[dict[str, Any]] = []
        self.preflight_count = 0
        self.patch_count = 0
        self.last_gaps: list[str] = []
        self.write_failed = False
        parsed = parse_requirements(question)
        items = [
            str(item.get("label") or "").strip()
            for item in parsed
            if isinstance(item, dict) and item.get("actionable", True)
        ]
        for clause in _SPLIT.split(question):
            clause = clause.strip(" ，、")
            if 6 <= len(clause) <= 160:
                items.append(clause)
        self.atomic_items = list(dict.fromkeys(item for item in items if item))[:24]

    def guidance(self) -> str:
        extra = (
            "本题要求全部/所有；Top-K搜索不能证明全库穷举，只能报告已核实候选和覆盖边界。"
            if self.exhaustive_requested else ""
        )
        return (
            "\n【研究机制实验】原始用户要求不可删减。提交报告前检查每个要求的已读证据，"
            "缺口优先补证。按块核验，审核后局部修复，不能删掉其他合格块。"
            "必须提供证据支持的实质研究结论，不能用引文列表代替答案。"
            + extra
        )

    def corpus_gaps(self, state: Any) -> list[str]:
        linked = {rid for group in state.claim_requirement_ids for rid in group}
        return [item.id for item in state.requirements if item.id not in linked]

    def pre_draft_critic(self, state: Any, *, finish_if_partial: bool) -> dict[str, Any] | None:
        gaps = self.corpus_gaps(state)
        self.last_gaps = gaps
        self.preflight_count += 1
        self.snapshot("pre_draft_corpus_review", state)
        if (self.preflight_count == 1 and gaps and not finish_if_partial
                and state.remaining()["search_read"] >= 2):
            return {"gaps": gaps[:16]}
        return None

    def adversarial_critic(self, state: Any, verdict: dict[str, Any]) -> dict[str, Any]:
        """Independent coverage gate after the original semantic model review."""
        output = dict(verdict)
        gaps = list(output.get("gaps") or [])
        approved = set(output.get("approved_block_ids") or [])
        blocks = [block for block in state.draft_blocks if block.id in approved]
        covered = set(output.get("covered_requirement_ids") or [])
        required = {item.id for item in state.requirements}
        if required - covered:
            gaps.extend(sorted(required - covered))
            output["passed"] = False
        if state.needs_analysis and not any(block.kind == "analysis" for block in blocks):
            gaps.append("analysis_block_not_approved")
            output["passed"] = False
        if self.exhaustive_requested:
            gaps.append("exhaustive_catalog_coverage_unverified")
            output["passed"] = False
        output["gaps"] = list(dict.fromkeys(gaps))
        self.last_gaps = output["gaps"]
        self.snapshot("adversarial_review", state)
        return output

    def patch_with_approved(self, state: Any, submitted: list[Any],
                            draft_support: dict[str, list[int]], lookup: Any) -> list[Any]:
        """Carry forward previously approved blocks; re-review the combined draft."""
        seen = {block.id for block in submitted}
        required = {item.id for item in state.requirements}
        valid_facts = {fact_id for group in state.claim_fact_ids for fact_id in group}
        kept = 0
        for block in state.approved_blocks:
            if (block.id in seen or not set(block.requirement_ids) <= required
                    or not set(block.evidence_ids) <= valid_facts):
                continue
            if not lookup(state, block):
                continue
            if any(not lookup(state, block, rid) for rid in block.requirement_ids):
                continue
            submitted.append(block)
            seen.add(block.id)
            kept += 1
            for rid in block.requirement_ids:
                draft_support.setdefault(rid, []).extend(lookup(state, block, rid))
        self.patch_count += kept
        self.snapshot("surgical_patch", state)
        return submitted

    def summary(self, state: Any) -> dict[str, Any]:
        return {
            "schema_version": "hyper-inspired/1",
            "enabled": True,
            "question_hash": self.question_hash,
            "exhaustive_requested": self.exhaustive_requested,
            "atomic_item_count": len(self.atomic_items),
            "requirement_count": len(state.requirements),
            "corpus_review_count": self.preflight_count,
            "patched_approved_block_count": self.patch_count,
            "evidence_claim_count": len(state.claims),
            "approved_block_count": len(state.approved_blocks),
            "gap_codes_or_ids": self.last_gaps[:32],
            "trace_written": not self.write_failed,
        }

    def snapshot(self, stage: str, state: Any) -> None:
        event = {
            "stage": stage,
            "requirements": len(state.requirements),
            "evidence_claims": len(state.claims),
            "approved_blocks": len(state.approved_blocks),
            "search_read_calls": state.io_calls,
            "model_calls": state.model_calls,
            "remaining_gap_count": len(self.last_gaps),
        }
        self.events.append(event)
        payload = {**self.summary(state), "events": self.events[-80:]}
        path = self.workspace_dir / (self.run_id + ".json")
        temp = self.workspace_dir / (self.run_id + "." + uuid.uuid4().hex + ".tmp")
        try:
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            with temp.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            os.replace(temp, path)
        except OSError:
            self.write_failed = True
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
