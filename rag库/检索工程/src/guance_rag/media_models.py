"""媒体契约独立于Markdown正文坐标，保留原图片出现位置和识别版本。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset_basis: Literal["body_first_non_whitespace"] = "body_first_non_whitespace"
    offset_unit: Literal["unicode_codepoint"] = "unicode_codepoint"
    body_origin: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    content: str

    @model_validator(mode="after")
    def check_range(self):
        if len(self.content) != self.char_end - self.char_start:
            raise ValueError("图片引用位置与Markdown原文不一致")
        return self


class OCRRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset_basis: Literal["ocr_transcript"] = "ocr_transcript"
    offset_unit: Literal["unicode_codepoint_in_ocr_transcript"] = "unicode_codepoint_in_ocr_transcript"
    transcript_id: str
    transcript_hash: str
    full_char_start: int = Field(ge=0)
    full_char_end: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)

    @model_validator(mode="after")
    def check_range(self):
        if not self.full_char_start <= self.char_start < self.char_end <= self.full_char_end:
            raise ValueError("OCR页必须属于所选转录范围")
        return self


class MediaPointer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["image_occurrence"] = "image_occurrence"
    source_id: str
    asset_id: str
    document_id: str
    generation_id: str
    association_basis: str
    reference_span: DocumentAnchor | None
    reference_complete: bool | None


class OCRContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    char_start: int
    char_end: int
    content: str
    offset_unit: Literal["unicode_codepoint_in_ocr_transcript"] = "unicode_codepoint_in_ocr_transcript"


class ImageReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    sha256: str
    mime_type: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    byte_length: int = Field(gt=0)
    path: str
    bbox_pixels: list[int] | None
    coordinate_unit: Literal["original_image_pixels"] = "original_image_pixels"
    coordinate_basis: str | None


class MediaEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["proposal-v0.4"] = "proposal-v0.4"
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
    section_id: str | None
    section_idx: int | None
    section_path: str
    source_type: Literal["visual_chunk", "image_occurrence"]
    source_id: str
    occurrence_id: str
    asset_id: str
    visual_chunk_id: str | None
    extraction_id: str | None
    ocr_parent_id: str | None
    recognition_status: str
    kind: str | None
    evidence_label: str
    content: str
    content_basis: Literal["ocr_transcript", "image_alt", "none"]
    ocr_range: OCRRange | None
    association_basis: str
    reference_span: DocumentAnchor | None
    image: ImageReference
    ocr_contexts: list[OCRContext] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    truncated: bool

    @model_validator(mode="after")
    def check_content(self):
        if self.ocr_range is not None:
            span = self.ocr_range
            if self.content_basis != "ocr_transcript" or len(self.content) != span.char_end - span.char_start:
                raise ValueError("OCR正文与转录坐标不一致")
        elif self.content_basis == "ocr_transcript":
            raise ValueError("OCR文字必须携带独立转录坐标")
        return self


class MediaHitV04(MediaEvidence):
    representation_id: str
    representation_type: Literal["text", "image", "image_text"]
    score: float

    @model_validator(mode="after")
    def check_preview(self):
        if self.ocr_range:
            s = self.ocr_range
            if self.truncated != (s.char_start != s.full_char_start or s.char_end != s.full_char_end):
                raise ValueError("OCR预览截断标志不一致")
        return self


class MediaPassageV04(MediaEvidence):
    read_scope: Literal["media"] = "media"
    next_offset: int | None

    @model_validator(mode="after")
    def check_paging(self):
        if self.truncated != (self.next_offset is not None):
            raise ValueError("OCR续读终止标志不一致")
        return self


class ImageDataV04(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["proposal-v0.4"] = "proposal-v0.4"
    source_id: str
    occurrence_id: str
    asset_id: str
    document_id: str
    generation_id: str
    variant: Literal["original", "region"]
    mime_type: str
    width: int
    height: int
    sha256: str
    source_image_sha256: str
    byte_length: int
    bbox_pixels: list[int]
    coordinate_unit: Literal["original_image_pixels"] = "original_image_pixels"
    data_url: str
