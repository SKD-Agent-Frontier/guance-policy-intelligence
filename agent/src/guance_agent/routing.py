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
# 地区名必须带行政后缀，并排除「地区/各地/本市/全县」这类泛指。
# 自治县/自治州名称可达 8 个字以上（如“长白朝鲜族自治县”），不能用 2-4
# 字前缀截断；最长后缀必须优先匹配，否则会误识别成“鲜族自治县”。
AREA = re.compile(
    r"(?<![地该各本全])"
    r"([\u4e00-\u9fa5]{2,12}?(?:自治县|自治州|自治旗|省|市(?!场)|区(?!域|块|间)|县|盟|旗|州(?!市|县|区|省|州)))"
)
# 前端在追问时会合成“研究对象：…；本轮问题：…”。解析用户当前约束时只取
# 当前问题，不能让旧研究对象回灌成本轮的新地区/新对象。
CURRENT_QUESTION_MARKER = "本轮问题："
# 规划期统一到 RAG 的规范期号口径
PERIOD = re.compile(r"第?(十[一二三四五])(?:个)?五")
# “十五”计划（2001—2005）不是“十五五”（2026—2030）。
# 显式排除“十五届”等序数表达；第十五个五年规划由 PERIOD 负责识别。
HISTORICAL_TENTH = re.compile(r'(?<![一二三四五六七八九十第])(?:[“"「])?十五(?:[”"」])?(?!五|届|大|中|个|年|岁)')
HISTORICAL_PERIOD = re.compile(r"(?<![一二三四五六七八九十])第?([一二三四五六七八九])(?:个)?五(?!折|届|大|中)")
PERIOD_ALIAS = {
    "十一": "第十一个五年规划", "十二": "第十二个五年规划", "十三": "第十三个五年规划",
    "十四": "第十四个五年规划", "十五": "第十五个五年规划",
}
# 地区名前面常粘着动词（比较大渡口区 / 与南平市），逐层剥掉再判定
LEAD_NOISE = ("我想了解一下", "我想了解", "我想知道", "我们想了解", "我们想",
              "请概括", "请介绍", "请说明", "请分析", "请查询", "请查找", "请问", "请",
              "概括", "简述", "介绍一下", "介绍一下", "介绍", "说明", "分析",
              "比较", "对照", "围绕", "针对", "对于", "关于", "以及", "从", "由",
              "想了解", "想知道", "了解", "查询", "查找", "研究", "梳理", "总结",
              "我想", "我们", "我想问", "想",
              "期间", "年度", "时期", "阶段",
              "那么", "那", "这", "该", "此",
              "和", "与", "及", "在", "对", "较", "比", "我")
# “其他远郊区县”“其余地区”是集合指代，不是可过滤的单一行政区。
COLLECTIVE_AREA_PREFIX = ("其他", "其它", "其余", "剩余", "各")
MAX_QUERY = 200


@dataclass(frozen=True)
class RouteDecision:
    """一次路由的完整决定；所有字段都可被测试直接断言。"""

    kind: Literal["open", "objects"]
    planids: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    # Populated only by the API's frozen policy-catalog resolver.
    canonical_areas: tuple[str, ...] = ()
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


def current_question(text: str) -> str:
    """Return only the current turn after a preparation-layer context prefix."""
    value = text or ""
    if CURRENT_QUESTION_MARKER in value:
        return value.rsplit(CURRENT_QUESTION_MARKER, 1)[1].strip()
    return value


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
    value = current_question(text)
    # 期号会和地名粘在一起（十四五期间甲市），先挖掉再提地名
    for raw in AREA.findall(PERIOD.sub(" ", value)):
        if raw.startswith(COLLECTIVE_AREA_PREFIX):
            continue
        # Keep true three-character names (e.g. 从江县) intact.
        name = _trim_area(raw) if len(raw) > 3 else raw
        # 「地区」是泛指，不是行政区名
        # “城市更新”“产业园区”等不是具体行政区，不能被强制作为检索过滤条件。
        generic = ("地区", "地市", "城市", "上市", "园区", "社区", "景区", "校区",
                   "片区", "街区", "辖区", "疫区", "远郊区", "近郊区",
                   "同省", "本省", "该省", "全省", "各省")
        if len(name) >= 2 and not name.endswith(generic):
            out.append(name)
    return tuple(dict.fromkeys(out))


def parse_fyps(text: str) -> tuple[str, ...]:
    """按原文出现顺序识别新旧规划期，避免把“十五”计划错归“十五五”。"""
    value = current_question(text)
    matches = [(match.start(), PERIOD_ALIAS[match.group(1)])
               for match in PERIOD.finditer(value)]
    matches.extend((match.start(), "第十个五年计划")
                   for match in HISTORICAL_TENTH.finditer(value))
    if any(term in value for term in ("规划", "计划", "时期", "期间")):
        matches.extend((match.start(), f"第{match.group(1)}个五年计划")
                       for match in HISTORICAL_PERIOD.finditer(value))
    return tuple(dict.fromkeys(period for _, period in sorted(matches)))


def strip_annotations(text: str) -> str:
    """把编号/书名号标注从检索子查询里去掉，避免它们干扰词法匹配。"""
    cleaned = PLANID.sub(" ", text or "")
    cleaned = TITLE.sub(lambda m: m.group(1), cleaned)
    cleaned = re.sub(r"[（）()]\s*[）)]?", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def decide(question: str, *, max_query: int = MAX_QUERY) -> RouteDecision:
    """把一个问题路由成「按对象取证」或「开放主题检索」。"""
    question = current_question(question)
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
