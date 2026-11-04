"""自然语言研究对象发现：只从当前冻结目录选择真实文献，不生成编号。

本层负责发现而不生成政策判断；业务模型只接收经此层定位的对象。
不做正文关键词的非确定性跨主题替代；模糊时返回文字澄清。
"""
from __future__ import annotations

import re
from typing import Literal

from guance_agent.routing import current_question, parse_fyps
from pydantic import BaseModel, ConfigDict, Field

from .compare_resolution import _DIMENSIONS, _PROVINCES, resolve_compare
from .compare_resolution import _areas as provinces_in_text
from .policy_catalog import PolicyCatalogService, PolicyRecord, normalize_fyp

Mode = Literal["qa", "extract", "organize", "compare", "compare_plans"]


class ResearchResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Mode
    question: str = Field(min_length=1, max_length=4000)


PLANIDS = re.compile(r"planid\s*[:：=]?\s*([A-Za-z0-9_-]{1,64})", re.IGNORECASE)
TITLES = re.compile(r"《([^《》]{3,120})》")
ORDINAL = re.compile(r"(?:十一|十二|十三|十四|十五)五")
EXPLICIT_PERIOD = re.compile(r"第(?:十[一二三四五]|十)个五年(?:规划|计划)")
HISTORIC_SHORTHAND = re.compile(r"(?<!五)十五[’'\"“”‘’]?\s*(?:规划|计划)")
COLLECTIVE = re.compile(r"(?:其他|其它|其余|全省|省内|各)(?:地级市|地市|城市|市)")
GENERAL_THEME = re.compile(r"产业结构|产业布局|产业升级|三次产业|产业优化|经济结构")
GENERAL_PLAN = re.compile(r"国民经济和社会发展|五年(?:规划|计划)纲要|总体规划纲要")
TITLE_REQUIRED_THEMES = frozenset({
    "数字经济", "人工智能", "养老", "能源", "科技创新", "人才", "生态环境",
    "服务业", "工业", "农业", "教育", "医疗", "交通", "财政", "金融",
})


def _period(question: str) -> str | None:
    question = current_question(question)

    def canonical(value: str) -> str:
        normalized = normalize_fyp(value)
        if normalized in {"第十个五年规划", "第十个五年计划"}:
            return "第十个五年计划"
        return normalized

    route_periods = list(dict.fromkeys(parse_fyps(question)))
    if len(route_periods) == 1:
        return canonical(route_periods[0])
    if len(route_periods) > 1:
        return None
    explicit = EXPLICIT_PERIOD.findall(question)
    names = ORDINAL.findall(question)
    values = [canonical(value) for value in explicit + names]
    if HISTORIC_SHORTHAND.search(question):
        values.append(canonical("第十个五年规划"))
    # The shared route parser also recognizes quoted shorthand such as
    # “十五”期间, which is a historical plan rather than 十五五.
    unique = list(dict.fromkeys(values))
    return unique[0] if len(unique) == 1 else None


def _dimensions(question: str) -> list[str]:
    found = [d for d in _DIMENSIONS if d in question]
    if GENERAL_THEME.search(question):
        for d in ("产业结构", "三次产业比重", "主导产业", "战略性新兴产业", "现代服务业"):
            if d not in found:
                found.append(d)
    return found[:8]


def _period_matches(record: PolicyRecord, period: str | None) -> bool:
    if not period:
        return True

    def period_key(value: str | None) -> str | None:
        normalized = normalize_fyp(value) if value else None
        if normalized in {"第十个五年规划", "第十个五年计划"}:
            return "第十个五年计划"
        return normalized

    wanted = period_key(period)
    meta_period = period_key(record.fyp)
    if meta_period:
        return wanted is not None and meta_period == wanted
    title_periods = {period_key(value) for value in parse_fyps(record.title or "")}
    return wanted is not None and wanted in title_periods


