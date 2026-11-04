"""Build a compact, data-independent checklist from a research question."""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from .research_targets import extract_targets
from .routing import current_question

_METRICS = {
    "enterprise_count": ("企业数量", ("企业数量", "企业数", "企业家数", "企业户数")),
    "annual_revenue": ("年销售收入", ("年销售收入", "销售收入", "营业收入", "年营收", "营收", "销售额")),
    "output_value": ("目标产值", ("目标产值", "工业总产值", "总产值", "产值")),
    "production_capacity": ("产能", ("生产能力", "年产能力", "加工能力", "产能")),
    "production_volume": ("产量", ("年产量", "产量", "产出")),
    "land_area": ("种植面积", ("种植面积", "播种面积", "养殖面积", "面积")),
    "investment": ("投资额", ("投资总额", "投资额", "投资规模")),
    "planning_year": ("规划年份", ("规划年份", "实施年份", "实施时间", "年份", "哪一年")),
}
_GENERIC_NUMERIC_CUES = re.compile(
    r"量化|数字指标|具体指标|指标|数据|多少|数量|规模目标|量化目标"
)
_VALUE_CHECK_CUES = re.compile(
    r"是否(?:不少于|不低于|至少|超过|高于|达到|低于|不足|不超过|等于|写了|提到|达到)"
    r"|(?:有没有|是不是写了|是否达标|是否真实|是否正确|是否属实)"
)
_SECTION_CUE = re.compile(
    r"(?:补充|增加|纳入|单列|补上)(?:对)?(?P<section>[^，。；！？?\n]{2,24}?)"
    r"(?:相关|有关)?(?:内容|方面|部分|板块|章节|一节|进总结|到总结|到报告|补充|$)"
)
_SECTION_AFTER_CUE = re.compile(
    r"(?:把|将)?(?P<section>[\u4e00-\u9fffA-Za-z0-9]{2,16}?)"
    r"(?:相关|有关)?补充(?:进|到|入|$)"
)
_SECTION_SUFFIX = re.compile(r"(?:相关|有关|方面|内容|部分|板块|章节|一节|分析|总结|补充)$")
_MEASUREMENT = re.compile(
    r"(?<!\d)(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<scale>万亿|亿|万|千)?\s*"
    r"(?P<unit>元|吨|家|户|个|亩|公顷|平方公里|平方米|人|人次|项|公里|千瓦|兆瓦|%|％)?"
)
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}\s*年")
_COMPARISON = re.compile(
    r"(?P<left>[^，。；！？?]{2,32}?)(?:与|和)"
    r"(?P<right>[^，。；！？?]{2,32}?)"
    r"(?:相比|相较|比较|有何(?:不同|差异)|(?:的)?(?:异同|差异|不同))"
)
_OVERVIEW_CUES = re.compile(
    r"总体|概括|概览|主要(?:部署|方面|任务|目标|内容)|涉及哪些|包括哪些|有哪些方面|"
    r"哪些(?:变化|特点|表现|进展|成就)|(?:进展|变化|表现|特点)有哪些"
)
_OUTLINE_PARENT_CUES = ("重点任务", "主要任务", "发展目标", "主要目标", "重点部署",
                        "发展重点", "总体目标", "主要举措", "重点领域", "目标安排",
                        "经济社会发展目标", "主要发展目标", "主要成就", "发展成就",
                        "发展进展", "业务进展", "发展变化", "发展特点", "发展表现")
_OUTLINE_EXCLUDED_CUES = ("实施保障", "组织保障", "规划保障", "附则", "目录")
_METRIC_UNITS = {
    "enterprise_count": {"count"},
    "annual_revenue": {"currency"},
    "output_value": {"currency"},
    "production_capacity": {"mass"},
    "production_volume": {"mass"},
    "land_area": {"area"},
    "investment": {"currency"},
    "planning_year": {"year"},
}


