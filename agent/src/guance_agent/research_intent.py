"""Bounded task-intent inference for policy research.

This module deliberately separates *what the user wants to do with information*
from *which policy/scope to retrieve*.  The existing router still owns the
latter; intent only changes how the verified research result is processed.

Rules are the cheap fast path.  The caller may pass a prior research turn so
follow-ups such as "总结以上内容" are recognised without re-retrieving policy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .research_targets import extract_targets
from .routing import current_question, parse_areas

INTENTS = (
    "fact_lookup", "organize", "summarize", "analyze",
    "compare", "synthesize", "clarify",
)

_SUMMARIZE = re.compile(
    r"总结|概括|归纳|提炼|压缩|简要说明|简短总结|一句话总结|要点总结|"
    r"总体概括|概要|总的来看|概括一下|总结一下"
)
_ORGANIZE = re.compile(
    r"梳理|整理|分类|分方面|分维度|结构化|重点体现在|重点有哪些|"
    r"主要措施|主要方向|主要内容|主要部署|工作重心|重点任务|"
    r"有哪些|哪些方面|展开讲讲|具体内容|整体部署|框架|结构"
)
_ANALYZE = re.compile(
    r"分析|研判|说明|为什么|为何|原因|反映了|体现出|体现.*思路|"
    r"怎样的.*思路|发展思路|特点|特征|意义|影响|启示|判断|评价|"
    r"换个角度|深入|展开分析|意味着|如何理解"
)
_COMPARE = re.compile(
    r"比较(?!优势)|对比|相比|差异|异同|共同点|共同做法|共同思路|不同点|"
    r"区别于|另一个地区|其他地区|其他区县|相比之"
)
_SYNTHESIZE = re.compile(
    r"综合|结合.*分析|多篇|两篇|多份|多文献|跨文献|共同方向|"
    r"整体判断|总体判断|综合判断|综合来看"
)
_RETRIEVE_CUES = re.compile(
    r"查一下|检索|查找|回读|原文|资料|最新|具体条款|第[一二三四五六七八九十\d]+条"
)
_HISTORY_CUES = re.compile(
    r"以上|上述|前面|前三轮|前几轮|前两轮|刚才|上一轮|之前|已有|现有|这些|该研究|本轮|"
    r"展开|详细说说|第[一二三四五六七八九十\d]+点|这一点|继续|重点"
)


@dataclass(frozen=True)
class ResearchIntent:
    primary: str
    secondary: tuple[str, ...] = ()
    use_history: bool = False
    need_report: bool = False
    compare_without_new_object: bool = False
    replace_scope: bool = False
    comparison_expansion: bool = False
    reason: str = ""

    @property
    def complex(self) -> bool:
        return self.primary in {"organize", "analyze", "compare", "synthesize", "summarize"}


def _scores(text: str) -> dict[str, int]:
    return {
        "summarize": len(_SUMMARIZE.findall(text)),
        "organize": len(_ORGANIZE.findall(text)),
        "analyze": len(_ANALYZE.findall(text)),
        "compare": len(_COMPARE.findall(text)),
        "synthesize": len(_SYNTHESIZE.findall(text)),
    }


def has_prior_facts(prior_turn: dict | None) -> bool:
    quote_meta = prior_turn.get("quote_verification") if isinstance(prior_turn, dict) else None
    return bool(isinstance(prior_turn, dict)
                and prior_turn.get("verified_facts")
                and prior_turn.get("evidence_refs")
                and isinstance(quote_meta, dict)
                and quote_meta.get("approved", 0) > 0
                and quote_meta.get("unverified") == 0)


def intent_payload(intent: ResearchIntent) -> dict:
    return {
        "primary": intent.primary,
        "secondary": list(intent.secondary),
        "use_history": intent.use_history,
        "need_report": intent.need_report,
        "compare_without_new_object": intent.compare_without_new_object,
        "replace_scope": intent.replace_scope,
        "comparison_expansion": intent.comparison_expansion,
        "reason": intent.reason,
    }


def requires_fresh_evidence(question: str) -> bool:
    """A new evidence need must bypass history-only report synthesis."""
    text = current_question(question)
    if re.search(r"只(?:总结|归纳|整理|使用).{0,12}(?:已有|上述|以上|历史)", text):
        return False
    if re.search(r"原文|引用|量化|指标|数据|具体|项目|产值|产量|"
                 r"目标|新(?:的|增)|另外|补充|查找|检索|"
                 r"多少|分别|对比|比较(?!优势)|相比|差异|区别|不同", text):
        return True
    return bool(extract_targets(text))


def classify_intent(question: str, prior_turn: dict | None = None) -> ResearchIntent:
    """Classify the *operation* requested by the latest user turn.

    The classifier is intentionally conservative for first-turn fact lookups:
    short factual questions stay on the cheap controlled-retrieval path.  When
    an explicit operation cue is present it can combine a primary operation with
    secondary ones (e.g. "总结并分析").
    """
    text = current_question(question).strip()
    if not text:
        return ResearchIntent("clarify", reason="empty_question")
    scores = _scores(text)
    areas = parse_areas(text)
    prior = has_prior_facts(prior_turn)
    history_cue = bool(_HISTORY_CUES.search(text))
    safe_history = prior and not requires_fresh_evidence(text)

    # Explicit new geography/period means retrieval is required; a history-only
    # summary must not silently borrow the old policy for a new place.
    explicit_new_scope = bool(areas)
    replace_scope = bool(re.search(
        r"改为|改成|只看|仅看|不要旧对象|只研究新对象", text
    ))
    comparison_expansion = bool(
        prior and _COMPARE.search(text) and not replace_scope
    )
    compare_without_new_object = (
        bool(_COMPARE.search(text)) and prior and not areas
        and bool(re.search(r"其他地区|其他区县|另一个地区|相比呢|换个地区", text))
    )

    # Multi-document comparison/synthesis cues take precedence over a generic
    # "归纳" cue; otherwise Q6 would be treated as a simple summary.
    if scores["compare"] or scores["synthesize"]:
        primary = "compare" if scores["compare"] else "synthesize"
        secondary = tuple(label for label in ("summarize", "analyze", "organize")
                          if scores[label])
        if (primary == "compare" and prior and not areas
                and bool(re.search(r"其他地区|其他区县|另一个地区|相比呢|换个地区", text))):
            return ResearchIntent(
                "compare", secondary=secondary, use_history=True,
                need_report=True, compare_without_new_object=True,
                replace_scope=replace_scope, comparison_expansion=comparison_expansion,
                reason="comparison_needs_new_object",
            )
        return ResearchIntent(
            primary, secondary=secondary,
            use_history=comparison_expansion,
            need_report=True, reason="multi_document_operation_cue",
            replace_scope=replace_scope,
            comparison_expansion=comparison_expansion,
        )

    # Summaries dominate because "总结以上并分析" must first reuse prior work.
    if scores["summarize"]:
        from .research_history import history_summary_matches_scope

        secondary = tuple(label for label in ("organize", "analyze")
                          if scores[label] or (label == "analyze" and "分析" in text))
        return ResearchIntent(
            "summarize", secondary=secondary,
            use_history=(history_summary_matches_scope(text, prior_turn)
                         or safe_history and (history_cue or not explicit_new_scope)),
            need_report=False,
            replace_scope=replace_scope,
            reason="explicit_summary_cue",
        )

    if compare_without_new_object:
        return ResearchIntent(
            "compare", secondary=("analyze",), use_history=prior,
            need_report=True, compare_without_new_object=True,
            replace_scope=replace_scope, comparison_expansion=comparison_expansion,
            reason="comparison_needs_new_object",
        )

    if scores["analyze"]:
        return ResearchIntent(
            "analyze", secondary=("organize",) if scores["organize"] else (),
            use_history=safe_history and history_cue,
            need_report=True, replace_scope=replace_scope,
            reason="explicit_analysis_cue",
        )

    if scores["organize"]:
        from .research_history import history_summary_matches_scope

        return ResearchIntent(
            "organize", secondary=("analyze",) if "分析" in text else (),
            use_history=(history_summary_matches_scope(text, prior_turn)
                         or safe_history and not explicit_new_scope and (
                             history_cue or "帮我" in text or "一下" in text)),
            need_report=True, replace_scope=replace_scope,
            reason="explicit_organization_cue",
        )

    # Topic-only policy queries request an organized overview, not a list of
    # isolated quotes with no actual answer. Keep this independent of subject:
    # firefighting, elderly care or other policy topics follow the same route.
    if (len(text) <= 60 and re.fullmatch(
            r"[\u4e00-\u9fffA-Za-z0-9“”‘’《》（）()·\s]{2,60}"
            r"(?:相关政策|政策梳理|政策汇总|政策概览)", text)):
        return ResearchIntent("organize", need_report=True,
                              replace_scope=replace_scope,
                              reason="topic_only_policy_overview")

    # "总结/概括" with no other cue and prior work is still a summary.
    if prior and history_cue and _SUMMARIZE.search(text):
        return ResearchIntent("summarize", use_history=True, reason="history_summary")
    if safe_history and re.search(r"展开讲讲|详细说说|换个角度|继续", text):
        return ResearchIntent("analyze", use_history=True, need_report=True,
                              reason="history_followup_analysis")
    return ResearchIntent("fact_lookup", use_history=False,
                          replace_scope=replace_scope,
                          reason="no_explicit_operation_cue")
