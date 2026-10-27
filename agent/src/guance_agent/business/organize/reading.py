"""S2: read candidate chunks with time, tool and document budgets."""

import time
from dataclasses import dataclass, field

from guance_agent.business import BusinessError
from guance_agent.context import same_source
from guance_agent.schemas import Passage

from .constants import (
    MAX_CHUNKS_PER_DOC,
    MAX_DOCS_READ,
    MAX_READS,
    MODEL_RESERVE_S,
    TOOL_RESERVE,
)
from .discovery import Discovery


@dataclass
class EvidenceWindow:
    planid: str
    chunk_id: str
    passage: Passage
    shown: str = ""


@dataclass
class PolicyReading:
    read_status: str = "not_read"
    chunks_read: int = 0
    # Preserve raw tool outcomes while read_status uses the organize/1 vocabulary.
    read_attempts: list[dict] = field(default_factory=list)


@dataclass
class Reading:
    windows: list[EvidenceWindow] = field(default_factory=list)
    policies: dict[str, PolicyReading] = field(default_factory=dict)
    limits: list[str] = field(default_factory=list)


def _rank_chunks(found: Discovery, planid: str):
    ranked = sorted((h for h in found.hits if h.planid == planid),
                    key=lambda h: h.score, reverse=True)
    unique = {h.chunk_id: h for h in reversed(ranked)}
    ranked = sorted(unique.values(), key=lambda h: h.score, reverse=True)
    selected, sections = [], set()
    for hit in ranked:
        if hit.section_path not in sections:
            selected.append(hit)
            sections.add(hit.section_path)
    selected.extend(h for h in ranked if h not in selected)
    return selected[:MAX_CHUNKS_PER_DOC]


def _registered(passage, planid, cid, ctx):
    if not isinstance(passage, Passage) or not passage.content or passage.is_index:
        return False
    ref = ctx.evidence.get(cid)
    if (ref is None or ref.planid != planid or passage.planid != planid
            or passage.chunk_id != cid or not same_source(ref, passage)):
        return False
    start, end = passage.char_start, passage.char_end
    return (start is not None and end is not None and end - start == len(passage.content)
            and any(a <= start < end <= b for a, b in ref.observed_ranges))


async def read_candidates(found: Discovery, ctx, services) -> Reading:
    reading = Reading(policies={p.planid: PolicyReading() for p in found.candidates})
    selected = found.candidates[:MAX_DOCS_READ]
    if len(found.candidates) > len(selected):
        reading.limits.append("max_docs_read")
    chunks = {p.planid: _rank_chunks(found, p.planid) for p in selected}
    cap = max(0, min(MAX_READS, ctx.settings.max_tool_calls - len(found.queries) - TOOL_RESERVE))
    attempts = 0
    # First pass reaches each selected policy before taking its second chunk.
    for index in range(MAX_CHUNKS_PER_DOC):
        for policy in selected:
            available = chunks[policy.planid]
            if index >= len(available):
                continue
            remaining = services.deadline - time.monotonic()
            if remaining <= 0:
                raise BusinessError("RUN_TIMEOUT")
            if remaining < MODEL_RESERVE_S:
                reading.limits.append("stopped_for_time")
                return reading
            if attempts >= cap:
                reading.limits.append("max_reads")
                return reading
            cid = available[index].chunk_id
            state = reading.policies[policy.planid]
            try:
                result = await services.read(policy.planid, chunk_id=cid)
            except BusinessError as exc:
                if exc.code != "TOOL_BUDGET":
                    raise
                reading.limits.append("stopped_for_tool_budget")
                return reading
            attempts += 1
            status = result.status
            if status == "ok":
                if _registered(result.data, policy.planid, cid, ctx):
                    reading.windows.append(EvidenceWindow(policy.planid, cid, result.data))
                    state.chunks_read += 1
                elif isinstance(result.data, Passage) and not result.data.content:
                    status = "no_text"
                else:
                    status = "unregistered_evidence"
            state.read_attempts.append(dict(chunk_id=cid, status=status))
            if state.chunks_read:
                state.read_status = "read_no_valid_points"
            elif all(a["status"] == "no_text" for a in state.read_attempts):
                state.read_status = "no_text"
            else:
                state.read_status = "read_failed"
    return reading
