"""比较模块内部模型；事实和来源始终使用基础层的 Claim/EvidenceRef。"""
import json
import re

from pydantic import BaseModel, Field

from ..contract import BusinessError

POLICY_DIMENSIONS = ["发展目标", "政策工具", "适用对象"]
SCHEME_DIMENSIONS = ["适用对象", "支持措施", "申请或执行条件"]


class ComparisonPlan(BaseModel):
    dimensions: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    evaluation_criteria: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    planid: str
    dimension: str
    text: str = ""
    evidence_ids: list[str]


class Judgment(BaseModel):
    text: str
    supporting_cells: list[str]


class Difference(Judgment):
    dimension: str
    comparable: bool = True
    reason: str = ""


class SchemeAnalysis(BaseModel):
    planid: str
    advantages: list[Judgment] = Field(default_factory=list)
    limitations: list[Judgment] = Field(default_factory=list)
    applicability: list[Judgment] = Field(default_factory=list)


class GeneratedComparison(BaseModel):
    facts: list[Fact] = Field(default_factory=list)
    differences: list[Difference] = Field(default_factory=list)
    scheme_analysis: list[SchemeAnalysis] = Field(default_factory=list)


class GeneratedAnalysis(BaseModel):
    differences: list[Difference] = Field(default_factory=list)
    scheme_analysis: list[SchemeAnalysis] = Field(default_factory=list)


def parse_model(raw: str, model):
    """只接受唯一 JSON 对象，兼容代码围栏和接口返回的思考标记。"""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip()
    if cleaned.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            raise BusinessError("OUTPUT_PARSE")
        cleaned = match.group(1)
    try:
        value = json.loads(cleaned)
        if not isinstance(value, dict):
            raise ValueError("expected one JSON object")
        if model is GeneratedComparison and "facts" not in value:
            raise ValueError("missing facts field")
        return model.model_validate(value)
    except (ValueError, TypeError) as exc:
        raise BusinessError("OUTPUT_PARSE") from exc


def resolve_plan(plan, task, *, scheme):
    """显式维度优先；评价标准必须原样来自用户问题，不能由模型代拟。"""
    defaults = SCHEME_DIMENSIONS if scheme else POLICY_DIMENSIONS
    dimensions = task.dimensions if task.dimensions else (plan.dimensions or defaults)
    dimensions = list(dict.fromkeys(d.strip() for d in dimensions if d.strip()))
    if not dimensions:
        raise BusinessError("INVALID_INPUT")
    queries = list(dict.fromkeys(q.strip() for q in plan.queries
                                if 0 < len(q.strip()) <= 200))
    if not queries:
        queries = [d for d in dimensions if len(d) <= 200] or ["政策支持措施"]
    criteria = list(dict.fromkeys(c.strip() for c in plan.evaluation_criteria
                                 if c.strip() and c.strip() in task.question))
    return ComparisonPlan(dimensions=dimensions, queries=queries[:4],
                          evaluation_criteria=criteria)
