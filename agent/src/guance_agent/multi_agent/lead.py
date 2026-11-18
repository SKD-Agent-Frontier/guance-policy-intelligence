"""Lead Agent: choose bounded research assignments and propose evidence-linked synthesis."""
from __future__ import annotations

import asyncio
import json
import re
import time

from .contracts import Assignment, Plan, Requirement, Synthesis
from ..research_requirements import parse_requirements

_EXHAUSTIVE = re.compile(r"所有|全部|全量|完整清单|准确篇数|穷举")
_SPLIT = re.compile(r"[；;。]|\s*(?:并且|此外还要|同时)\s*")


def _units(question: str) -> list[str]:
    parsed = parse_requirements(question)
    labels = [
        str(unit.get("label") or "").strip()
        for unit in parsed if isinstance(unit, dict) and unit.get("actionable", True)
    ]
    labels += [
        clause.strip(" ，、")
        for clause in _SPLIT.split(question)
        if 7 <= len(clause.strip()) <= 150
    ]
    return list(dict.fromkeys(x for x in labels if x))[:8] or [question[:150]]


def _parse_json(raw: str) -> dict:
    value = json.loads(raw.strip())
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


async def build_plan(question: str, targets: list[str] | None, runtime,
                     deadline: float, *, max_workers: int = 2) -> Plan:
    """Model may group GIVEN requirement IDs, never invent a new user scope."""
    labels = _units(question)
    units = tuple(Requirement(f"R{i + 1}", label) for i, label in enumerate(labels))
    if len(units) < 2 and _EXHAUSTIVE.search(question):
        # Complementary perspectives, not fabricated regions/periods.
        units = (
            *units,
            Requirement(f"R{len(units) + 1}", "政策对象候选覆盖与遗漏边界"),
        )
    size = min(max_workers, len(units))
    groups = [[] for _ in range(size)]
    source = "deterministic_fallback"
    if size > 1 and deadline - time.monotonic() > 12:
        try:
            raw = await runtime.complete_text(
                [
                    {"role": "system", "content": (
                        "你只负责把已经给出的研究要求 ID 分配给两个政策研究执行者，"
                        "不允许创造新的地区、时期、文献或用户要求。"
                        "返回严格 JSON：{\"groups\":[[\"R1\"],[\"R2\"]]}。"
                        "必须且只能把全部 ID 分配一次，不得遗漏和重复。"
                    )},
                    {"role": "user", "content": json.dumps(
                        [{"id": x.id, "label": x.label} for x in units], ensure_ascii=False
                    )},
                ],
                max_tokens=260,
                timeout=min(12.0, max(0.5, deadline - time.monotonic() - 4)),
                purpose="multi_lead_plan",
            )
            proposed = _parse_json(raw).get("groups")
            if isinstance(proposed, list) and len(proposed) == size:
                id_set = {x.id for x in units}
                flattened = [v for group in proposed if isinstance(group, list) for v in group]
                if (all(isinstance(group, list) and group for group in proposed)
                        and all(isinstance(v, str) for v in flattened)
                        and len(flattened) == len(units)
                        and set(flattened) == id_set
                        and len(set(flattened)) == len(flattened)):
                    groups = proposed
                    source = "model_validated"
        except asyncio.CancelledError:
            raise
        except Exception:
            # A bad/unavailable Lead must not block the deterministic fallback.
            pass
    if not any(groups):
        for index, unit in enumerate(units):
            groups[index % size].append(unit.id)
    labels_by_id = {item.id: item.label for item in units}
    assignments = tuple(
        Assignment(
            id=f"W{index + 1}",
            focus="；".join(labels_by_id[ident] for ident in group)[:380],
            requirement_ids=tuple(group),
            original_question=question,
            targets=tuple(targets) if targets is not None else None,
        )
        for index, group in enumerate(groups)
    )
    return Plan(requirements=units, assignments=assignments, source=source)


async def synthesize(question: str, claims: list[dict], findings: list[dict],
                     runtime, deadline: float, *, feedback: str = "") -> list[Synthesis]:
    """Synthesize ONLY from surviving quote-verified findings and fact identifiers."""
    remaining = deadline - time.monotonic()
    if remaining < 9 or not claims:
        return []
    compact_facts = [
        {"id": item["id"], "text": item["text"][:260],
         "quotes": [x[:220] for x in item.get("quotes", [])[:2]],
         "planids": item.get("planids", [])[:3]}
        for item in claims[:36]
    ]
    compact_findings = [
        {"id": x["id"], "kind": x["kind"], "text": x["text"][:420],
         "fact_ids": x["fact_ids"][:8]}
        for x in findings[:20]
    ]
    raw = await runtime.complete_text(
        [
            {"role": "system", "content": (
                "你是政策研究负责人，只基于输入的、经过原文核验的 facts 和 findings"
                "形成至多3个综合分析段落。不得使用外部记忆或增添新的政策对象、数字和事实。"
                "逐段列明全部支持的 fact_ids，没有依据的结论不写。"
                "请输出 JSON 对象 {\"blocks\":[{\"id\":\"S1\",\"title\":\"研究综合\","
                "\"text\":\"分析...\",\"fact_ids\":[\"F1\"]}]}。"
                "每段不超过480字，标题不超过50字。只能引用输入的 fact_ids。"
            )},
            {"role": "user", "content": json.dumps(
                {"question": question[:2200], "facts": compact_facts,
                 "findings": compact_findings, "critic_feedback": feedback[:500]},
                ensure_ascii=False,
            )},
        ],
        max_tokens=950,
        timeout=min(22.0, max(0.5, remaining - 3)),
        purpose="multi_lead_synthesis",
    )
    data = _parse_json(raw)
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("synthesis missing blocks")
    allowed = {item["id"] for item in claims}
    output = []
    for i, item in enumerate(blocks[:3]):
        if not isinstance(item, dict):
            continue
        identifiers = item.get("fact_ids")
        if (not isinstance(identifiers, list) or not identifiers
                or any(not isinstance(v, str) or v not in allowed for v in identifiers)):
            continue
        text = item.get("text")
        title = item.get("title")
        if not isinstance(text, str) or not text.strip() or len(text) > 480:
            continue
        if not isinstance(title, str) or not title.strip() or len(title) > 50:
            continue
        output.append(Synthesis(
            id=f"S{i + 1}", title=title.strip(), text=text.strip(),
            fact_ids=tuple(dict.fromkeys(identifiers)),
        ))
    return output