def _metric_occurrences(question: str) -> list[tuple[int, str, str]]:
    matches = []
    for metric_id, (label, aliases) in _METRICS.items():
        positions = [
            (match.start(), match.end())
            for alias in aliases
            for match in re.finditer(re.escape(alias), question)
        ]
        if positions:
            start, end = min(positions)
            matches.append((start, metric_id, label))
    return sorted(matches)


def _measurement_value(text: str, metric_id: str) -> tuple[str, str] | None:
    if metric_id == "planning_year":
        match = _YEAR.search(text)
        if not match:
            return None
        context = text[max(0, match.start() - 12):match.end() + 12]
        return ((match.group(0).replace(" ", ""), "year")
                if re.search(r"规划|实施|启动|完成|建成|达到", context) else None)
    for match in _MEASUREMENT.finditer(text):
        raw = match.group("number").replace(",", "")
        scale = match.group("scale") or ""
        unit = match.group("unit") or ""
        if not unit:
            continue
        if scale in {"亿", "万亿"} and unit == "元":
            canonical_unit, multiplier = "currency", Decimal("100000000" if scale == "亿" else "1000000000000")
        elif scale == "万" and unit == "元":
            canonical_unit, multiplier = "currency", Decimal("10000")
        elif unit == "元":
            canonical_unit, multiplier = "currency", Decimal("1")
        elif scale == "万" and unit == "吨":
            canonical_unit, multiplier = "mass", Decimal("10000")
        elif unit == "吨":
            canonical_unit, multiplier = "mass", Decimal("1")
        elif unit in {"家", "户", "个", "项"}:
            canonical_unit, multiplier = "count", Decimal("10000") if scale == "万" else Decimal("1")
        elif unit in {"亩", "公顷", "平方公里", "平方米"}:
            canonical_unit = "area"
            multiplier = {"亩": Decimal("10000") if scale == "万" else Decimal("1"),
                          "公顷": Decimal("150000") if scale == "万" else Decimal("15"),
                          "平方公里": Decimal("15000"), "平方米": Decimal("0.0015")}[unit]
        elif unit in {"%", "％"}:
            canonical_unit, multiplier = "percent", Decimal("1")
        else:
            canonical_unit, multiplier = unit, Decimal("1")
        if canonical_unit not in _METRIC_UNITS.get(metric_id, {canonical_unit}):
            continue
        try:
            normalized = Decimal(raw) * multiplier
        except InvalidOperation:
            continue
        return (str(normalized.normalize()), canonical_unit)
    return None


def _expected_value(question: str, start: int, end: int, metric_id: str) -> tuple[str, str] | None:
    # Record a nearby user-supplied candidate/threshold, not an expected source
    # answer. A planning year elsewhere must not become this metric's value.
    window = question[max(0, start - 20):min(len(question), end + 32)]
    return _measurement_value(window, metric_id)


def _value_operator(question: str) -> str:
    if re.search(r"不少于|不低于|至少", question):
        return "gte"
    if re.search(r"不超过|至多|以内|低于|不足|少于", question):
        return "lte"
    if re.search(r"超过|高于|大于", question):
        return "gt"
    if re.search(r"是否写了|有没有|是否提到|是否载明|是否列明", question):
        return "exists"
    if re.search(r"是否等于|是否为|是否真实|是否正确|是否属实|核实|核验|查证|确认", question):
        return "equal"
    return "compare"


