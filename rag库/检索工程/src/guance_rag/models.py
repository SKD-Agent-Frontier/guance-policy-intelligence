"""proposal-v0.3的独立类型；Agent可用model_dump后验证到其扩展类型。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .media_models import MediaPointer


class SourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["proposal-v0.3"] = "proposal-v0.3"
    planid: str
    title: str
    area: str | None
    fyp: str | None
    admin_level: str | None
    plantype_label: str | None
    document_id: str
    document_version_id: str | None
    source_hash: str
    generation_id: str
    source_path: str
    snapshot: str
    offset_basis: Literal["body_first_non_whitespace"] = "body_first_non_whitespace"
    offset_unit: Literal["unicode_codepoint"] = "unicode_codepoint"
    body_start: int = Field(ge=0)
    body_origin: int = Field(ge=0)
    section_id: str | None
    section_idx: int | None
    section_path: str


class ContextSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    document_id: str
    source_hash: str
    generation_id: str
    body_origin: int = Field(ge=0)
    offset_basis: Literal["body_first_non_whitespace"] = "body_first_non_whitespace"
    offset_unit: Literal["unicode_codepoint"] = "unicode_codepoint"
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    content: str
    role: str


class ChunkSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str
    chunk_char_start: int = Field(ge=0)
    chunk_char_end: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    complete: bool
    section_id: str | None = None
    section_path: str = ""
    section_char_start: int | None = Field(default=None, ge=0)
    section_char_end: int | None = Field(default=None, ge=0)
    quality_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_ranges(self):
        if not self.chunk_char_start <= self.char_start < self.char_end <= self.chunk_char_end:
            raise ValueError("页内块范围必须是完整块的真实交集")
        if self.complete != (self.char_start == self.chunk_char_start and self.char_end == self.chunk_char_end):
            raise ValueError("complete与实际交集不一致")
        return self


class HitV03(SourceInfo):
    chunk_id: str
    parent_id: str
    source_type: Literal["text_chunk"] = "text_chunk"
    representation_id: str
    chunk_char_start: int = Field(ge=0)
    chunk_char_end: int = Field(ge=0)
    section_char_start: int | None = Field(default=None, ge=0)
    section_char_end: int | None = Field(default=None, ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    content: str
    score: float
    truncated: bool
    contexts: list[ContextSpan] = Field(default_factory=list)
    required_relation_ids: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    media_sources: list[MediaPointer] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_ranges(self):
        if not self.chunk_char_start <= self.char_start < self.char_end <= self.chunk_char_end:
            raise ValueError("预览必须位于完整块内")
        if len(self.content) != self.char_end - self.char_start:
            raise ValueError("content长度与码点范围不一致")
        if self.truncated != (self.char_start != self.chunk_char_start or self.char_end != self.chunk_char_end):
            raise ValueError("truncated与完整块范围不一致")
        return self


class PassageV03(SourceInfo):
    read_scope: Literal["index", "section", "chunk"]
    chunk_id: str | None = None
    parent_id: str | None = None
    chunk_char_start: int | None = None
    chunk_char_end: int | None = None
    section_char_start: int | None = None
    section_char_end: int | None = None
    content: str
    char_start: int | None
    char_end: int | None
    is_index: bool = False
    truncated: bool = False
    next_offset: int | None = None
    chunks: list[ChunkSpan] = Field(default_factory=list)
    contexts: list[ContextSpan] = Field(default_factory=list)
    required_relation_ids: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    sections: list[dict] | None = None
    media_sources: list[MediaPointer] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_ranges(self):
        if self.is_index:
            if self.read_scope != "index" or self.content or self.char_start is not None or self.char_end is not None:
                raise ValueError("目录不能携带正文范围")
        elif self.char_start is None or self.char_end is None or self.char_start < 0 or len(self.content) != self.char_end - self.char_start:
            raise ValueError("正文与码点范围不一致")
        if self.truncated != (self.next_offset is not None):
            raise ValueError("分页终止标志不一致")
        return self


class DocCandidate(BaseModel):
    """文档定位结果；只描述对象身份，不携带正文证据。"""
    model_config = ConfigDict(extra="forbid")
    planid: str
    title: str
    area: str | None
    fyp: str | None
    document_id: str
    document_version_id: str | None
    generation_id: str


class TitleLookup(BaseModel):
    """标题查询结果：matched 是真实匹配数量，不能只看候选列表长度就宣称唯一。"""
    model_config = ConfigDict(extra="forbid")
    matched: int = Field(ge=0)
    candidates: list[DocCandidate] = Field(default_factory=list)
