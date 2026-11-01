"""Conservative fallback to verified verbatim extracts on QA model failure.

Never infer conclusions from retrieved text, and never turn a failed LLM answer
into an alleged completed policy analysis.
"""
from __future__ import annotations

import asyncio
import re
import time

from .schemas import Claim

RECOVERABLE_ERRORS = frozenset({"OUTPUT_PARSE", "MODEL_TIMEOUT", "MAX_TURNS"})
MAX_EXCERPTS = 3
MAX_QUOTE_CHARS = 220
MAX_SENTENCE_SCAN_CHARS = 360


def complete_excerpt(text: str) -> str | None:
    """Only return a bounded complete sentence copied exactly from original."""
    visible = text.lstrip()
    if not visible:
        return None
    within = [
        match.end() for match in re.finditer(r"[。；;！？!?\n]", visible[:MAX_QUOTE_CHARS])
        if match.end() >= 16
    ]
    if within:
        return visible[:within[-1]].strip()
    # The first sentence may be longer than the ideal excerpt size; avoid
    # slicing it mid-word, but preserve a firm total fallback budget.
    longer = re.search(r"[。；;！？!?\n]", visible[:MAX_SENTENCE_SCAN_CHARS])
    if longer and longer.end() >= 16:
        return visible[:longer.end()].strip()
    return None



async def recover_read_excerpts(result, ctx, deadline: float) -> bool:
    """Recover only original text witnessed by kb_read and independently verified."""
    if (result.task_type != "qa" or result.status != "failed" or result.error is None
            or result.error.code not in RECOVERABLE_ERRORS or ctx is None):
        return False
    getter = getattr(ctx.kb, "get_chunk_source", None)
    checker = getattr(ctx.kb, "verify_quote", None)
    if not callable(getter) or not callable(checker):
        return False
    route = getattr(ctx, "route", None)
    allowed = set(getattr(route, "planids", ()) or ()) if getattr(
        route, "kind", None) == "objects" else None
    refs, claims = [], []
    for cid in ctx.evidence.ids():
        if len(claims) >= MAX_EXCERPTS or deadline - time.monotonic() < 2:
            break
        ref = ctx.evidence.get(cid)
        if (ref is None or ref.source != "kb_read" or not ref.observed_ranges
                or (allowed is not None and ref.planid not in allowed)):
            continue
        try:
            remaining = min(2.0, deadline - time.monotonic() - 0.5)
            if remaining <= 0:
                break
            original = await asyncio.wait_for(asyncio.to_thread(
                getter, cid, document_id=ref.document_id,
                generation_id=ref.generation_id), timeout=remaining)
            if (getattr(original, "planid", None) != ref.planid
                    or (getattr(original, "document_id", None) is not None
                        and original.document_id != ref.document_id)
                    or (getattr(original, "generation_id", None) is not None
                        and original.generation_id != ref.generation_id)):
                continue
            base = getattr(original, "chunk_char_start", None) or 0
            content = getattr(original, "content", None)
            if not isinstance(content, str):
                continue
            quote = None
            for start, end in ref.observed_ranges:
                if end <= start or start < base or end > base + len(content):
                    continue
                candidate = complete_excerpt(content[start - base:end - base])
                if candidate is not None:
                    quote = candidate
                    break
            if not quote:
                continue
            verified = await asyncio.wait_for(asyncio.to_thread(
                checker, cid, quote, ref.observed_ranges,
                document_id=ref.document_id, generation_id=ref.generation_id),
                timeout=remaining)
            if not verified:
                continue
        except Exception:  # noqa: BLE001 - a failed source check must never be included
            continue
        refs.append(ref)
        claims.append(Claim(text=quote, chunk_ids=[cid], quotes=[quote]))
    if not claims:
        return False

    code = result.error.code
    result.meta["original_execution_error"] = code
    result.meta["execution_recovery"] = {
        "kind": "verified_verbatim_excerpts", "status": "partial",
        "original_error": code, "excerpt_count": len(claims),
    }
    result.claims = claims
    result.evidence_refs = refs
    result.error = None
    result.status = "completed"
    result.evidence_status = "insufficient"
    sources = "\n".join(
        "- 《" + ref.title + "》·" + (ref.area or "地区未标注")
        + "·" + (ref.fyp or "时期未标注")
        + "·planid=" + ref.planid + "·" + ref.section_path
        for ref in refs
    )
    result.answer = (
        "模型未能完成本轮研究（" + code + "）。以下仅为已经逐字核验的政策原文节选，"
        "不是研究分析或政策结论；具体引文见下方证据。\n\n"
        + "\n".join("- " + claim.text for claim in claims)
        + "\n\n原文出处：\n" + sources
    )
    result.warnings.append(
        "本轮模型执行失败；仅提供已读原文的逐字核验节选，未生成综合研究结论。")
    return True


def recover_empty_search_failure(result, ctx) -> bool:
    """Describe actual empty searches; never claim the policy does not exist."""
    if (result.task_type != "qa" or result.status != "failed"
            or result.error is None or result.error.code != "OUTPUT_PARSE"
            or ctx is None or ctx.evidence.ids()):
        return False
    calls = list(ctx.tool_calls)
    if not calls or any(call.get("status") not in {"empty", "no_text", "not_found"}
                        for call in calls):
        return False
    code = result.error.code
    result.meta["original_execution_error"] = code
    result.meta["execution_recovery"] = {
        "kind": "empty_search_only", "status": "insufficient",
        "original_error": code,
    }
    result.status, result.error, result.evidence_status = "completed", None, "insufficient"
    result.answer = (
        "本轮对知识库的检索没有取得可供引用的政策正文，模型最终输出也未能"
        "正确解析；无法依据本次结果回答。这里的“未检索到”不代表相关政策不存在。"
    )
    result.warnings.append("模型输出格式异常；没有发布任何未经核验的政策事实。")
    return True