def _enterprise_count_value(text: str) -> tuple[str, str] | None:
    patterns = (
        r"(?P<value>\d+(?:\.\d+)?\s*(?:万)?\s*(?:家|户))企业",
        r"(?:工业)?企业(?:数量|数目|数|总数)?[^，。；]{0,8}?"
        r"(?P<value>\d+(?:\.\d+)?\s*(?:万)?\s*(?:家|户))",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _measurement_value(match.group("value"), "enterprise_count")
    return None


def _metric_value_window(text: str, metric_id: str, match: re.Match) -> str:
    """Keep a value attached to its local clause and stop at another metric."""
    boundaries = "，,。；;！？?!\n"
    start = max((text.rfind(char, 0, match.start()) for char in boundaries), default=-1) + 1
    ends = [position for char in boundaries
            if (position := text.find(char, match.end())) >= 0]
    end = min(ends, default=len(text))
    for other_id, (_, aliases) in _METRICS.items():
        if other_id == metric_id:
            continue
        for alias in aliases:
            for other in re.finditer(re.escape(alias), text[start:end]):
                absolute_start, absolute_end = start + other.start(), start + other.end()
                if absolute_end <= match.start():
                    start = max(start, absolute_end)
                elif absolute_start >= match.end():
                    end = min(end, absolute_start)
    return text[start:end]


def _metric_value_is_negated(window: str, metric_end: int) -> bool:
    value_positions = [match.start() for match in _MEASUREMENT.finditer(window[metric_end:])
                       if match.group("unit")]
    if not value_positions:
        return False
    relation = window[metric_end:metric_end + value_positions[0]]
    return bool(re.search(
        r"未公开|未披露|未明确|未提出|未设定|未制定|未达到|尚未|暂无|无目标|没有(?:公布|明确|提出)",
        relation,
    ))


def parse_requirements(question: str) -> list[dict]:
    """Return deterministic requirement units; these are needs, never evidence."""
    text = current_question(question or "").strip()
    subjects = list(extract_targets(text))
    metrics = _metric_occurrences(text)
    requirements: list[dict] = []

    if metrics:
        subject_positions = sorted(
            (text.find(subject), subject) for subject in subjects if subject in text
        )
        if len(subjects) == len(metrics) and len(subjects) > 1:
            assignments = list(zip(metrics, subjects))
        elif len(subjects) > 1 and len(metrics) == 1 and "分别" in text:
            assignments = [(metric, subject) for subject in subjects for metric in metrics]
        else:
            assignments = []
            for metric in metrics:
                start, _, _ = metric
                preceding = [subject for position, subject in subject_positions if position <= start]
                subject = preceding[-1] if preceding else (subjects[0] if len(subjects) == 1 else None)
                assignments.append((metric, subject))
        for metric_index, ((start, metric_id, label), subject) in enumerate(assignments, start=1):
            expected = _expected_value(text, start, start + len(label), metric_id)
            requirements.append({
                "id": f"metric:{metric_index}:{metric_id}:{subject or 'topic'}",
                "kind": "metric",
                "label": label,
                "subject": subject,
                "metric": metric_id,
                "expected_value": expected,
            })
        if (_VALUE_CHECK_CUES.search(text)
                or (re.search(r"核实|核验|查证|确认", text)
                    and any(item.get("expected_value") for item in requirements))):
            for metric_index, ((_, metric_id, label), subject) in enumerate(assignments, start=1):
                expected = requirements[metric_index - 1].get("expected_value")
                requirements.append({
                    "id": f"value_check:{metric_index}:{metric_id}:{subject or 'topic'}",
                    "kind": "value_check",
                    "label": f"{subject or ''}{label}数值核对",
                    "subject": subject,
                    "metric": metric_id,
                    "expected_value": expected,
                    "operator": _value_operator(text),
                    "phase": "synthesis",
                    "actionable": False,
                })

    if subjects and not metrics:
        numeric_requested = bool(_GENERIC_NUMERIC_CUES.search(text))
        for index, subject in enumerate(subjects, start=1):
            label = f"{subject}量化指标" if numeric_requested else subject
            requirements.append({
                "id": f"{'numeric' if numeric_requested else 'subject'}:{index}",
                "kind": "numeric_fact" if numeric_requested else "subject_fact",
                "label": label,
                "subject": subject,
                "metric": None,
                "expected_value": None,
            })

    section_matches = [match.group("section") for match in _SECTION_CUE.finditer(text)]
    section_matches.extend(match.group("section") for match in _SECTION_AFTER_CUE.finditer(text))
    named_sections = []
    if re.search(r"分.{0,100}(?:部分|板块|章节)", text):
        named_sections = re.findall(r"['‘“\"]([^'’”\"]{2,24})['’”\"]", text)
        section_matches = named_sections
    for raw_section in section_matches:
        section = raw_section.strip(" ，、:：")
        section = re.sub(r"^(?:请|再|把|将)+", "", section)
        while True:
            shortened = _SECTION_SUFFIX.sub("", section).strip(" ，、:：")
            if shortened == section:
                break
            section = shortened
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]{2,24}", section) and not any(item.get("kind") == "output_section"
                                         and item["label"] == section for item in requirements):
            if not any(item.get("kind") == "subject_fact" and item.get("subject") == section
                       for item in requirements):
                requirements.append({
                    "id": f"section_topic:{len(requirements) + 1}:{section}",
                    "kind": "subject_fact", "label": section,
                    "subject": section, "metric": None, "expected_value": None,
                })
            requirements.append({
                "id": f"section:{len(requirements) + 1}:{section}",
                "kind": "output_section", "label": section,
                "subject": None, "metric": None, "expected_value": None,
                "phase": "synthesis", "actionable": False,
            })

    from .routing import parse_fyps

    content_periods = list(parse_fyps(text))
    relation = comparison_sides(text)
    if relation:
        left, right = relation
        requirements.append({
            "id": f"comparison:{left}:{right}",
            "kind": "comparison",
            "label": f"{left}与{right}比较",
            "subject": None,
            "metric": None,
            "left": left,
            "right": right,
            "expected_value": None,
        })
    for unit in requirements:
        unit.setdefault("phase", "evidence")
        unit.setdefault("actionable", unit["kind"] != "output_section")
        if len(content_periods) == 1 and unit["kind"] not in {"output_section", "analysis"}:
            unit["content_period"] = content_periods[0]
    return requirements


