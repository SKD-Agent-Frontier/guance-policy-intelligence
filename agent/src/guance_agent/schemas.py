"""工具契约与对外结果，不依赖 Agent SDK。"""
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

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
    fact_ids: list[str] = Field(default_factory=list)
    content_period: str | None = None
    fact_status: Literal["achieved", "target", "ongoing", "unspecified"] = "unspecified"
    time_anchor: str | None = None
    # 要素提取时填；qa 时为 None。有了它，现有三层校验可以原样复用。
    dimension: str | None = None
    # 提取时模型只给定位线索，不给引文；引文由程序从原文截取后写回 quotes
    anchors: list[str] = Field(default_factory=list)

    @field_validator("chunk_ids", "quotes", "anchors", mode="before")
    @classmethod
    def _coerce_claim_lists(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, tuple):
            return list(value)
        return value

class QABlock(BaseModel):
    id: str = Field(min_length=1)
    title_path: list[str] = Field(min_length=1, max_length=2)
    dimension: str = Field(min_length=1)
    kind: Literal["fact", "analysis"]
    text: str = Field(min_length=1, max_length=1200)
    supporting_claim_indexes: list[StrictInt] = Field(min_length=1, max_length=96)

    @model_validator(mode="after")
    def _validate_support(self):
        if any(not title or len(title) > 80 or "\n" in title or "\r" in title
               for title in self.title_path):
            raise ValueError("每级标题必须为 1–80 个字符")
        if any(index < 0 for index in self.supporting_claim_indexes):
            raise ValueError("事实索引必须是非负整数")
        if len(set(self.supporting_claim_indexes)) != len(self.supporting_claim_indexes):
            raise ValueError("事实索引不能重复")
        # A one-source value check is valid; prune_report applies the frozen
        # requirement-specific gate and still rejects generic one-source analysis.
        if not self.supporting_claim_indexes:
            raise ValueError("报告块至少依赖 1 条事实")
        return self


class QAReportDraft(BaseModel):
    dimensions: list[str] = Field(min_length=1, max_length=8)
    blocks: list[QABlock] = Field(max_length=12)

    @model_validator(mode="after")
    def _validate_report(self):
        if any(not dimension for dimension in self.dimensions):
            raise ValueError("报告维度不能为空")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("报告维度不能重复")
        if len({block.id for block in self.blocks}) != len(self.blocks):
            raise ValueError("报告块 ID 不能重复")
        if sum(len(block.supporting_claim_indexes) for block in self.blocks) > 96:
            raise ValueError("报告总事实依赖不能超过 96 条")
        if any(block.dimension not in self.dimensions for block in self.blocks):
            raise ValueError("报告块维度必须属于报告声明的维度")
        return self


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
    report: QAReportDraft | None = None
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
            if "report" not in data:
                data["report"] = None
            # One malformed claim must not discard every other verified fact.
            raw_claims = data.get("claims")
            if isinstance(raw_claims, list):
                valid_claims = []
                for raw in raw_claims:
                    try:
                        valid_claims.append(Claim.model_validate(raw))
                    except (ValidationError, ValueError, TypeError):
                        continue
                data["claims"] = valid_claims
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
    _qa_report_candidates: list[dict] = PrivateAttr(default_factory=list)
    _qa_report_dimensions: list[str] = PrivateAttr(default_factory=list)
    _qa_report_supplied: bool = PrivateAttr(default=False)
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
