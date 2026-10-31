"""自然语言研究对象发现：只从当前冻结目录选择真实文献，不生成编号。

本层负责发现而不生成政策判断；业务模型只接收经此层定位的对象。
不做正文关键词的非确定性跨主题替代；模糊时返回文字澄清。
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .compare_resolution import _DIMENSIONS, _areas as provinces_in_text, resolve_compare
from .policy_catalog import PolicyCatalogService, PolicyRecord, normalize_fyp

Mode = Literal["extract", "compare", "compare_plans"]


class ResearchResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Mode
    question: str = Field(min_length=1, max_length=4000)


PLANIDS = re.compile(r"planid\s*[:：=]?\s*([A-Za-z0-9_-]{1,64})", re.IGNORECASE)
TITLES = re.compile(r"《([^《》]{3,120})》")
ORDINAL = re.compile(r"(?:十一|十二|十三|十四|十五)五")
EXPLICIT_PERIOD = re.compile(r"第(?:十[一二三四五]|十)个五年(?:规划|计划)")
COLLECTIVE = re.compile(r"(?:其他|其它|其余|全省|省内|各)(?:地级市|地市|城市|市)")
GENERAL_THEME = re.compile(r"产业结构|产业布局|产业升级|三次产业|产业优化|经济结构")
GENERAL_PLAN = re.compile(r"国民经济和社会发展|五年(?:规划|计划)纲要|总体规划纲要")


def _period(question: str) -> str | None:
    explicit = EXPLICIT_PERIOD.findall(question)
    names = ORDINAL.findall(question)
    values = [normalize_fyp(value) for value in explicit + names]
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
    meta = normalize_fyp(record.fyp)
    if meta:
        return meta == period
    # 未标注规划期时，必须由题名明确确认，不能从正文中的偶然出现推断。
    title = record.title or ""
    alias = next((name for name in ORDINAL.findall(title)
                  if normalize_fyp(name) == period), None)
    return bool(alias or period in title)


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


def _mentioned_areas(question: str, catalog: PolicyCatalogService) -> list[str]:
    names = {node["name"] for node in catalog._region_nodes.values()
             if node["tier"] in ("prefecture", "province")}
    names.update(rec.area for rec in catalog.snapshot.records if rec.area)
    # 最长地名优先，短名出现在长名之内时不再匹配。
    matches: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []
    for name in sorted(names, key=lambda n: -len(n)):
        for hit in re.finditer(re.escape(name), question):
            if any(hit.start() < b and hit.end() > a for a, b in occupied):
                continue
            occupied.append(hit.span())
            matches.append((hit.start(), name))
    return [name for _, name in sorted(matches)]


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
    return {"status": "needs_clarification", "message": message, "request": None,
            "policies": []}


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


def resolve_research(mode: Mode, question: str, catalog: PolicyCatalogService) -> dict:
    """基于自然语言做保守对象发现；绝不把研究主题等同于文献身份。"""
    period = _period(question)
    dimensions = _dimensions(question)
    explicit, error = _explicit_records(question, catalog)
    if error:
        return _clarify(error)
    if explicit:
        required = 1 if mode == "extract" else 2
        if not required <= len(explicit) <= (1 if mode == "extract" else 8):
            return _clarify("请在研究问题中说明需要处理的政策名称或方案；比较需至少两份不同文献。")
        return _ready(mode, question, explicit, dimensions)

    if mode == "compare" and len(provinces_in_text(question)) >= 2:
        found = resolve_compare(question, catalog)
        if found["status"] == "ready":
            by_id = {r.planid: r for r in catalog.snapshot.records}
            return _ready(mode, question, [by_id[i] for i in found["targets"]],
                          dimensions or found["dimensions"], found["message"])
        return _clarify(found["message"].replace("使用高级入口", "补充文献名称"))

    areas = _mentioned_areas(question, catalog)
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
                return _clarify(
                    f"当前发布库无法唯一定位{focus[0]}对应时期的规划，"
                    + ("发现同等匹配文献，请补充文件名称。" if ambiguous
                       else "未取得可用的对应时期规划正文，无法开展可靠比较。"))
            peers = []
            for name in prefectures:
                if name == focus[0]:
                    continue
                rec, tie = _rank(_area_records(catalog, name), question, period)
                if rec and not tie:
                    peers.append(rec)
            peers = peers[:6]
            if not peers:
                return _clarify(f"已定位{focus[0]}的规划，但当前发布库没有足够的同省同期可比规划，不能据此得出比较结论。")
            return _ready(mode, question, [anchor, *peers], dimensions,
                          f"以{focus[0]}为核心，自动选择{len(peers)}个当前发布库中有同期可比规划的{province}地级市；结论只覆盖本轮入选城市，不代表全省。")

        named = [a for a in areas if a not in provinces]
        if len(named) >= 2:
            selected = []
            for name in named[:8]:
                rec, tie = _rank(_area_records(catalog, name), question, period)
                if rec is None:
                    return _clarify(f"无法唯一定位{name}的对应政策文献；请补充政策名称或缩小研究范围。" if tie
                                    else f"当前发布库没有{name}符合条件的可比文献。")
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
        return _clarify("有多篇同等匹配文献，请补充完整政策标题。" if tie
                        else "当前发布库未能定位符合研究主题的政策正文；请补充政策名称、地区或规划时期。")

    return _clarify("请提供至少两份可区分的方案名称或相关地区与时期，以便自动发现文献。")
