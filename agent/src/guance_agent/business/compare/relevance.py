"""Conservative topic-fit guards for explicit cross-region five-year comparisons.

A lexical title match is not proof of all details, but an unrelated policy must
never become an automatically trusted subject document or a comparison fact.
For broader/multi-topic policy research, the user must confirm target documents.
"""
import re

_PERIOD = r"(?:十|十一|十二|十三|十四|十五)五(?:时期|规划|期间)?"
_FOCUS = re.compile(
    _PERIOD + r"(?P<topic>[\u4e00-\u9fffA-Za-z0-9]{2,30}?)"
    r"(?:发展目标|量化目标|政策工具|支持措施|适用对象|产业布局|"
    r"主要任务|发展方向|实施路径|政策重点)(?:的|有何|异同|差异|区别|$)"
)
_GOAL = re.compile(r"目标|到20\d{2}|力争|预计|预期|达到|增加值|占比|规模|增长|提升|翻番|比重|建成|超过")
_TOOL = re.compile(r"支持|措施|资金|扶持|补贴|激励|投入|保障|引导|鼓励|推动|建设|完善")


def explicit_comparison_topic(question: str) -> str | None:
    """Only gate an unambiguous subject after a named planning period and dimension."""
    match = _FOCUS.search(re.sub(r"[\s“”\"']", "", question))
    return match.group("topic") if match else None


def topic_mismatched_objects(objects: list[dict], topic: str, targets: list[str]) -> list[str]:
    """Directory identity is evidence of subject *eligibility*, not research completion."""
    index = {item.get("planid"): item for item in objects}
    return [
        f"{pid}《{(index.get(pid) or {}).get('title') or '标题未取得'}》"
        for pid in targets
        if topic not in ((index.get(pid) or {}).get("title") or "")
    ]


def evidence_on_topic(unit, dimension: str, topic: str, title: str) -> bool:
    """Keep dimension-specific evidence under a topic-matched policy's remit."""
    if topic not in title:
        return False
    # A topical title alone does not validate every clause under "发展目标":
    # e.g. fire casualty figures in an incidental chapter are still off topic.
    # When local source context carries no subject evidence, fail closed.
    source = (unit.text or "") + "\n" + (unit.section_path or "")
    if topic not in source:
        return False
    if "目标" in dimension:
        return bool(_GOAL.search(source))
    if any(part in dimension for part in ("支持", "政策工具", "措施")):
        return bool(_TOOL.search(source))
    return True
