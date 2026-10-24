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

class SourceMeta(BaseModel):
    """proposal-v0.3 的来源与坐标信息；mock 可为 None，真实后端必须给全。"""

    document_id: str | None = None
    document_version_id: str | None = None
    generation_id: str | None = None
    source_hash: str | None = None
    section_id: str | None = None
    section_char_start: int | None = None
    section_char_end: int | None = None
    offset_basis: str | None = None
    offset_unit: str | None = None
    body_origin: int | None = None
    contract_version: str | None = None


class Hit(SourceMeta):
    planid: str
    chunk_id: str
    parent_id: str | None = None
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
    chunk_char_start: int | None = None
    chunk_char_end: int | None = None
    truncated: bool = False
    score: float
    source_type: str | None = None

class ChunkSpan(BaseModel):
    chunk_id: str
    char_start: int
    char_end: int
    chunk_char_start: int | None = None
    chunk_char_end: int | None = None
    complete: bool = False   # 整块是否完整出现在本页；False 不能登记为已读完整块

class Passage(SourceMeta):
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
    is_index: bool = False   # True 表示本响应是章节目录，不携带正文
    read_scope: str | None = None            # index / section / chunk
    chunk_id: str | None = None
    parent_id: str | None = None
    chunk_char_start: int | None = None
    chunk_char_end: int | None = None
    truncated: bool = False
    next_offset: int | None = None           # 对象内下一页起点；读完为 None
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

class EvidenceRef(SourceMeta):
    planid: str
    chunk_id: str
    parent_id: str | None = None
    title: str
    area: str | None
    fyp: str | None
    admin_level: str | None
    plantype_label: str | None
    section_idx: int | None
    section_path: str
    char_start: int
    char_end: int
    chunk_char_start: int | None = None
    chunk_char_end: int | None = None
    truncated: bool = False
    source: Literal["kb_search", "kb_read"]
    observed_ranges: list[tuple[int, int]] = Field(default_factory=list)
    complete: bool = False   # 已见范围是否覆盖完整块

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
    warnings: list[str] = Field(default_factory=list)
