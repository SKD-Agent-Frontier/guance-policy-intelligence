"""Typed cross-agent contracts; no unconstrained agent-to-agent conversations."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Assignment:
    id: str
    focus: str
    requirement_ids: tuple[str, ...]
    original_question: str
    targets: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Requirement:
    id: str
    label: str


@dataclass(frozen=True)
class Plan:
    requirements: tuple[Requirement, ...]
    assignments: tuple[Assignment, ...]
    source: str


@dataclass(frozen=True)
class Finding:
    id: str
    worker_id: str
    kind: str
    title: str
    text: str
    claim_indexes: tuple[int, ...]
    requirement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Synthesis:
    id: str
    title: str
    text: str
    fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class Review:
    approved_ids: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    comments: str = ""
    performed: bool = False


@dataclass
class WorkerOutcome:
    assignment: Assignment
    status: str
    result: object | None = None
    error_type: str | None = None
    elapsed_seconds: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    notes: list[str] = field(default_factory=list)