def normalize_requirements(question: str, planned=None) -> list[dict]:
    """Merge a pre-answer semantic checklist with deterministic requirements.

    The planner may add concepts outside the rule vocabulary, but it cannot
    remove explicit facts, metrics, comparisons, or requested sections found by
    the deterministic parser. A missing/invalid plan leaves a question-level
    unresolved unit for callers to retain instead of silently accepting an
    empty checklist.
    """
    deterministic = parse_requirements(question)
    from .routing import parse_fyps

    content_periods = list(parse_fyps(question or ""))
    allowed_content_periods = set(content_periods)
    if isinstance(planned, dict):
        planned = planned.get("requirements")
    planned_units = []
    invalid_plan = False
    if isinstance(planned, list):
        allowed = {"subject_fact", "metric", "numeric_fact", "comparison", "value_check",
                   "output_section", "analysis", "dimension", "action",
                   "unresolved_requirement", "source_citation"}
        for item in planned:
            if not isinstance(item, dict):
                invalid_plan = True
                continue
            kind = item.get("kind")
            label = item.get("label")
            string_fields = ("subject", "metric", "left", "right", "content_period")
            phase, operator = item.get("phase"), item.get("operator")
            if (not isinstance(kind, str) or kind not in allowed
                    or not isinstance(label, str) or not label.strip()
                    or any(item.get(field) is not None
                           and not isinstance(item[field], str) for field in string_fields)
                    or (phase is not None and (not isinstance(phase, str)
                                               or phase not in {"evidence", "synthesis"}))
                    or (operator is not None and (not isinstance(operator, str)
                                                  or operator not in {"gte", "gt", "lte", "lt", "equal", "exists", "compare"}))
                    or ("actionable" in item and type(item["actionable"]) is not bool)
                    or (kind == "comparison" and not all(
                        isinstance(item.get(side), str) and item[side].strip()
                        for side in ("left", "right")))):
                invalid_plan = True
                continue
            if kind == "metric" and not item.get("metric"):
                kind = "numeric_fact"
            locator_label = bool(re.search(
                r"定位依据|来源定位|文献定位|对应文献|文献身份|规划来源|来源出处|"
                r"原文出处|引用出处", label))
            if locator_label or (kind == "output_section"
                                 and re.search(r"引用|出处|原文标注|来源标注", label)):
                kind = "source_citation"
            synthesis = kind in {"analysis", "output_section", "value_check"}
            subject = item["subject"].strip()[:160] if item.get("subject") else None
            if subject and re.search(
                    r"(?:规划纲要|发展规划|规划|计划|方案)(?:中|的|所列|第)|"
                    r"(?:第[一二三四五六七八九十0-9①②③④⑤]+项|文献中的|原文中的)",
                    subject):
                # Document and section identity is already represented by
                # trusted source refs. It is not the factual subject of a
                # request such as an economic facet nested in that document.
                subject = None
            if kind == "output_section" and subject and re.search(
                    r"(?:规划纲要|发展规划|规划|计划|方案)$", subject):
                subject = None
            normalized = {
                "kind": kind,
                "label": label.strip()[:120],
                "subject": subject,
                "metric": item["metric"].strip()[:120] if item.get("metric") else None,
                "expected_value": None,
                "phase": "synthesis" if synthesis else "evidence",
                "actionable": (not synthesis and kind not in {
                    "unresolved_requirement", "source_citation"}),
            }
            if item.get("content_period") in allowed_content_periods:
                normalized["content_period"] = item["content_period"]
            if operator is not None:
                normalized["operator"] = operator
            for field in ("left", "right"):
                if item.get(field):
                    normalized[field] = str(item[field]).strip()
            if "expected_value" in item and item["expected_value"] is not None:
                value = item["expected_value"]
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    normalized["expected_value"] = [str(value[0]), str(value[1])]
                else:
                    normalized["expected_value"] = str(value)
            planned_units.append(normalized)

    def identity(item):
        return (item["kind"], item["label"], item.get("subject"), item.get("metric"),
                item.get("left"), item.get("right"),
                tuple(item.get("expected_value") or ()), item.get("phase", "evidence"),
                item.get("content_period"))

    def canonical_id(item):
        key = "|".join(str(value or "") for value in identity(item))
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        return f"planned:{digest}"

    units = list(deterministic)

    known = {identity(item) for item in units}
    for item in planned_units:
        key = identity(item)
        if key not in known:
            units.append(item)
            known.add(key)

    # A planner may use the full question as a conservative fallback while
    # also identifying its concrete requirements. Keep that fallback only
    # when it is the sole available unit; otherwise it duplicates all gaps.
    substantive = any(item["kind"] != "unresolved_requirement" for item in units)
    if substantive:
        whole_question = current_question(question).strip()
        units = [item for item in units if not (
            item["kind"] == "unresolved_requirement"
            and item["label"].strip() == whole_question)]

    if (invalid_plan or not units) and question.strip():
        units.append({
            "kind": "unresolved_requirement", "label": current_question(question)[:120],
            "subject": None, "metric": None, "expected_value": None,
            "phase": "evidence", "actionable": False,
        })
    for unit in units:
        if (len(content_periods) == 1
                and unit["kind"] not in {"output_section", "analysis"}):
            unit.setdefault("content_period", content_periods[0])
        if unit not in deterministic:
            unit["id"] = (f"dimension:{unit['label']}"
                          if unit.get("kind") == "dimension"
                          else canonical_id(unit))

    # Every comparison needs independently retrievable evidence for each side.
    # The relation itself is validated later as synthesis, after both sides
    # have quote-verified support.
    side_units = []
    for relation in [unit for unit in units if unit.get("kind") == "comparison"]:
        for side_index, (field, side_name) in enumerate(
                (("left", "left"), ("right", "right"))):
            side = relation.get(field)
            if not isinstance(side, str) or not side.strip():
                continue
            side_periods = list(parse_fyps(side))
            if len(side_periods) == 1:
                side_period = side_periods[0]
            elif len(content_periods) == 2:
                side_period = content_periods[side_index]
            else:
                side_period = None
            existing = next((unit for unit in units
                             if unit.get("kind") == "subject_fact"
                             and unit.get("subject") == side
                             and unit.get("content_period") == side_period), None)
            if existing is None:
                existing = {
                    "kind": "subject_fact", "label": side,
                    "subject": side, "metric": None,
                    "expected_value": None, "phase": "evidence",
                    "actionable": True,
                }
                if side_period is not None:
                    existing["content_period"] = side_period
                existing["id"] = canonical_id(existing)
                units.append(existing)
                side_units.append(existing)
            relation_ids = set(existing.get("comparison_relation_ids") or [])
            relation_ids.add(relation["id"])
            existing["comparison_relation_ids"] = sorted(relation_ids)
            relation[f"{side_name}_requirement_id"] = existing["id"]
        if (relation.get("left") == relation.get("right")
                and not (relation.get("left_requirement_id")
                         != relation.get("right_requirement_id")
                         and len(content_periods) == 2)):
            unresolved = {
                "kind": "unresolved_requirement",
                "label": f"无法区分比较时期：{relation.get('label', '')}",
                "subject": None, "metric": None, "expected_value": None,
                "phase": "evidence", "actionable": False,
            }
            if not any(identity(unit) == identity(unresolved) for unit in units):
                units.append(unresolved)

    for unit in units:
        if unit in deterministic:
            continue
        if unit.get("kind") == "dimension":
            unit["id"] = f"dimension:{unit['label']}"
        else:
            unit["id"] = canonical_id(unit)

    if any(unit.get("kind") == "comparison" for unit in units):
        units.sort(key=lambda unit: (unit.get("kind") == "comparison",))
    if len(content_periods) != 1:
        for relation in (unit for unit in units if unit.get("kind") == "comparison"):
            for side_index, side_name in enumerate(("left", "right")):
                side_id = relation.get(f"{side_name}_requirement_id")
                side_unit = next((unit for unit in units if unit.get("id") == side_id), None)
                if side_unit is not None:
                    side_periods = list(parse_fyps(relation.get(side_name) or ""))
                    if len(side_periods) == 1:
                        side_unit["content_period"] = side_periods[0]
                    elif len(content_periods) == 2:
                        side_unit.setdefault("content_period", content_periods[side_index])
    return units