def _score(record: PolicyRecord, question: str, period: str | None) -> int:
    if not _period_matches(record, period):
        return -1
    title = record.title or ""
    if not title or not record.body.strip():
        return -1
    score = 0
    if GENERAL_PLAN.search(title):
        score += 45
    if "总体规划纲要" in (record.plantype_label or ""):
        score += 45
    if period and (period in title or any(
            normalize_fyp(n) == period for n in ORDINAL.findall(title))):
        score += 15
    keywords = ("数字经济", "人工智能", "产业结构", "产业发展", "养老", "能源",
                "科技创新", "人才", "生态环境", "服务业", "工业", "农业", "教育",
                "医疗", "交通", "财政", "金融")
    related = [x for x in keywords if x in question]
    if related:
        # A passing title for a broad plan must not turn an incidental body mention
        # of a specialized topic into an identified thematic document.
        if any(token in TITLE_REQUIRED_THEMES and token not in title for token in related):
            return -1
        for token in related:
            if token in title:
                score += 70
            elif token in record.body and GENERAL_PLAN.search(title):
                score += 10
        if score < 45:
            return -1
    return score


def _rank(records: list[PolicyRecord], question: str, period: str | None
          ) -> tuple[PolicyRecord | None, bool]:
    scored = sorted(((score, record) for record in records
                     if (score := _score(record, question, period)) >= 0),
                    key=lambda item: (-item[0], item[1].planid))
    if not scored:
        return None, False
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, True
    return scored[0][1], False


def _record_area(catalog: PolicyCatalogService, record: PolicyRecord) -> str | None:
    mapping = (catalog.region_mapping or {}).get(record.planid) or {}
    code = mapping.get("region_code")
    if code and code in catalog._region_nodes:
        return catalog._region_nodes[code]["name"]
    # 没有可靠地区映射时只能按记录自带的精确行政区名称。
    return record.area


def _area_records(catalog: PolicyCatalogService, name: str) -> list[PolicyRecord]:
    return [rec for rec in catalog.snapshot.records if _record_area(catalog, rec) == name]


def _prefecture_names(catalog: PolicyCatalogService, province: str) -> list[str]:
    roots = [node for node in catalog._region_nodes.values()
             if node["tier"] == "province" and node["name"] == province]
    if len(roots) != 1:
        return []
    return sorted({child["name"] for child in roots[0]["children"]
                   if child["tier"] == "prefecture"})


def _canonical_region_aliases(name: str) -> tuple[str, ...]:
    """Derive common ethnicity-shortened names from an official area label."""
    aliases = []
    for suffix, short_suffix in (("自治县", "县"), ("自治州", "州")):
        if not name.endswith(suffix):
            continue
        match = re.fullmatch(
            rf"(?P<base>[\u4e00-\u9fff]{{2,8}}?)[\u4e00-\u9fff]{{1,5}}族{suffix}",
            name,
        )
        if match:
            aliases.append(match.group("base") + short_suffix)
    return tuple(aliases)


def _mentioned_areas(question: str, catalog: PolicyCatalogService) -> list[str]:
    question = current_question(question)
    names = {node["name"] for node in catalog._region_nodes.values()
             if node["tier"] in ("county", "prefecture", "province")}
    names.update(rec.area for rec in catalog.snapshot.records if rec.area)
    aliases: dict[str, set[str]] = {}
    title_region = re.compile(
        r"[\u4e00-\u9fa5]{2,12}?(?:自治县|自治州|自治旗|省|市|区|县|盟|旗|州)"
    )
    for record in catalog.snapshot.records:
        canonical = _record_area(catalog, record)
        if not canonical:
            continue
        for alias in _canonical_region_aliases(canonical):
            aliases.setdefault(alias, set()).add(canonical)
            if alias in question:
                names.add(alias)
        for alias in title_region.findall(record.title or ""):
            aliases.setdefault(alias, set()).add(canonical)
            if alias in question:
                names.add(alias)
    # 最长地名优先，短名出现在长名之内时不再匹配。
    matches: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []
    for name in sorted(names, key=lambda n: -len(n)):
        for hit in re.finditer(re.escape(name), question):
            if any(hit.start() < b and hit.end() > a for a, b in occupied):
                continue
            occupied.append(hit.span())
            matches.append((hit.start(), name))
    resolved = []
    for _, name in sorted(matches):
        mapped = aliases.get(name) or set()
        # Historical administrative names in a verified policy title may map
        # to the catalog's current canonical region (e.g. 密云县 -> 密云区).
        # Ambiguous aliases stay literal so retrieval fails closed.
        resolved.append(next(iter(mapped)) if len(mapped) == 1 else name)
    return list(dict.fromkeys(resolved))


