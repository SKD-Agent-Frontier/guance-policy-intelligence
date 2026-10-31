"""检索路由：从用户问题里确定性地识别对象与约束。

不做模型调用，不访问任何存储，只产出一个可检验的决定交给编排层执行。
识别策略偏保守：认不出来就当开放主题，宁可不限定，也不猜错对象。
"""
import re
from dataclasses import dataclass
from typing import Literal

# 只认带标签的编号。题干里 planid 是明确写出来的（形如 planid 10032），
# 裸数字一律不认——训练集里 （2016）（2010）这类是年份，认成编号会拿错政策。
PLANID = re.compile(r"planid\s*[:：]?\s*([A-Za-z0-9_-]{2,32})", re.IGNORECASE)
# 政策标题用书名号标注；只剥这一种最明确的写法
TITLE = re.compile(r"《([^《》]{2,80})》")
# 地区名必须带行政后缀，并排除「地区/各地/本市/全县」这类泛指
AREA = re.compile(r"(?<![地该各本全])([\u4e00-\u9fa5]{2,4}(?:省|市|区|县|自治州|盟|旗))")
# 规划期统一到 RAG 的规范期号口径
PERIOD = re.compile(r"第?(十[一二三四五])(?:个)?五")
# “十五”计划（2001—2005）不是“十五五”（2026—2030）。
# 显式排除“十五届”等序数表达；第十五个五年规划由 PERIOD 负责识别。
HISTORICAL_TENTH = re.compile(r'(?<![一二三四五六七八九十第])(?:[“"「])?十五(?:[”"」])?(?!五|届|大|中|个|年|岁)')
PERIOD_ALIAS = {
    "十一": "第十一个五年规划", "十二": "第十二个五年规划", "十三": "第十三个五年规划",
    "十四": "第十四个五年规划", "十五": "第十五个五年规划",
}
# 地区名前面常粘着动词（比较大渡口区 / 与南平市），逐层剥掉再判定
LEAD_NOISE = ("比较", "分析", "对照", "围绕", "针对", "对于", "关于", "以及",
              "想了解", "了解", "查询", "查找", "介绍", "研究", "梳理", "总结", "请问",
              "期间", "年度", "时期", "阶段",
              "和", "与", "及", "在", "对", "较", "比")
MAX_QUERY = 200


@dataclass(frozen=True)
class RouteDecision:
    """一次路由的完整决定；所有字段都可被测试直接断言。"""

    kind: Literal["open", "objects"]
    planids: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    fyps: tuple[str, ...] = ()
    sub_query: str = ""
    # 非空表示这一轮不能检索，必须如实告诉用户原因，不能假装检索过
    blocked: str | None = None

    @property
    def multi(self) -> bool:
        return len(self.planids) + len(self.titles) > 1


def parse_planids(text: str) -> tuple[str, ...]:
    """只取带 planid 标签的编号，返回去重后的稳定顺序。"""
    return tuple(dict.fromkeys(PLANID.findall(text or "")))


def parse_titles(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.strip() for m in TITLE.findall(text or "") if m.strip()))


def _trim_area(name: str) -> str:
    """剥掉粘在前面的常见动词/介词，保留真正的行政区名。"""
    changed = True
    while changed:
        changed = False
        for noise in LEAD_NOISE:
            if name.startswith(noise) and len(name) - len(noise) >= 2:
                name, changed = name[len(noise):], True
                break
    return name


def parse_areas(text: str) -> tuple[str, ...]:
    """提取明确的行政区名；泛指词和「地区」这类后缀不会命中。"""
    out = []
    # 期号会和地名粘在一起（十四五期间甲市），先挖掉再提地名
    for raw in AREA.findall(PERIOD.sub(" ", text or "")):
        name = _trim_area(raw)
        # 「地区」是泛指，不是行政区名
        if len(name) >= 2 and not name.endswith("地区"):
            out.append(name)
    return tuple(dict.fromkeys(out))


def parse_fyps(text: str) -> tuple[str, ...]:
    """按原文出现顺序识别新旧规划期，避免把“十五”计划错归“十五五”。"""
    value = text or ""
    matches = [(match.start(), PERIOD_ALIAS[match.group(1)])
               for match in PERIOD.finditer(value)]
    matches.extend((match.start(), "第十个五年计划")
                   for match in HISTORICAL_TENTH.finditer(value))
    return tuple(dict.fromkeys(period for _, period in sorted(matches)))


def strip_annotations(text: str) -> str:
    """把编号/书名号标注从检索子查询里去掉，避免它们干扰词法匹配。"""
    cleaned = PLANID.sub(" ", text or "")
    cleaned = TITLE.sub(lambda m: m.group(1), cleaned)
    cleaned = re.sub(r"[（）()]\s*[）)]?", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def decide(question: str, *, max_query: int = MAX_QUERY) -> RouteDecision:
    """把一个问题路由成「按对象取证」或「开放主题检索」。"""
    planids, titles = parse_planids(question), parse_titles(question)
    areas, fyps = parse_areas(question), parse_fyps(question)
    sub_query = strip_annotations(question)
    kind: Literal["open", "objects"] = "objects" if (planids or titles) else "open"
    blocked = None
    if not sub_query:
        blocked = "empty_query"
    elif len(sub_query) > max_query:
        # 不静默截断：超长就明确要求缩小范围，不能让用户以为已经检索过
        blocked = "query_too_long"
    return RouteDecision(kind=kind, planids=planids, titles=titles, areas=areas,
                         fyps=fyps, sub_query=sub_query, blocked=blocked)
