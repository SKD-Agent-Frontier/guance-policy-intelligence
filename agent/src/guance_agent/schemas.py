"""工具契约与对外结果，不依赖 Agent SDK。"""
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, model_validator

ToolStatus = Literal["ok", "empty", "no_text", "not_found", "invalid_argument",
                     "timeout", "backend_error"]

DIMENSIONS = ("适用对象", "政策工具", "量化目标", "责任主体", "期限", "资金规模")


class TaskRequest(BaseModel):
    run_id: str
    session_id: str
    user_id: str | None = None
    question: str
    created_at: str
    task_type: str = "qa"                    # qa / extract
    dimensions: list[str] = Field(default_factory=list)
    targets: list[str] | None = None

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
    quality_warnings: list[str] = Field(default_factory=list)


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
    contexts: list[dict] = Field(default_factory=list)
    required_relation_ids: list[str] = Field(default_factory=list)
    media_sources: list[dict] = Field(default_factory=list)

class ChunkSpan(BaseModel):
    chunk_id: str
    char_start: int
    char_end: int
    chunk_char_start: int | None = None
    chunk_char_end: int | None = None
    complete: bool = False   # 整块是否完整出现在本页；False 不能登记为已读完整块
    # 块自身的章节归属，不能拿当前父章节范围替代
    section_id: str | None = None
    section_path: str | None = None
    section_char_start: int | None = None
    section_char_end: int | None = None
    quality_warnings: list[str] = Field(default_factory=list)

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
    contexts: list[dict] = Field(default_factory=list)
    required_relation_ids: list[str] = Field(default_factory=list)
    media_sources: list[dict] = Field(default_factory=list)

class ToolResult(BaseModel):
    status: ToolStatus
    message: str = ""
    data: list[Hit] | Passage | None = None

class Claim(BaseModel):
    text: str
    chunk_ids: list[str]
    quotes: list[str] = Field(default_factory=list)
    # 要素提取时填；qa 时为 None。有了它，现有三层校验可以原样复用。
    dimension: str | None = None
    # 提取时模型只给定位线索，不给引文；引文由程序从原文截取后写回 quotes
    anchors: list[str] = Field(default_factory=list)

class QAAnalysisDraft(BaseModel):
    """Schema-safe derived interpretation. Source dependencies are verified later."""
    kind: Literal["synthesis", "comparison", "implication"]
    text: str
    scope_note: str
    supporting_claim_indexes: list[int]


class AgentOutput(BaseModel):
    answer: str = ""
    claims: list[Claim] = Field(default_factory=list)
    insufficient: bool = False
    # Typed shape is required by the SDK strict structured-output mode.
    analyses: list[QAAnalysisDraft] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data):
        """模型常把 claims/answer 写成 null 或缺字段，容错成空值而不是整轮解析失败。"""
        if isinstance(data, dict):
            if data.get("claims") is None:
                data["claims"] = []
            if data.get("answer") is None:
                data["answer"] = ""
            if data.get("insufficient") is None:
                data["insufficient"] = False
            # analyses 是可选增值能力：单条格式错误不能使已完整的事实回答失败。
            # 仅筛结构有效草稿；依赖关系依旧由 finalize_qa_analysis 严格把关。
            drafts = data.get("analyses")
            valid = []
            if isinstance(drafts, list):
                for draft in drafts:
                    try:
                        valid.append(QAAnalysisDraft.model_validate(draft))
                    except (ValidationError, ValueError, TypeError):
                        continue
            data["analyses"] = valid
        return data

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
    out_of_section: bool = False   # 完整块越出登记章节：可引用，但不能当单章节证据

class RunError(BaseModel):
    code: str
    message: str

class RunResult(BaseModel):
    # Never serialized: draft model interpretations cannot escape before verification.
    _qa_analysis_candidates: list[dict] = PrivateAttr(default_factory=list)
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
    task_type: str = "qa"
    structured: dict | None = None
    route: dict | None = None        # 本轮路由决定与逐对象结果   # 功能层产物：要素表 / 比较矩阵等


class DocCandidate(BaseModel):
    """文档定位候选；只描述身份，不携带正文证据。"""
    planid: str
    title: str
    area: str | None = None
    fyp: str | None = None
    document_id: str
    document_version_id: str | None = None
    generation_id: str


class TitleLookup(BaseModel):
    """标题查询结果；matched 是真实匹配数量，不能看候选长度就称唯一。"""
    matched: int = 0
    candidates: list[DocCandidate] = Field(default_factory=list)