def _ready(mode: Mode, question: str, records: list[PolicyRecord], dimensions: list[str],
           note: str = "") -> dict:
    return {
        "status": "ready", "message": note or "已根据研究需求定位当前发布库中的政策文献。",
        "request": {
            "task_type": mode, "question": question,
            "targets": [rec.planid for rec in records],
            "dimensions": dimensions or None,
        },
        "policies": [{"area": rec.area, "planid": rec.planid, "title": rec.title,
                      "fyp": rec.fyp} for rec in records],
    }


def _clarify(message: str) -> dict:
    return {"status": "needs_clarification", "reason": "user_scope_missing", "message": message, "request": None,
            "policies": []}


def _insufficient(message: str, reason: str = "catalog_evidence_missing") -> dict:
    return {"status": "insufficient", "reason": reason, "message": message,
            "request": None, "policies": []}


def _unperioded_comparison_coverage(question: str, catalog: PolicyCatalogService,
                                    areas: list[str]) -> dict:
    """Check title-linked policy coverage before requiring a period from the user.

    Ambiguous or absent region evidence is reported instead of silently
    selecting another policy or manufacturing a cross-region comparison.
    """
    topics = [item for item in TITLE_REQUIRED_THEMES if item in question]
    if not topics:
        return _clarify("请说明需要比较的政策主题；目前无法判断可比研究对象。")
    topic = min(topics, key=lambda value: (-len(value), value))
    selected: list[PolicyRecord] = []
    gaps: list[str] = []
    ambiguous: list[str] = []
    counts: dict[str, int] = {}
    for area in areas:
        canonical = _PROVINCES.get(area, (area,))[0]
        matches = [record for record in _area_records(catalog, canonical)
                   if topic in (record.title or "") and (record.body or "").strip()]
        counts[area] = len(matches)
        if not matches:
            gaps.append(canonical)
        elif len(matches) > 1:
            ambiguous.append(canonical)
        else:
            selected.append(matches[0])
    subjects = list(dict.fromkeys(_PROVINCES.get(area, (area,))[0] for area in areas))
    if gaps or ambiguous:
        coverage = "、".join(f"{_PROVINCES.get(area, (area,))[0]}：{counts[area]}篇" for area in areas)
        result = _insufficient(
            f"已在当前发布库按题名核查「{topic}」政策覆盖（{coverage}）。"
            "缺失地区无法支撑完整比较；多候选地区不能任意选一篇。"
            "本次不生成跨地区比较结论。",
            "insufficient_document_coverage",
        )
        result["requested_subjects"] = subjects
        result["subject_gaps"] = gaps
        result["coverage_limited_subjects"] = ambiguous
        return result
    result = _ready("compare", question, selected, _dimensions(question),
                    "各地区均找到唯一的题名匹配政策；未限定时期，"
                    "研究须保留实际政策时期，不将不同时期当作同期比较。")
    result["requested_subjects"] = subjects
    return result


def _explicit_records(question: str, catalog: PolicyCatalogService) -> tuple[list[PolicyRecord], str | None]:
    by_id = {r.planid: r for r in catalog.snapshot.records}
    ids = list(dict.fromkeys(PLANIDS.findall(question)))
    if ids:
        missing = [value for value in ids if value not in by_id]
        if missing:
            return [], "指定的政策文献在当前发布库中未找到，请核对文献名称或研究范围。"
        return [by_id[i] for i in ids], None
    titles = list(dict.fromkeys(TITLES.findall(question)))
    if not titles:
        return [], None
    picked = []
    for title in titles:
        matches = [r for r in catalog.snapshot.records if r.title
                   and (r.title == title or r.title.strip("“”") == title.strip("“”"))]
        if len(matches) != 1:
            return [], f"《{title}》无法唯一定位到当前发布文献；请补充更完整的政策名称。"
        if matches[0].planid not in {r.planid for r in picked}:
            picked.append(matches[0])
    return picked, None


