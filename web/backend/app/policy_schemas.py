"""政策文献目录接口的响应模型（只描述契约，不加载任何数据）。"""
from typing import Literal

from pydantic import BaseModel, Field, StrictInt, StrictStr


class DatasetInfo(BaseModel):
    generation_id: str
    revision: int
    total_documents: int
    partial: bool | None = None
    source: Literal["current_published_frozen_release"] = "current_published_frozen_release"
    region_version: str | None = None
    region_mapping_version: str | None = None


class LegacyDatasetInfo(BaseModel):
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
    admin_level: str
    region_code: str | None
    region_status: str | None
    matches: list[MatchSnippet] = Field(default_factory=list)


class QueryInfo(BaseModel):
    q: str | None
    area: str | None
    fyp: str | None
    plantype: str | None
    admin_level: str | None = None
    province_code: str | None = None
    prefecture_code: str | None = None
    county_code: str | None = None
    region_status: str | None = None
    year: int | Literal["unknown"] | None = None
    fyp_status: str | None = None
    plantype_status: str | None = None
    match_mode: Literal["literal"] | None = None


class FacetQueryInfo(BaseModel):
    q: str | None = None
    area: str | None = None
    admin_level: str | None = None
    province_code: str | None = None
    prefecture_code: str | None = None
    county_code: str | None = None
    region_status: str | None = None
    year: int | Literal["unknown"] | None = None
    fyp: str | None = None
    fyp_status: str | None = None
    plantype: str | None = None
    plantype_status: str | None = None


class FacetOption(BaseModel):
    value: StrictInt | StrictStr
    label: str
    count: int


class RegionOption(BaseModel):
    code: str
    name: str
    source_type: str
    tier: str
    count: int


class CountLabel(BaseModel):
    label: str
    count: int


class RegionFacets(BaseModel):
    province_options: list[RegionOption]
    prefecture_options: list[RegionOption]
    county_options: list[RegionOption]
    unclassified: CountLabel
    not_applicable: CountLabel


class AdminLevelFacet(BaseModel):
    options: list[FacetOption]
    regions: RegionFacets


class ValueFacet(BaseModel):
    options: list[FacetOption]
    unclassified: CountLabel


class YearFacet(BaseModel):
    options: list[FacetOption]


class FacetData(BaseModel):
    admin_level: AdminLevelFacet
    year: YearFacet
    fyp: ValueFacet
    plantype: ValueFacet


class RegionNode(BaseModel):
    code: str
    name: str
    source_type: str
    tier: str
    children: list["RegionNode"]


class RegionTreeResponse(BaseModel):
    region_version: str
    artifact_sha256: str
    source: str
    source_url: str
    retrieved_at_utc: str | None
    snapshot_as_of: str | None
    official_version: str | None
    node_count: int
    data: list[RegionNode]


class PolicyFacetsResponse(BaseModel):
    dataset: DatasetInfo
    query: FacetQueryInfo
    facets: FacetData


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
    dataset: LegacyDatasetInfo
    policy: PolicyDetail


class PolicyContentResponse(BaseModel):
    dataset: LegacyDatasetInfo
    planid: str
    title: str | None
    offset: int
    limit: int
    total_chars: int
    has_more: bool
    text: str = Field(description="冻结正文的连续片段；offset 以正文字符为起点")
