"""模型响应的内部形状及任务参数归一化。

这些类型用于解析各阶段 JSON，不是对外结果契约。对外事实仍使用共享 Claim，
来源仍使用 EvidenceRef，最终 structured 字段由 result.py 按 SCHEMA.md 组装。
类的用途写在普通注释中，避免为解释代码而改变 Pydantic 生成的修正用 schema。
"""
import json
import re

from pydantic import BaseModel, Field

from ..contract import BusinessError

# 只有显式维度和模型识别维度均未提供时才使用默认值；普通比较维度不等于评价标准。
POLICY_DIMENSIONS = ["发展目标", "政策工具", "适用对象"]
SCHEME_DIMENSIONS = ["适用对象", "支持措施", "申请或执行条件"]


# 第一阶段的结果：检索主题与比较维度。evaluation_criteria 必须来自用户原话，
# 不能把“提供依据”“不排名”等输出要求解释成一个新的评判标准。
class ComparisonPlan(BaseModel):
    dimensions: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    evaluation_criteria: list[str] = Field(default_factory=list)


# 第二阶段选择的证据。evidence_ids 是本轮 Collection 内的临时编号，不是 chunk_id。
# text 兼容模型多返回的字段，但 result.py 会以证据原句覆盖它，绝不直接信任其内容。
class Fact(BaseModel):
    planid: str
    dimension: str
    text: str = ""
    evidence_ids: list[str]


# 分析判断靠 supporting_cells 关联事实矩阵，而不是直接附上看似可信的原文编号。
# 单元格编号形如 d0:37982，代表“维度零”的这份政策；引用是否有效由结果层检查。
class Judgment(BaseModel):
    text: str
    supporting_cells: list[str]


# 每行主要异同必须依赖该行的全部政策对象。comparable=False 仍可做受限的文字对照，
# 只是不能视为同口径数值比较；reason 也不能自行补造未取证的经济或实施背景。
class Difference(Judgment):
    dimension: str
    comparable: bool = True
    reason: str = ""


# 一份政策的三类判断分别验证；一类缺证不要求模型编造内容，也不代表政策本身没有优势。
class SchemeAnalysis(BaseModel):
    planid: str
    advantages: list[Judgment] = Field(default_factory=list)
    limitations: list[Judgment] = Field(default_factory=list)
    applicability: list[Judgment] = Field(default_factory=list)


# 证据选择阶段的兼容形状。module.py 会主动丢弃此阶段夹带的两类分析。
class GeneratedComparison(BaseModel):
    facts: list[Fact] = Field(default_factory=list)
    differences: list[Difference] = Field(default_factory=list)
    scheme_analysis: list[SchemeAnalysis] = Field(default_factory=list)


# 分析与复核共享形状，但不含 facts：这两个步骤都不能修改已经确定的原文矩阵。
class GeneratedAnalysis(BaseModel):
    differences: list[Difference] = Field(default_factory=list)
    scheme_analysis: list[SchemeAnalysis] = Field(default_factory=list)


def parse_model(raw: str, model):
    """将一个模型响应转换为当前阶段的类型，形状或语法错误返回 OUTPUT_PARSE。

    只移除完整思考标记和包住整个响应的 JSON 代码围栏，不从一段任意文字里
    猜测性地截取大括号。这样拼接对象、截断输出和多余尾文不会被当成完整结果。
    空数组可表示缺证；响应缺少阶段标识字段则属于输出形状错误。
    """
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
        # Pydantic 中的数组允许为空，但不能让字段错位的响应利用默认空数组
        # 静默变成“证据不足”。证据选择和分析分别要求显式返回自己的阶段字段。
        required = "facts" if model is GeneratedComparison else (
            "differences" if model is GeneratedAnalysis else None)
        if required and required not in value:
            raise ValueError(f"missing {required} field")
        return model.model_validate(value)
    except (ValueError, TypeError) as exc:
        raise BusinessError("OUTPUT_PARSE") from exc


def resolve_plan(plan, task, *, scheme):
    """确定本轮使用的维度、有限查询词和用户给定评价标准。

    顺序为显式 dimensions → 模型识别维度 → 功能默认维度。保留第一次出现的
    顺序，保证矩阵行顺序和后续 d{index}:planid 引用稳定。查询词在这里限长限数，
    工具层还会再次执行真实参数和范围校验；本函数不改变路由与 ctx.scope。
    """
    defaults = SCHEME_DIMENSIONS if scheme else POLICY_DIMENSIONS
    dimensions = task.dimensions if task.dimensions else (plan.dimensions or defaults)
    dimensions = list(dict.fromkeys(d.strip() for d in dimensions if d.strip()))
    if not dimensions:
        raise BusinessError("INVALID_INPUT")
    queries = list(dict.fromkeys(q.strip() for q in plan.queries
                                if 0 < len(q.strip()) <= 200))
    # 模型未给有效查询时使用维度作为回退主题，避免完全失去检索入口。
    if not queries:
        queries = [d for d in dimensions if len(d) <= 200] or ["政策支持措施"]
    # 原话包含检查只能约束来源，不能完全证明该短语语义上就是评价标准；
    # 提示词还会区分评价偏好与输出要求，不据此自动评分或分配权重。
    criteria = list(dict.fromkeys(c.strip() for c in plan.evaluation_criteria
                                 if c.strip() and c.strip() in task.question))
    return ComparisonPlan(dimensions=dimensions, queries=queries[:4],
                          evaluation_criteria=criteria)
