"""为明确的跨省政策比较自动定位冻结目录中的政策对象。

这里只做确定性的元数据/全文字面定位，不调用大模型，不把 planid 暴露为普通用户前置输入。
定位失败时返回明确原因，绝不编造政策或将其它地区、时期的文件替代。
"""
from __future__ import annotations

import re
from pydantic import BaseModel, ConfigDict, Field

from .policy_catalog import PolicyCatalogService, PolicyRecord, normalize_fyp


class CompareResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, max_length=4000)


# 左侧为目录地区归一化键，右侧为自然语言中的可识别写法。
_PROVINCES = {
    "北京": ("北京市", "北京"),
    "天津": ("天津市", "天津"),
    "河北": ("河北省", "河北"),
    "山西": ("山西省", "山西"),
    "内蒙古": ("内蒙古自治区", "内蒙古"),
    "辽宁": ("辽宁省", "辽宁"),
    "吉林": ("吉林省", "吉林"),
    "黑龙江": ("黑龙江省", "黑龙江"),
    "上海": ("上海市", "上海"),
    "江苏": ("江苏省", "江苏"),
    "浙江": ("浙江省", "浙江"),
    "安徽": ("安徽省", "安徽"),
    "福建": ("福建省", "福建"),
    "江西": ("江西省", "江西"),
    "山东": ("山东省", "山东"),
    "河南": ("河南省", "河南"),
    "湖北": ("湖北省", "湖北"),
    "湖南": ("湖南省", "湖南"),
    "广东": ("广东省", "广东"),
    "广西": ("广西壮族自治区", "广西自治区", "广西"),
    "海南": ("海南省", "海南"),
    "重庆": ("重庆市", "重庆"),
    "四川": ("四川省", "四川"),
    "贵州": ("贵州省", "贵州"),
    "云南": ("云南省", "云南"),
    "西藏": ("西藏自治区", "西藏"),
    "陕西": ("陕西省", "陕西"),
    "甘肃": ("甘肃省", "甘肃"),
    "青海": ("青海省", "青海"),
    "宁夏": ("宁夏回族自治区", "宁夏"),
    "新疆": ("新疆维吾尔自治区", "新疆"),
    "香港": ("香港特别行政区", "香港"),
    "澳门": ("澳门特别行政区", "澳门"),
    "台湾": ("台湾省", "台湾"),
}
_ALIASES = sorted(
    ((alias, province) for province, aliases in _PROVINCES.items() for alias in aliases),
    key=lambda pair: -len(pair[0]),
)
_DIMENSIONS = (
    "发展目标", "量化目标", "政策工具", "适用对象", "支持对象", "支持措施",
    "资金标准", "实施期限", "实施路径", "主要任务", "责任主体", "产业布局",
    "发展方向", "重点任务", "政策重点", "考核指标", "申报条件", "办理流程",
)
_PERIODS = ("十", "十一", "十二", "十三", "十四", "十五")


def _areas(question: str) -> list[str]:
    # 保留首次出现顺序，长别名优先，避免将「浙江省」重复识别为「浙江」。
    matches: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for alias, province in _ALIASES:
        for hit in re.finditer(re.escape(alias), question):
            start, end = hit.span()
            if any(start < right and end > left for left, right in occupied):
                continue
            occupied.append((start, end))
            matches.append((start, end, province))
    found: list[str] = []
    for _, _, province in sorted(matches):
        if province not in found:
            found.append(province)
    return found


def _period(question: str) -> str | None:
    matches = [name for name in _PERIODS
               if re.search(rf"(?:{name}五|第{name}个五年(?:规划|计划))", question)]
    if len(matches) != 1:
        return None
    return matches[0] + "五"