def comparison_sides(question: str) -> tuple[str, str] | None:
    """Extract a named pair around an explicit comparison relation, if present."""
    text = current_question(question or "")
    from .routing import parse_areas

    areas = parse_areas(text)
    if len(areas) >= 2 and re.search(r"比较|对比|相比|相较|异同|差异|不同", text):
        return areas[0], areas[1]
    match = _COMPARISON.search(text)
    if not match:
        return None
    sides = []
    for name in ("left", "right"):
        value = match.group(name).strip(" ：:，。；、 ")
        value = re.sub(r"^(?:比较|对比|围绕|针对|关于|在)", "", value)
        value = re.sub(r"^.*?(?:规划中|文本中|文件中|政策中|方案中)", "", value)
        value = re.sub(r"^(?:这些|上述|前述|这类|该|这个)", "", value)
        value = re.sub(r"(?:规划)?的?(?:扶持|支持)?(?:方式|措施)$", "", value)
        if len(value) < 2:
            return None
        sides.append(value)
    return tuple(sides)


def select_overview_facets(question: str, sections: list[dict]) -> list[dict]:
    """Select relevant main-task catalog headings for a broad overview.

    The document's trusted section directory supplies the facets; claims and
    model answers are never used to define this checklist.
    """
    if not _OVERVIEW_CUES.search(current_question(question or "")):
        return []
    selected = []
    seen = set()
    for section in sections or []:
        if not isinstance(section, dict) or type(section.get("idx")) is not int:
            continue
        path = str(section.get("path") or "").strip()
        parts = [part.strip() for part in re.split(r"\s*/\s*|\s*[>＞]\s*", path) if part.strip()]
        if not parts:
            continue
        parent_text = " ".join(parts[:-1])
        leaf = re.sub(r"^[（(]?[一二三四五六七八九十0-9]+[）)、.．、]?\s*", "", parts[-1]).strip()
        if len(leaf) < 2:
            continue
        if any(cue in parent_text for cue in _OUTLINE_EXCLUDED_CUES):
            continue
        if not any(cue in parent_text for cue in _OUTLINE_PARENT_CUES):
            if not any(cue in leaf for cue in _OUTLINE_PARENT_CUES):
                continue
        key = (leaf, path)
        if key in seen:
            continue
        seen.add(key)
        selected.append({"section_idx": section["idx"], "label": leaf, "section_path": path})
    if len(selected) > 24:
        remaining = len(selected) - 23
        return selected[:23] + [{
            "section_idx": None,
            "label": f"目录中尚有{remaining}个相关方面超过本轮研究边界",
            "section_path": "",
            "overflow": True,
            "remaining_count": remaining,
        }]
    return selected


