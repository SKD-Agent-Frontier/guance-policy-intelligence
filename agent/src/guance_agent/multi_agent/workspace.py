"""Run-scoped ledger and privacy-minimal diagnostic snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from .contracts import Finding, WorkerOutcome


class EvidenceLedger:
    """Import only already-verified worker claim/block bindings.

    Worker-produced text is not trusted unless the originating SDK has verified
    citations and its approved block contains references to surviving claims.
    """

    def __init__(self):
        self.claims = []
        self.refs = []
        self.findings: list[Finding] = []
        self._refs_by_chunk: dict[str, tuple] = {}
        self._claim_keys: dict[tuple, int] = {}
        self.rejected_blocks = 0
        self.conflicts = 0

    @staticmethod
    def _identity(ref) -> tuple:
        return (ref.planid, ref.document_id, ref.document_version_id, ref.generation_id)

    def ingest(self, outcome: WorkerOutcome) -> int:
        result = outcome.result
        if (result is None or result.status != "completed"
                or result.evidence_status != "verified"
                or result.meta.get("verification_incomplete")
                or not isinstance(result.structured, dict)):
            return 0
        blocks = result.structured.get("blocks")
        if not isinstance(blocks, list):
            return 0
        refs = {ref.chunk_id: ref for ref in result.evidence_refs}
        if len(refs) != len(result.evidence_refs):
            # Duplicate chunk IDs may hide competing document generations.
            self.conflicts += 1
            return 0
        local_indexes = {}
        new_count = 0
        for block in blocks:
            if not isinstance(block, dict) or block.get("kind") not in {"fact", "analysis"}:
                self.rejected_blocks += 1
                continue
            old_ids = block.get("supporting_claim_indexes")
            if not isinstance(old_ids, list) or not old_ids or any(
                type(x) is not int or x < 0 or x >= len(result.claims) for x in old_ids
            ):
                self.rejected_blocks += 1
                continue
            source_claims = [result.claims[x] for x in old_ids]
            chunks = {chunk for claim in source_claims for chunk in claim.chunk_ids}
            if any(not refs.get(chunk) or refs[chunk].source != "kb_read"
                   for chunk in chunks):
                self.rejected_blocks += 1
                continue
            if any(not claim.quotes or not claim.chunk_ids for claim in source_claims):
                self.rejected_blocks += 1
                continue
            if any(chunk in self._refs_by_chunk
                   and self._refs_by_chunk[chunk] != self._identity(refs[chunk])
                   for chunk in chunks):
                self.conflicts += 1
                self.rejected_blocks += 1
                continue
            title = (block.get("title_path") or ["研究发现"])[0]
            text = block.get("text")
            if not isinstance(title, str) or not title.strip():
                self.rejected_blocks += 1
                continue
            if not isinstance(text, str) or not text.strip():
                self.rejected_blocks += 1
                continue
            indexes = []
            for old in old_ids:
                if old not in local_indexes:
                    claim = result.claims[old]
                    # Worker fact IDs are local; their raw values may collide.
                    key = (
                        claim.text, tuple(claim.chunk_ids), tuple(claim.quotes),
                        claim.fact_status, claim.content_period, claim.time_anchor,
                    )
                    if key not in self._claim_keys:
                        self._claim_keys[key] = len(self.claims)
                        merged = claim.model_copy(deep=True)
                        merged.fact_ids = [f"G{len(self.claims) + 1}"]
                        self.claims.append(merged)
                    local_indexes[old] = self._claim_keys[key]
                indexes.append(local_indexes[old])
            for chunk in chunks:
                if chunk not in self._refs_by_chunk:
                    self._refs_by_chunk[chunk] = self._identity(refs[chunk])
                    self.refs.append(refs[chunk].model_copy(deep=True))
            finding = Finding(
                id=f"{outcome.assignment.id}/B{len(self.findings) + 1}",
                worker_id=outcome.assignment.id,
                kind=block["kind"],
                title=title.strip()[:80],
                text=text.strip()[:1200],
                claim_indexes=tuple(dict.fromkeys(indexes)),
                requirement_ids=outcome.assignment.requirement_ids,
            )
            self.findings.append(finding)
            new_count += 1
        return new_count

    def facts(self) -> list[dict]:
        refs = {ref.chunk_id: ref for ref in self.refs}
        return [
            {
                "id": f"F{i + 1}", "index": i, "text": claim.text,
                "quotes": list(claim.quotes),
                "chunk_ids": list(claim.chunk_ids),
                "planids": list(dict.fromkeys(
                    refs[chunk].planid for chunk in claim.chunk_ids if chunk in refs
                )),
            }
            for i, claim in enumerate(self.claims)
        ]

    def finding_inputs(self) -> list[dict]:
        return [
            {
                "id": block.id, "kind": block.kind, "text": block.text,
                "fact_ids": [f"F{i + 1}" for i in block.claim_indexes],
            }
            for block in self.findings
        ]


class Workspace:
    """Durable counters and stage transitions; never saves original prompt or policy text."""

    def __init__(self, run_id: str, root: Path, question: str):
        clean = "".join(ch for ch in run_id if ch.isalnum() or ch in "_-")[:80]
        self.path = Path(root) / ((clean or uuid.uuid4().hex) + ".json")
        self.question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
        self.events: list[dict] = []
        self.failed_write = False

    def record(self, stage: str, **counts) -> None:
        event = {"stage": stage}
        for k, v in counts.items():
            # No arbitrary event payload; accept only safe counters and fixed codes.
            if isinstance(v, bool) or type(v) is int or type(v) is float:
                event[k] = v
            elif k in {"worker", "status", "reason"} and isinstance(v, str):
                event[k] = v[:64]
        self.events.append(event)
        payload = {
            "schema_version": "multi-agent-trace/1",
            "question_sha256": self.question_hash,
            "events": self.events[-80:],
        }
        tmp = self.path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except OSError:
            self.failed_write = True
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
