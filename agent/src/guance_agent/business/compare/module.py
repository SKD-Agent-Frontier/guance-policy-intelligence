"""政策比较与多方案分析的公共流程，所有外部能力均由BusinessServices提供。"""
import json

from ..contract import BusinessError
from .evidence import collect
from .models import (
    ComparisonPlan,
    GeneratedAnalysis,
    GeneratedComparison,
    parse_model,
    resolve_plan,
)
from .prompts import analysis_messages, comparison_messages, planning_messages
from .result import build_outcome


class PolicyComparisonModule:
    name = "compare"
    requires_targets = True
    scheme = False

    async def __call__(self, task, ctx, services):
        if not task.targets or not 2 <= len(task.targets) <= 8:
            raise BusinessError("INVALID_INPUT")
        repaired = False

        async def ask(messages, model):
            nonlocal repaired
            raw = await services.call_model(messages)
            try:
                return parse_model(raw, model)
            except BusinessError:
                if repaired:
                    raise
                repaired = True
                # 全流程至多修正一次格式，不重新取证或放宽引用检查。
                repair = [{"role": "system", "content":
                           "只修正下列响应的JSON语法和字段形状，按schema输出唯一JSON对象。"
                           "不得新增、改写事实、编号或分析判断；缺证数组保留为空。"},
                          {"role": "user", "content": json.dumps(
                              {"schema": model.model_json_schema(), "response": raw}, ensure_ascii=False)}]
                return parse_model(await services.call_model(repair), model)

        parsed = await ask(planning_messages(task, scheme=self.scheme), ComparisonPlan)
        plan = resolve_plan(parsed, task, scheme=self.scheme)
        collection = await collect(task, ctx, services, plan)
        if collection.units:
            generated = await ask(comparison_messages(
                task, plan, collection, scheme=self.scheme), GeneratedComparison)
        else:
            generated = GeneratedComparison()
        generated = generated.model_copy(update={"differences": [], "scheme_analysis": []})
        factual = build_outcome(task, plan, collection, generated, scheme=False)
        if factual.claims:
            analyses = await ask(analysis_messages(
                task, plan, factual.structured, scheme=self.scheme), GeneratedAnalysis)
            analyses = await ask(analysis_messages(
                task, plan, factual.structured, scheme=self.scheme, draft=analyses), GeneratedAnalysis)
            generated = generated.model_copy(update={"differences": analyses.differences,
                                                     "scheme_analysis": analyses.scheme_analysis})
        return build_outcome(task, plan, collection, generated, scheme=self.scheme)


class SchemeComparisonModule(PolicyComparisonModule):
    name = "compare_plans"
    scheme = True