def is_broad_outline_parent(label: str) -> bool:
    """Identify generic grouping headings that may hide enumerated subtopics."""
    leaf = re.sub(r"^[（(]?[一二三四五六七八九十0-9]+[）)、.．、.]?\s*", "",
                  str(label or "").strip())
    return len(leaf) <= 28 and bool(re.search(
        r"(?:目标|任务|重点|部署|安排|方面|内容|工作)$", leaf))


def expand_outline_subheadings(text: str) -> list[str]:
    """Extract explicit subheading labels from a trusted outline section body."""
    source = str(text or "")
    labels = []
    for match in re.finditer(r"(?:——|—{2,})\s*([^——\n]{2,64})", source):
        label = re.split(r"[：:；;。！？!?\n]", match.group(1), maxsplit=1)[0]
        label = re.sub(r"\s+", " ", label).strip(" ，、-— ")
        if 2 <= len(label) <= 48 and label not in labels:
            labels.append(label)
    if not labels:
        for match in re.finditer(
                r"(?:^|[\n。；])\s*(?:[（(][一二三四五六七八九十]+[）)]|"
                r"[一二三四五六七八九十]+、|\d+[、.．])\s*([^：:；;。！？!?\n]{2,48})",
                source):
            label = re.sub(r"\s+", " ", match.group(1)).strip(" ，、-— ")
            if 2 <= len(label) <= 48 and label not in labels:
                labels.append(label)
    if len(labels) > 24:
        return labels[:23] + [f"正文总纲还有{len(labels) - 23}个子方面超过本轮边界"]
    return labels


