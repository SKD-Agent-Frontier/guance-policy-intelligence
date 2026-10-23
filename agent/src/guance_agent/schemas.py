"""工具契约与对外结果，不依赖 Agent SDK。"""
from typing import Literal

from pydantic import BaseModel, Field

ToolStatus = Literal["ok", "empty", "no_text", "not_found", "invalid_argument",
                     "timeout", "backend_error"]


class TaskRequest(BaseModel):
    run_id: str
    session_id: str
    user_id: str | None = None
    question: str
    created_at: str


class Hit(BaseModel):
    planid: str
    chunk_id: str
    title: str
    area: str | None
    fyp: str | None
    admin_level: str | None
    plantype_label: str | None
    section_idx: int | None
    section_path: str
    content: str
    char_start: int
    char_end: int
    score: float


class ChunkSpan(BaseModel):
    chunk_id: str
    char_start: int
    char_end: int


class Passage(BaseModel):
    planid: str
    title: str
    area: str | None
    fyp: str | None
    admin_level: str | None
    plantype_label: str | None
    section_idx: int | None
    section_path: str
    content: str
    char_start: int | None
    char_end: int | None
    truncated: bool = False
    chunks: list[ChunkSpan] = Field(default_factory=list)
    sections: list[dict] | None = None


class ToolResult(BaseModel):
    status: ToolStatus
    message: str = ""
    data: list[Hit] | Passage | None = None


class Claim(BaseModel):
    text: str
    chunk_ids: list[str]
    quotes: list[str] = Field(default_factory=list)


class AgentOutput(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    insufficient: bool = False


class EvidenceRef(BaseModel):
    planid: str
    chunk_id: str
    title: str
    area: str | None
    fyp: str | None
    admin_level: str | None
    plantype_label: str | None
    section_idx: int | None
    section_path: str
    char_start: int
    char_end: int
    source: Literal["kb_search", "kb_read"]


class RunError(BaseModel):
    code: str
    message: str


class RunResult(BaseModel):
    run_id: str
    session_id: str
    status: Literal["completed", "failed", "limit_reached"]
    evidence_status: Literal["verified", "insufficient", "none"]
    answer: str
    claims: list[Claim]
    evidence_refs: list[EvidenceRef]
    dropped_chunk_ids: list[str]
    error: RunError | None
    meta: dict