def resolve_research_internal(mode: Mode, question: str, catalog: PolicyCatalogService) -> dict:
    """基于自然语言做保守对象发现；绝不把研究主题等同于文献身份。"""
    period = _period(question)
    dimensions = _dimensions(question)
    requested_ids = list(dict.fromkeys(PLANIDS.findall(question)))
    by_id = {record.planid: record for record in catalog.snapshot.records}
    if requested_ids and any(planid not in by_id for planid in requested_ids):
        return _insufficient("指定文献不在当前发布目录中，无法核验其来源身份。", "requested_document_missing")
    explicit, error = _explicit_records(question, catalog)
    if error:
        return _clarify(error)
    if explicit:
        required = 1 if mode in ("qa", "extract") else 2
        if not required <= len(explicit) <= (1 if mode == "extract" else 8):
            return _clarify("请在研究问题中说明需要处理的政策名称或方案；比较需至少两份不同文献。")
        return _ready(mode, question, explicit, dimensions)

    comparison_question = question.replace("京津冀", "北京、天津、河北")
    if mode == "compare" and len(provinces_in_text(comparison_question)) >= 2:
        found = resolve_compare(comparison_question, catalog, internal=True)
        if found["status"] == "ready":
            by_id = {r.planid: r for r in catalog.snapshot.records}
            return _ready(mode, question, [by_id[i] for i in found["targets"]],
                          dimensions or found["dimensions"], found["message"])
        if found.get("reason_code") == "user_period_missing":
            return _unperioded_comparison_coverage(
                question, catalog, provinces_in_text(comparison_question))
        if (found.get("status") == "insufficient"
                or found.get("reason_code") == "catalog_evidence_missing"):
            return _insufficient(found["message"], "catalog_evidence_missing")
        message = ("当前比较能力需要明确共同的五年规划时期，请补充本次研究时期。"
                   if found.get("reason_code") == "user_period_missing" else found["message"])
        clarification = _clarify(message)
        if comparison_question != question:
            clarification["requested_subjects"] = [
                {"北京": "北京市", "天津": "天津市", "河北": "河北省"}.get(name, name)
                for name in provinces_in_text(comparison_question)
            ]
        return clarification

    areas = _mentioned_areas(question, catalog)
    if mode == "qa":
        if len(areas) == 1:
            found, ambiguous = _rank(_area_records(catalog, areas[0]), question, period)
            if found:
                return _ready(mode, question, [found], dimensions)
            if ambiguous:
                return {"status": "fallback", "message": "目录中有多个同等匹配文献，继续使用现有 RAG 检索能力。",
                        "request": None, "policies": []}
        return {"status": "fallback", "message": "目录未能唯一定位单篇文献，继续使用现有 RAG 检索能力。",
                "request": None, "policies": []}

    if mode == "organize":
        if not areas and not period and not any(
            token in question for token in TITLE_REQUIRED_THEMES | {"产业结构", "产业升级"}
        ):
            return _clarify("请说明需要整理的政策主题，以便查找真实发布文献。")
        candidates = [record for record in catalog.snapshot.records
                      if (not areas or _record_area(catalog, record) in areas)
                      and _score(record, question, period) >= 0]
        candidates.sort(key=lambda record: (-_score(record, question, period), record.planid))
        if len(candidates) < 2:
            return _insufficient("当前发布目录中可确认的文献不足两篇，无法完成文献梳理。",
                                 "insufficient_document_coverage")
        total = len(candidates)
        return _ready(mode, question, candidates[:8], dimensions,
                      f"当前发布库确认 {total} 篇候选，本轮仅选取其中 {min(total, 8)} 篇供初步梳理；"
                      "并非完整清单，不得据此宣称覆盖全部政策。")

    if mode in ("compare", "compare_plans"):
        # 单省的集合式比较：以明确命名的地级市为主对象，自动选择同省同期可比规划。
        provinces = [a for a in areas if a.endswith(("省", "自治区"))]
        if len(provinces) == 1 and COLLECTIVE.search(question):
            province = provinces[0]
            prefectures = _prefecture_names(catalog, province)
            focus = [a for a in areas if a in prefectures]
            if len(focus) != 1:
                return _clarify("请指出本次需要重点比较的地级市；其余同省城市将由系统自动检索。")
            anchor, ambiguous = _rank(_area_records(catalog, focus[0]), question, period)
            if anchor is None:
                return (_insufficient(
                    f"当前发布库无法定位{focus[0]}对应时期的规划正文，不能完成可靠比较。",
                    "anchor_document_missing") if not ambiguous else _clarify(
                    f"当前发布库无法唯一定位{focus[0]}对应时期的规划，"
                    "发现同等匹配文献，请补充文件名称。"))
            peers = []
            subject_gaps = []
            for name in prefectures:
                if name == focus[0]:
                    continue
                rec, tie = _rank(_area_records(catalog, name), question, period)
                if rec and not tie:
                    peers.append(rec)
                else:
                    subject_gaps.append(name)
            if not peers:
                result = _insufficient(
                    f"已定位《{anchor.title}》（planid={anchor.planid}），但当前发布库缺少"
                    f"{province}其他地市的同期可比规划，不能据此得出跨地市比较结论。",
                    "insufficient_peer_coverage",
                )
                result["policies"] = [
                    {"planid": anchor.planid, "area": anchor.area, "title": anchor.title, "fyp": anchor.fyp}
                ]
                result["requested_subjects"] = list(dict.fromkeys(prefectures))
                result["subject_gaps"] = list(dict.fromkeys(subject_gaps))
                return result
            selected_peers = peers[:6]
            coverage_limited = [record.area for record in peers[6:] if record.area]
            result = _ready(mode, question, [anchor, *selected_peers], dimensions,
                            f"以{focus[0]}为核心，自动选择{len(selected_peers)}个当前发布库中有同期可比规划的{province}地级市；结论只覆盖本轮入选城市，不代表全省。")
            result["requested_subjects"] = list(dict.fromkeys(prefectures))
            result["subject_gaps"] = list(dict.fromkeys(subject_gaps))
            result["coverage_limited_subjects"] = list(dict.fromkeys(coverage_limited))
            return result

        named = [a for a in areas if a not in provinces]
        if len(named) >= 2:
            selected = []
            for name in named[:8]:
                rec, tie = _rank(_area_records(catalog, name), question, period)
                if rec is None:
                    if tie:
                        return _clarify(f"无法唯一定位{name}的对应政策文献；请补充政策名称或缩小研究范围。")
                    return _insufficient(f"当前发布库没有{name}符合条件的可比文献。",
                                         "comparison_document_missing")
                selected.append(rec)
            return _ready(mode, question, selected, dimensions)

        return _clarify("请描述需要比较的两个以上地区、政策文件名称或方案；不需要填写政策编号。")

    # 提取模式：按名称/地区/规划期/主题在真实目录中唯一定位一个文件。
    if mode == "extract":
        candidates = list(catalog.snapshot.records)
        if areas:
            city = areas[-1]
            candidates = _area_records(catalog, city)
        found, tie = _rank(candidates, question, period)
        if found:
            return _ready(mode, question, [found], dimensions)
        return (_clarify("有多篇同等匹配文献，请补充完整政策标题。") if tie else
                _insufficient("当前发布库未能定位符合研究主题的政策正文。"))

    return _clarify("请提供至少两份可区分的方案名称或相关地区与时期，以便自动发现文献。")


def resolve_research(mode: Mode, question: str, catalog: PolicyCatalogService) -> dict:
    """Legacy public response shape; run discovery uses the richer internal result."""
    result = resolve_research_internal(mode, question, catalog)
    status = result["status"]
    if status in {"insufficient", "fallback"}:
        status = "needs_clarification"
    return {
        "status": status,
        "message": result["message"],
        "request": result.get("request"),
        "policies": result.get("policies", []),
    }
