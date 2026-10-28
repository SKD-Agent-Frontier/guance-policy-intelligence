"""S0: deterministic intent extraction; route remains the source of scope."""

import re
from dataclasses import dataclass, field

from .constants import MAX_CATEGORIES, MAX_LABEL_CHARS, MAX_QUERY_CHARS, MIN_LABEL_CHARS

PERIOD = re.compile(r"第?[一二三四五六七八九十百]+(?:个)?五(?:年规划)?")
CATEGORIES = re.compile(r"按(.{2,40}?)(?:分类|归类|划分|梳理)")
TYPE_WORDS = {
    "总体规划纲要": ("总体规划纲要", "总体规划", "规划纲要"),
    "专项规划": ("专项规划",),
    "政府工作报告": ("政府工作报告",),
    "规划文件": ("五年规划", "规划文件"),
}
NOISE = ("梳理", "整理", "归纳", "汇总", "分析", "查找", "列出", "请", "帮我",
         "一下", "相关", "有关", "领域", "方面", "政策", "文件", "文献", "的",
         "时期", "期间")


def _compact(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text).strip()


@dataclass
class OrganizeScope:
    topic: str
    areas: list[str]
    fyps: list[str]
    plantypes: list[str]
    categories: list[str]
    dimensions: list[str]
    targets: list[str] | None
    fallback_query: str
    question: str
    effective_area: str | None
    effective_fyp: str | None
    top_k: int
    post_filters: list[dict] = field(default_factory=list)
    unapplied: list[dict] = field(default_factory=list)


def parse_scope(task, ctx) -> OrganizeScope:
    route = ctx.route
    sub_query = route.sub_query or task.question
    areas, fyps = list(route.areas), list(route.fyps)
    stripped = PERIOD.sub(" ", sub_query)
    for area in areas:
        stripped = stripped.replace(area, " ")
    categories = []
    for match in CATEGORIES.finditer(task.question):
        for label in re.split(r"[、，,和及]", match.group(1)):
            label = label.strip()
            if MIN_LABEL_CHARS <= len(label) <= MAX_LABEL_CHARS and label not in categories:
                categories.append(label)
    categories = categories[:MAX_CATEGORIES]
    # A period name alone does not request the generic policy-type post-filter.
    plantypes = [name for name, words in TYPE_WORDS.items()
                 if any(word in stripped for word in words)]
    topic = CATEGORIES.sub(" ", stripped)
    for word in sorted({w for words in TYPE_WORDS.values() for w in words}, key=len, reverse=True):
        topic = re.sub(re.escape(word) + r"(?:中)?", " ", topic)
    for word in NOISE:
        topic = topic.replace(word, " ")
    topic = re.sub(r"\s+", "", _compact(topic)) or sub_query[:MAX_QUERY_CHARS]
    # Fixed targets bypass route-filter inheritance in BusinessServices.
    inherited = ctx.scope is None
    return OrganizeScope(
        topic=topic, areas=areas, fyps=fyps, plantypes=plantypes, categories=categories,
        dimensions=list(task.dimensions),
        targets=None if task.targets is None else list(task.targets),
        fallback_query=re.sub(r"\s+", " ", stripped).strip()[:MAX_QUERY_CHARS],
        question=task.question, effective_area=areas[0] if inherited and areas else None,
        effective_fyp=fyps[0] if inherited and fyps else None,
        top_k=ctx.settings.top_k_default,
    )
