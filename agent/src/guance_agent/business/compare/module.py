"""政策对比和多方案分析共用的业务编排。

本模块只组织功能层步骤：解析需求、取证、选择原句、分析、复核和组装结果。
模型客户端、RAG、调用预算、证据登记均复用注入的 BusinessServices；最终
RunResult 仍由基础层创建。这样新增业务不需要复制或绕开基础层的核验流程。
"""
import json

from ..contract import BusinessError, BusinessOutcome
from .evidence import collect
from .models import (
    ComparisonPlan,
    GeneratedAnalysis,
    GeneratedComparison,
    parse_model,
    resolve_plan,
)
from .prompts import analysis_messages, comparison_messages, planning_messages
from .relevance import explicit_comparison_topic, topic_mismatched_objects
from .result import build_outcome


class PolicyComparisonModule:
    """注册为 compare 的可调用模块，一次处理完整的目标政策集合。

    scheme 只控制默认维度和是否输出方案分析。子类复用同一取证和核验路径，
    避免政策对比与方案分析各自实现一套容易产生口径差异的流程。
    """

    name = "compare"
    # 目标必须由入口/路由唯一确定；本模块不从开放搜索结果中擅自挑选政策。
    requires_targets = True
    scheme = False

    async def __call__(self, task, ctx, services):
        """返回 BusinessOutcome；预算、超时或模型服务错误交由基础层统一转换。"""
        # 入口已有同类校验，这里再次保护直接调用业务模块的情况。
        if not task.targets or not 2 <= len(task.targets) <= 8:
            raise BusinessError("INVALID_INPUT")
        # 修正次数属于整次业务调用，不能让四个模型步骤各自无限重试。
        corrected = False

        async def ask(messages, model):
            """调用共享模型服务并按当前步骤的内部模型解析响应。

            服务自身负责时间/轮次预算及遥测；这里只修正已经收到的非合规 JSON。
            网络、超时等服务错误发生在解析之前，不会被误当成 JSON 问题重试。
            """
            nonlocal corrected
            raw = await services.call_model(messages)
            try:
                return parse_model(raw, model)
            except BusinessError:
                if corrected:
                    raise
                corrected = True
                # 带回目标 schema 和原响应，仅要求形状修正。修正后的内容仍要经过
                # 下游证据编号、单元格依赖和基础层引文检查，不能因此增加信任等级。
                repair = [{"role": "system", "content":
                           "只修正下列响应的JSON语法和字段形状，按schema输出唯一JSON对象。"
                           "不得新增、改写事实、编号或分析判断；缺证数组保留为空。"},
                          {"role": "user", "content": json.dumps(
                              {"schema": model.model_json_schema(), "response": raw}, ensure_ascii=False)}]
                return parse_model(await services.call_model(repair), model)

        # 第一步只解析任务，不生成政策事实。resolve_plan 再用显式参数覆盖模型选择，
        # 并把评价标准限制在用户原问题中，防止模型自行定义“最佳方案”的标准。
        parsed = await ask(planning_messages(task, scheme=self.scheme), ComparisonPlan)
        plan = resolve_plan(parsed, task, scheme=self.scheme)
        collection = await collect(task, ctx, services, plan)
        # A quoted passage can be true but unrelated to the comparison subject.
        # Reject the entire run before selecting facts or drafting differences.
        topic = explicit_comparison_topic(task.question)
        if topic:
            mismatch = topic_mismatched_objects(collection.objects, topic, task.targets)
            if mismatch:
                return BusinessOutcome(
                    answer=f"本轮无法确认全部比较对象符合「{topic}」研究主题；"
                           "已停止比较，未生成异同判断。请在政策目录核对研究对象："
                           + "；".join(mismatch),
                    insufficient=True,
                    structured={"research_gate": {
                        "status": "blocked", "reason": "topic_mismatch",
                        "topic": topic, "mismatched_objects": mismatch,
                    }},
                )
        # 第二步仅让模型选择本轮已读证据编号。没有正文就不要求模型“补齐”事实。
        if collection.units:
            generated = await ask(comparison_messages(
                task, plan, collection, scheme=self.scheme), GeneratedComparison)
            if not generated.facts and not corrected:
                # 已读到候选原句却全空时，给一次针对空选择的复核机会。与格式修正
                # 共用一次纠正预算，正常四次、至多五次调用；仍空就按不足处理。
                # 复核只能选择当前已有证据编号，后续原文组装和统一核验照常执行。
                corrected = True
                generated = await ask(comparison_messages(
                    task, plan, collection, scheme=self.scheme, draft=generated), GeneratedComparison)
        else:
            generated = GeneratedComparison()
        # 即使证据选择响应夹带分析，也不采纳。事实矩阵先由程序用原句建立，
        # 后续分析只能依赖这份矩阵，不能反过来用分析草稿修改事实。
        generated = generated.model_copy(update={"differences": [], "scheme_analysis": []})
        factual = build_outcome(task, plan, collection, generated, scheme=False)
        if factual.claims:
            # 第三步生成判断，第四步对照同一矩阵复核主体、条件及关系。
            # 这里的矩阵已通过业务编号检查，尚待最终基础层逐字核验；模型复核
            # 也是分析步骤，不能被解释成已经完成确定性的语义核验。
            analyses = await ask(analysis_messages(
                task, plan, factual.structured, scheme=self.scheme), GeneratedAnalysis)
            # 在原有第四次调用中反馈硬约束错误，避免复核只改措辞却重复无效依赖。
            # 事实选择仍保持不变；反馈不补造依据，也不增加重试或调用次数。
            draft = generated.model_copy(update={"differences": analyses.differences,
                                                 "scheme_analysis": analyses.scheme_analysis})
            feedback = build_outcome(task, plan, collection, draft, scheme=self.scheme)
            analyses = await ask(analysis_messages(
                task, plan, factual.structured, scheme=self.scheme, draft=analyses,
                validation_feedback=feedback.structured["analysis_gaps"]), GeneratedAnalysis)
            generated = generated.model_copy(update={"differences": analyses.differences,
                                                     "scheme_analysis": analyses.scheme_analysis})
        # 重新从原始证据选择与复核后的分析组装结果：无效依赖在此撤回，
        # 基础层随后仍可因引文失效清空 structured，本模块不能保留旧矩阵绕过它。
        return build_outcome(task, plan, collection, generated, scheme=self.scheme)


class SchemeComparisonModule(PolicyComparisonModule):
    """以 compare_plans 注册，增加优势、限制、适用性，沿用全部公共约束。"""

    name = "compare_plans"
    scheme = True
