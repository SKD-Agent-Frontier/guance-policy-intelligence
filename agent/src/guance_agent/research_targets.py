"""Concrete research objects named in a question: retrieval hints, not facts."""
from __future__ import annotations

import re

SIGNPOST = re.compile(
    r"(?:特别是|尤其是|重点关注|重点研究|重点分析|重点看|"
    r"关于|涉及|包括|例如|比如|针对)"
    r"([^，。；：!?？\n]{2,55})"
)
TAIL = re.compile(
    r"的(?:具体|主要|相关|发展目标|量化|支持|扶持|政策|措施|规划|"
    r"建设|指标|做法|部署|情况|内容|要求|目标|特点|差异|区别)"
    r"|(?:有哪些|是什么|如何|怎样|怎么|分别|其中|请引用|请说明|请分析)"
)
SPLIT = re.compile(r"和|与|以及|、|及")
STOP = frozenset({"政策", "规划", "情况", "内容", "这些措施", "该政策",
                  "这一点", "上面", "上述", "重点", "目标", "原文", "发展", "其中"})


def extract_targets(question: str) -> tuple[str, ...]:
    """Find up to four explicit target subjects, without a model call."""
    found: list[str] = []
    for match in SIGNPOST.finditer(question or ""):
        raw = TAIL.split(match.group(1), maxsplit=1)[0]
        for part in SPLIT.split(raw):
            item = re.sub(r"^(?:这些|上述|其中|该|这类|有关|相关)", "", part.strip())
            item = re.sub(r"(?:方面|部分|领域)$", "", item)
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
