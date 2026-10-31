"""Evidence-only fallback for multi-aspect QA reports.

The model should compose its own report. When verification removes claims, we
must not publish its now-unsupported prose; rebuild a readable outline from
surviving evidence, with explicit gaps rather than invented conclusions.
"""
import re
from collections import OrderedDict

from .schemas import Claim

_CHINESE = "一二三四五六七八九十"
_CATEGORIES = (
    ("总体发展环境", ("总体", "整体环境", "外部环境", "发展环境")),
    ("主要发展机遇", ("机遇", "机会", "有利条件")),
    ("面临挑战", ("挑战", "困难", "发展瓶颈", "制约", "不足")),
)
_GROUP_ORDER = ("总体发展环境", "主要发展机遇", "面临挑战")


def needs_outline(question: str) -> bool:
    """Only require a report outline for genuinely multi-part QA, not a fact lookup."""
    text = question or ""
    pairs = (("机遇", "挑战"), ("优势", "劣势"), ("优势", "不足"),
             ("成效", "问题"), ("问题", "对策"), ("现状", "趋势"))
    return any(a in text and b in text for a, b in pairs)


def _number(index: int) -> str:
    if 1 <= index <= 10:
        return _CHINESE[index - 1]
    if 11 <= index <= 19:
        return "十" + _CHINESE[index - 11]
    if index == 20:
        return "二十"
    return str(index)


def _category(claim: Claim, question: str) -> str:
    dimension = (claim.dimension or "").strip()
    for label, aliases in _CATEGORIES:
        if any(alias in dimension for alias in aliases):
            return label
    # Only classify from the claim's explicit language; do not infer policy
    # opportunities from a generic mention of infrastructure or markets.
    text = claim.text
    for label, aliases in _CATEGORIES:
        if any(alias in text for alias in aliases):
            return label
    if dimension and len(dimension) <= 30 and "\n" not in dimension:
        return dimension
    return "其他已核验事实"


def is_canonical_outline(answer: str) -> bool:
    """Reject skipped / out-of-order numbering in generated reports.

    Validate heading lines only; factual numbers in ordinary sentences are not
    chapter numbering. Depth must follow 一、 > （一） > 1、 > （1）.
    """
    level_patterns = (
        re.compile(r"^([一二三四五六七八九十]+)、\s*\S"),
        re.compile(r"^（([一二三四五六七八九十]+)）\s*\S"),
        re.compile(r"^(\d{1,3})、\s*\S"),
        re.compile(r"^（(\d{1,3})）\s*\S"),
    )

    def numeral(value: str) -> int:
        if value.isdigit():
            return int(value)
        if value == "十":
            return 10
        if value.startswith("十") and len(value) == 2:
            return 10 + _CHINESE.index(value[1]) + 1
        if value.startswith("二十"):
            return 20 if value == "二十" else 20 + _CHINESE.index(value[-1]) + 1
        return _CHINESE.index(value) + 1 if value in _CHINESE else -1

    counters = [0, 0, 0, 0]
    headings = 0
    for original in (answer or "").splitlines():
        line = original.strip()
        if not line:
            continue
        if re.match(r"^(?:[-*•]\s+|#{1,6}\s+|\d{1,3}[.．)）]\s+|[（(]\d{1,3}[)）]\s+)", line):
            return False
        match = next(((level, m) for level, pattern in enumerate(level_patterns, start=1)
                      if (m := pattern.match(line)) is not None), None)
        if match is None:
            continue
        level, parsed = match
        if level > 1 and counters[level - 2] < 1:
            return False
        number = numeral(parsed.group(1))
        if number != counters[level - 1] + 1:
            return False
        counters[level - 1] = number
        for deeper in range(level, 4):
            counters[deeper] = 0
        if level == 1:
            headings += 1
    return headings >= 2


def _short_heading(text: str) -> str:
    """Use a substring of the verified claim, never manufacture a new fact."""
    cleaned = re.sub(
        r"^(?:发展)?(?:机遇|挑战)(?:之[一二三四五六七八九十])?(?:是|为|包括|在于)?\s*",
        "", text.strip(),
    ).lstrip("一二三四五六七八九十、：:， ")
    head = re.split(r"[，。；;：:\n]", cleaned, maxsplit=1)[0].strip()
    if not head:
        head = text.strip()
    return head[:36] + ("…" if len(head) > 36 else "")


def verified_qa_outline(claims: list[Claim], question: str) -> tuple[str, list[str]]:
    """Return a structured summary and dimensions without evidence.

    The summary only reports categories in *surviving* facts; it is not a
    substitute for a model-generated, evidence-linked interpretation.
    """
    if not claims:
        return "本轮缺少可核验的政策事实，暂不能完成分项归纳。", []

    groups: dict[str, list[Claim]] = OrderedDict()
    for claim in claims:
        groups.setdefault(_category(claim, question), []).append(claim)

    required = ["主要发展机遇", "面临挑战"] if "机遇" in question and "挑战" in question else []
    missing = [label for label in required if not groups.get(label)]
    overview = groups.pop("总体发展环境", []) if required else []
    if required:
        # The overall assessment belongs in the opening paragraph, not in a
        # third top-level section ahead of the two dimensions the user asked.
        ordered = list(required)
        ordered += [label for label in groups if label not in ordered]
        intro = ("根据本轮可追溯到政策原文的事实，相关发展环境涉及"
                 "发展机遇与面临挑战两个方面。以下仅归纳已有证据支持的内容。")
        if overview:
            intro += "\n" + "\n".join(claim.text.strip() for claim in overview)
    else:
        ordered = [label for label in _GROUP_ORDER if label in groups]
        ordered += [label for label in groups if label not in ordered]
        intro = "根据本轮已取得并通过原文核验的政策事实，可从以下方面梳理："

    lines = [intro]
    for i, label in enumerate(ordered, start=1):
        lines.extend(["", f"{_number(i)}、{label}"])
        items = groups.get(label, [])
        if not items:
            lines.append("本轮尚未取得足以支持这一方面的原文证据，暂不能作出判断。")
            continue
        for j, claim in enumerate(items, start=1):
            lines.append(f"（{_number(j)}）{_short_heading(claim.text)}")
            lines.append(claim.text.strip())

    covered = (["总体发展环境"] if overview else []) + [label for label in ordered if groups.get(label)]
    closing = "综上，本轮证据涉及" + "、".join(covered) + "。"
    if missing:
        closing += "关于" + "、".join(missing) + "尚需补充证据，不能视为完整回答。"
    else:
        closing += "以上为已核验材料范围内的归纳，不代表对全部相关情况的穷尽。"
    lines.extend(["", closing])
    return "\n".join(lines), missing
