"""政策文献目录接口的响应模型（只描述契约，不加载任何数据）。"""
from typing import Literal

from pydantic import BaseModel, Field


class DatasetInfo(BaseModel):
    generation_id: str
    revision: int
    total_documents: int
    partial: bool | None = None
    source: Literal["current_published_frozen_release"] = "current_published_frozen_release"


class MatchSnippet(BaseModel):
    field: Literal["title", "body"]
    offset: int = Field(description="命中位置：标题或正文字符串内的 Unicode 码点偏移")
    snippet: str = Field(description="来自冻结原文的连续片段，长度受限")


class PolicyItem(BaseModel):
    planid: str
    title: str | None
    area: str | None
    fyp: str | None
    plantype_label: str | None
    year: int | None = Field(description="政策标题或规范规划期推导的参考年份；不是发布日期")
    matches: list[MatchSnippet] = Field(default_factory=list)


class QueryInfo(BaseModel):
    q: str | None
    area: str | None
    fyp: str | None
    plantype: str | None
    match_mode: Literal["literal"] | None = None


class PolicyListResponse(BaseModel):
    dataset: DatasetInfo
    query: QueryInfo
    matched_documents: int = Field(description="应用全部筛选条件后的政策数量，与分页无关")
    page: int
    page_size: int
    total_pages: int
    items: list[PolicyItem]


class SectionInfo(BaseModel):
    index: int
    level: int
    path: str
    start: int = Field(description="相对正文起点的码点偏移")
    end: int


class PolicyDetail(BaseModel):
    planid: str
    title: str | None
    area: str | None
    fyp: str | None
    plantype_label: str | None
    year: int | None = Field(description="政策标题或规范规划期推导的参考年份；不是发布日期")
    body_chars: int
    sections: list[SectionInfo]


class PolicyDetailResponse(BaseModel):
    dataset: DatasetInfo
    policy: PolicyDetail


class PolicyContentResponse(BaseModel):
    dataset: DatasetInfo
    planid: str
    title: str | None
    offset: int
    limit: int
    total_chars: int
    has_more: bool
    text: str = Field(description="冻结正文的连续片段；offset 以正文字符为起点")
