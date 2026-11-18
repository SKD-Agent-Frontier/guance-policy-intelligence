"""Independent critical review of proposed synthesis against verified quotes."""
from __future__ import annotations

import asyncio
import json
import re
import time

from .contracts import Review, Synthesis

_DIGITS = re.compile(r"(?<![a-zA-Z])\d+(?:\.\d+)?%?")


def deterministic_filter(blocks: list[Synthesis], facts: dict[str, dict]) -> list[Synthesis]:
    """Reject invented numeric values and unknown fact citations before an LLM review."""
    valid = []
    for block in blocks:
        if not block.fact_ids or any(ident not in facts for ident in block.fact_ids):
            continue
        sources = " ".join(
            facts[fid]["text"] + " " + " ".join(facts[fid].get("quotes", []))
            for fid in block.fact_ids
        )
        if any(n not in _DIGITS.findall(sources) for n in _DIGITS.findall(block.text)):
            continue
        valid.append(block)
    return valid


async def review(blocks: list[Synthesis], facts: dict[str, dict], runtime,
                 deadline: float) -> Review:
    """Fail closed. A valid JSON model verdict is not equal to verified evidence."""
    if not blocks or deadline - time.monotonic() < 7:
        return Review(gaps=("critic_budget_or_empty",))
    safe = deterministic_filter(blocks, facts)
    if not safe:
        return Review(gaps=("no_grounded_synthesis",))
    try:
        raw = await runtime.complete_text(
            [
                {"role": "system", "content": (
                    "你是独立审查者。逐段检查综合分析是否真正能由所附原文引句支持，"
                    "是否存在跨政策混淆、目标当作结果、错误数字、虚构完整性。"
                    "仅在引用足够支持段落全部事实时批准。"
                    "不批准不代表删除已有核验通过的研究原文块。"
                    "严格返回 JSON {\"approved_ids\":[\"S1\"],"
                    "\"gaps\":[\"具体问题\"],\"comments\":\"简短意见\"}。"
                    "不可发明未见过的其他证据。"
                )},
                {"role": "user", "content": json.dumps({
                    "blocks": [
                        {"id": x.id, "text": x.text,
                         "evidence": [
                             {"id": fid, "text": facts[fid]["text"][:280],
                              "quotes": facts[fid].get("quotes", [])[:3]}
                             for fid in x.fact_ids
                         ]}
                        for x in safe
                    ],
                }, ensure_ascii=False)},
            ],
            max_tokens=450,
            timeout=min(18.0, max(0.5, deadline - time.monotonic() - 2)),
            purpose="multi_critic_review",
        )
        response = json.loads(raw.strip())
        if not isinstance(response, dict):
            raise ValueError("not an object")
        approved = response.get("approved_ids")
        gaps = response.get("gaps")
        if not isinstance(approved, list) or not isinstance(gaps, list):
            raise ValueError("invalid review contract")
        permitted = {item.id for item in safe}
        if not all(isinstance(x, str) for x in approved + gaps):
            raise ValueError("invalid review fields")
        return Review(
            approved_ids=tuple(dict.fromkeys(x for x in approved if x in permitted)),
            gaps=tuple(gaps[:8]),
            comments=str(response.get("comments") or "")[:360],
            performed=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        # A model outage cannot discard already independently verified worker facts.
        return Review(gaps=("critic_unavailable",))
