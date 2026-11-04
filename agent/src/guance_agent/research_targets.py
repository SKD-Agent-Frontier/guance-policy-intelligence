"""Concrete research objects named in a question: retrieval hints, not facts."""
from __future__ import annotations

import re

from .routing import current_question

SIGNPOST = re.compile(
    r"(?:特别是|特别关注|尤其是|重点关注|重点研究|重点分析|重点看|"
    r"关于|涉及|包括|例如|比如|针对)"
    r"([^，。；：!?？\n]{2,55})"
)
AREA_SIGNPOST = re.compile(r"在(?P<targets>[^。；!?？\n]{2,60}?)(?:方面|领域|上|中)")
TAIL = re.compile(
    r"的(?:具体|主要|相关|企业数量|企业家数|年销售收入|销售收入|营业收入|"
    r"年营收|营收|销售额|目标产值|工业总产值|总产值|产值|生产能力|"
    r"年产能力|加工能力|产能|年产量|产量|播种面积|种植面积|投资总额|"
    r"投资额|投资规模|发展目标|量化|支持|扶持|政策|措施|规划|"
    r"建设|指标|做法|部署|情况|内容|要求|目标|特点|差异|区别)"
    r"|(?:有哪些|是什么|如何|怎样|怎么|分别|其中|请引用|请说明|请分析)"
)
SPLIT = re.compile(r"和|与|以及|、|及")
STOP = frozenset({"政策", "规划", "情况", "内容", "这些措施", "该政策",
                  "这一点", "上面", "上述", "重点", "目标", "原文", "发展", "其中",
                  "哪些", "什么", "如何", "怎样", "怎么"})


def extract_targets(question: str) -> tuple[str, ...]:
    """Find up to four explicit target subjects, without a model call."""
    found: list[str] = []
    text = current_question(question or "")
    segments = [match.group(1) for match in SIGNPOST.finditer(text)]
    segments.extend(match.group("targets") for match in AREA_SIGNPOST.finditer(text))
    for segment in segments:
        raw = TAIL.split(segment, maxsplit=1)[0]
        for part in SPLIT.split(raw):
            item = re.sub(r"^(?:这些|上述|其中|该|这类|有关|相关|在|对)", "", part.strip())
            item = re.sub(r"(?:方面|部分|领域)(?:是|又|都)?$", "", item)
            item = re.sub(r"(?:具体|主要|相关)$", "", item)
            item = item.strip(" ：:，。？?!！'“”‘’（）() ")
            if (2 <= len(item) <= 12 and item not in STOP
                    and re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]+", item)
                    and item not in found):
                found.append(item)
            if len(found) >= 4:
                return tuple(found)
    return tuple(found)


def mentions_subject(text: str, subject: str) -> bool:
    """Exact or limited morphological match; never assert semantic equivalence."""
    if subject in text:
        return True
    for suffix in ("加工业", "养殖业", "产业", "行业", "领域", "建设", "养殖", "加工"):
        if subject.endswith(suffix):
            base = subject[:-len(suffix)]
            if len(base) >= 2 and base in text:
                return True
    return False


def wants_numbers(question: str) -> bool:
    return bool(re.search(r"量化|数字指标|具体指标|数据|产值|产量|占比|比例|"
                          r"销售收入|年均增长|增长率|多少|数量|规模目标", question or ""))
