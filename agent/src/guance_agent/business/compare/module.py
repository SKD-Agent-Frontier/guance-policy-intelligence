"""政策比较与多方案分析的公共流程，所有外部能力均由BusinessServices提供。"""
from ..contract import BusinessError
from .evidence import collect
from .models import ComparisonPlan, GeneratedComparison, parse_model, resolve_plan
from .prompts import comparison_messages, planning_messages
from .result import build_outcome


class PolicyComparisonModule:
    name = "compare"
    requires_targets = True
    scheme = False

    async def __call__(self, task, ctx, services):
        if not task.targets or not 2 <= len(task.targets) <= 8:
            raise BusinessError("INVALID_INPUT")
        raw = await services.call_model(planning_messages(task, scheme=self.scheme))
        plan = resolve_plan(parse_model(raw, ComparisonPlan), task, scheme=self.scheme)
        collection = await collect(task, ctx, services, plan)
        if collection.units:
            raw = await services.call_model(comparison_messages(
                task, plan, collection, scheme=self.scheme))
            generated = parse_model(raw, GeneratedComparison)
        else:
            generated = GeneratedComparison()
        return build_outcome(task, plan, collection, generated, scheme=self.scheme)


class SchemeComparisonModule(PolicyComparisonModule):
    name = "compare_plans"
    scheme = True