def supports_requirement(requirement: dict, text: str) -> bool:
    """Check claim text against the requested subject and metric as one unit."""
    subject = requirement.get("subject")
    if subject:
        from .research_targets import mentions_subject

        if not mentions_subject(text, subject):
            return False

    metric_id = requirement.get("metric")
    if requirement["kind"] == "comparison":
        return all(side in text for side in (requirement["left"], requirement["right"]))
    if not _content_period_matches(requirement, text):
        return False
    if requirement["kind"] == "numeric_fact":
        return bool(any(_measurement_value(text, metric_id)
                        for metric_id in _METRICS if metric_id != "planning_year"))
    if requirement["kind"] == "subject_fact":
        return bool(subject)
    if not metric_id or metric_id not in _METRICS:
        return False
    if metric_id == "enterprise_count":
        return _enterprise_count_value(text) is not None
    if metric_id == "planning_year":
        return _measurement_value(text, metric_id) is not None
    aliases = _METRICS[metric_id][1]
    metric_matches = [
        match for alias in aliases for match in re.finditer(re.escape(alias), text)
    ]
    if not metric_matches:
        return False

    for match in metric_matches:
        window = _metric_value_window(text, metric_id, match)
        local_start = window.find(match.group())
        local_end = local_start + len(match.group())
        if _metric_value_is_negated(window, local_end):
            continue
        value = (_enterprise_count_value(window) if metric_id == "enterprise_count"
                 else _measurement_value(window, metric_id))
        if value is not None:
            return True
    return False