def _topic(question: str, areas: list[str], period: str | None, dimensions: list[str]) -> str:
    value = question
    for alias, province in _ALIASES:
        if province in areas:
            value = value.replace(alias, "")
    if period:
        value = re.sub(
            rf"第{re.escape(period[:-1])}个五年(?:规划|计划)|{re.escape(period)}(?:时期|规划|期间)?",
            "", value,
        )
    for dimension in dimensions:
        value = value.replace(dimension, "")
    value = re.sub(
        r"比较|对比|分析|研究|请|帮我|一下|的异同|异同|有何不同|有哪些不同|"
        r"相同点|不同点|差异|区别|共同点|两省|两个省|两地|各自|之间|"
        r"相关政策|政策文件|政策|规划|目标|内容|方面|情况|分别|是什么|"
        r"[和与及、，,。！？?：:；;\s]",
        "", value,
    )
    return value[:100]


def _canonical_area(value: str | None) -> str | None:
    for province, aliases in _PROVINCES.items():
        if value in aliases:
            return province
    return None


def _score(record: PolicyRecord, period: str, topic: str) -> int | None:
    title = record.title or ""
    # 整词连续匹配；例如「数字经济」绝不能拆成「数字」「经济」。
    in_title = topic in title
    in_body = topic in record.body
    if not (in_title or in_body):
        return None
    requested_period = normalize_fyp(period)
    metadata_period = normalize_fyp(record.fyp)
    period_in_title = (period in title or requested_period in title)
    if metadata_period and metadata_period != requested_period:
        return None
    if metadata_period != requested_period and not period_in_title:
        return None
    return (
        (100 if in_title else 15)
        + (30 if period_in_title else 0)
        + (8 if "规划" in title else 0)
        + (4 if "专项" in title else 0)
        + (2 if "发展" in title else 0)
    )


def resolve_compare(question: str, catalog: PolicyCatalogService) -> dict:
    """输出可交给 compare Agent 的目标编号；无确定来源则拒绝自动执行。"""
    areas = _areas(question)
    period = _period(question)
    dimensions = [dimension for dimension in _DIMENSIONS if dimension in question]
    topic = _topic(question, areas, period, dimensions)
    if len(areas) < 2 or len(areas) > 8:
        return {"status": "needs_clarification", "message": "请明确需要比较的 2—8 个省级地区。", "targets": [], "policies": [], "dimensions": dimensions}
    if not period:
        return {"status": "needs_clarification", "message": "请明确共同的五年规划时期（例如十四五），或使用高级入口指定文献。", "targets": [], "policies": [], "dimensions": dimensions}
    if len(topic) < 2:
        return {"status": "needs_clarification", "message": "请补充比较主题，例如数字经济；无需提供政策编号。", "targets": [], "policies": [], "dimensions": dimensions}

    selected: list[dict] = []
    for area in areas:
        candidates: list[tuple[int, PolicyRecord]] = []
        for record in catalog.snapshot.records:
            if _canonical_area(record.area) != area:
                continue
            if record.admin_level not in (None, "", "province", "省", "省级"):
                continue
            score = _score(record, period, topic)
            if score is not None:
                candidates.append((score, record))
        candidates.sort(key=lambda pair: (-pair[0], pair[1].planid))
        if not candidates:
            return {"status": "needs_clarification",
                    "message": f"当前发布政策库未找到{area}地区「{period}·{topic}」的可定位文献；不会以其它地区或时期的政策替代。可调整主题或改用高级入口。",
                    "targets": [], "policies": selected, "dimensions": dimensions}
        top_score, top = candidates[0]
        # 多份候选同分意味着无法根据明确条件辨识原意，不任意按编号挑选。
        if len(candidates) > 1 and candidates[1][0] == top_score:
            titles = "；".join(f"《{record.title}》（{record.planid}）"
                               for _, record in candidates[:3])
            return {"status": "needs_clarification",
                    "message": f"{area}存在多个同等匹配的政策文献：{titles}。请补充文件标题以消除歧义；编号仅供高级定位使用。",
                    "targets": [], "policies": selected, "dimensions": dimensions}
        selected.append({"area": area, "planid": top.planid, "title": top.title,
                         "fyp": top.fyp, "match": "title" if topic in (top.title or "") else "body"})

    return {"status": "ready",
            "message": "已根据地区、规划时期和完整主题词在当前发布库中定位比较文献。",
            "targets": [item["planid"] for item in selected],
            "policies": selected, "dimensions": dimensions}