def _content_period_matches(requirement: dict, text: str) -> bool:
    content_period = requirement.get("content_period")
    if not content_period:
        return True
    from .routing import parse_fyps

    claim_text, marker, section_text = text.partition("〔章节〕")
    if marker and content_period in parse_fyps(section_text):
        return True
    metric = requirement.get("metric")
    metric_aliases = (_METRICS.get(metric, (None, ()))[1] if metric else ())
    subject = str(requirement.get("subject") or "")
    for sentence in re.split(r"[。；;！？!?\n]", claim_text):
        if content_period not in parse_fyps(sentence):
            continue
        if metric_aliases and not any(alias in sentence for alias in metric_aliases):
            continue
        if subject and subject not in sentence:
            from .research_targets import mentions_subject

            if not mentions_subject(sentence, subject):
                continue
        return True
    return False


def claim_matches_requirement_source(requirement: dict, claim, refs_by_chunk: dict,
                                     source_bindings: dict) -> bool:
    """Enforce a locally frozen catalog-section binding when present."""
    binding = source_bindings.get(requirement.get("id")) if isinstance(source_bindings, dict) else None
    if not isinstance(binding, dict):
        return True
    expected_planid = str(binding.get("planid") or "")
    expected_path = re.sub(r"\s+", " ", str(binding.get("section_path") or "")).strip()
    for chunk_id in getattr(claim, "chunk_ids", []) or []:
        ref = refs_by_chunk.get(chunk_id)
        if ref is None or str(getattr(ref, "planid", "")) != expected_planid:
            continue
        path = re.sub(r"\s+", " ", str(getattr(ref, "section_path", "") or "")).strip()
        if path == expected_path or path.startswith(expected_path + " / "):
            return True
    return False


def catalog_facet_matches_claim(requirement: dict, text: str,
                                source_bindings: dict) -> bool:
    """Require a catalog facet's own subject in the claim, not just its parent."""
    if requirement.get("id") not in source_bindings:
        return True
    subject = str(requirement.get("subject") or "").strip()
    if not subject:
        subject = re.sub(r"^目录方面：", "", str(requirement.get("label") or "")).strip()
    if not subject:
        return False
    from .research_targets import mentions_subject

    return mentions_subject(text, subject)


def supports_metric_value(requirement: dict, text: str) -> bool:
    """Validate a semantically mapped metric using value type and normalized units."""
    subject = requirement.get("subject")
    if subject:
        from .research_targets import mentions_subject

        if not mentions_subject(text, str(subject)):
            return False
    metric_id = requirement.get("metric")
    if not metric_id or metric_id not in _METRICS:
        # Open-vocabulary metrics need an explicit metric phrase in the cited
        # fact; a same-subject quantity of another kind is not enough.
        label = str(requirement.get("label") or "").strip()
        return bool(metric_id and label and label in text and supports_numeric_value(text))
    if not supports_requirement(requirement, text):
        return False
    aliases = _METRICS[metric_id][1]
    for alias in aliases:
        for match in re.finditer(re.escape(alias), text):
            window = _metric_value_window(text, metric_id, match)
            local_end = window.find(match.group()) + len(match.group())
            if _metric_value_is_negated(window, local_end):
                continue
            value = (_enterprise_count_value(window) if metric_id == "enterprise_count"
                     else _measurement_value(window, metric_id))
            if value is not None:
                return True
    return False


def supports_numeric_value(text: str) -> bool:
    """Require a typed, non-year measure for a generic numeric target."""
    if any(_measurement_value(text, metric_id)
           for metric_id in _METRICS if metric_id != "planning_year"):
        return True
    return bool(re.search(
        r"[零〇一二两三四五六七八九十百千万亿点\d,.]+\s*"
        r"(?:头|张|床|名|座|台|辆|人均|覆盖率|%|％)", text
    ))


def claim_fingerprint(claims) -> str:
    """Bind semantic support indexes to the exact ordered surviving claims."""
    payload = [{
        "text": str(getattr(claim, "text", "") or ""),
        "chunk_ids": list(getattr(claim, "chunk_ids", []) or []),
        "quotes": list(getattr(claim, "quotes", []) or []),
    } for claim in claims or []]
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def requirements_fingerprint(requirements) -> str:
    """Bind semantic support to the frozen, ordered requirement definitions."""
    canonical = json.dumps(
        list(requirements or []), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
