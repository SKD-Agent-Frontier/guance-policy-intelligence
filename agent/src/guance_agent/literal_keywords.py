"""Literal policy-keyword boundary for short, explicit topic-search requests.

BM25/embedding candidates are not proof that a full phrase appeared in a cited source.
Keep candidate discovery and final acceptance separate.
"""
import re


def literal_keyword(question: str) -> str | None:
    """Recognize an explicit 'X相关政策' topic without splitting X into component words."""
    first = re.split(r"[；;。\n]", question or "", maxsplit=1)[0].strip().rstrip("?!？！ ")
    first = re.sub(
        r"^(?:请(?:帮我)?|帮我|我想(?:要)?|想|麻烦)?"
        r"(?:搜索|检索|查找|查询|找|了解|梳理|整理|看看|研究)\s*", "", first,
    )
    first = re.sub(r"^(?:关于|有关|针对|围绕)\s*", "", first)
    match = re.fullmatch(
        r"(?P<term>[\u4e00-\u9fffA-Za-z0-9]{2,32}?)(?:的)?"
        r"(?:相关|有关)?(?:有哪些|有哪一些)?政策(?:有哪些|有哪一些|是什么|吗)?", first,
    )
    if not match:
        return None
    term = match.group("term")
    return term if term not in {"相关", "有关", "全部", "所有", "哪些", "这些"} else None


def literal_hit(hit, keyword: str) -> bool:
    """Only title/section/body containing the whole contiguous phrase count as a hit."""
    return any(keyword in (value or "") for value in (
        getattr(hit, "title", None), getattr(hit, "section_path", None),
        getattr(hit, "content", None),
    ))


def enforce_literal_result(result, ctx) -> None:
    """Fail closed on claims from unrelated candidate snippets; do not echo raw LLM answer."""
    keyword = getattr(ctx, "literal_keyword", None)
    if not keyword or result.status != "completed" or result.error is not None:
        return
    eligible = getattr(ctx, "literal_hit_ids", set())
    references = {ref.chunk_id: ref for ref in result.evidence_refs}
    previous = len(result.claims)
    kept = []
    for claim in result.claims:
        matching_ids = [
            cid for cid in claim.chunk_ids if cid in references and (
                cid in eligible or keyword in (references[cid].title or "")
                or any(keyword in quote for quote in claim.quotes)
            )
        ]
        if matching_ids:
            kept.append(claim.model_copy(update={"chunk_ids": matching_ids}))
    result.claims = kept
    valid_ids = {cid for claim in kept for cid in claim.chunk_ids}
    result.evidence_refs = [ref for ref in result.evidence_refs if ref.chunk_id in valid_ids]
    removed = previous - len(kept)
    result.meta["literal_keyword"] = dict(
        term=keyword, mode="contiguous_substring", removed_claims=removed,
        accepted_claims=len(kept),
    )
    if removed:
        result.evidence_status = "insufficient"
        result.warnings.append(
            f"已排除 {removed} 条未能证明包含完整关键词「{keyword}」的结论；"
            "分词命中或语义相似不等于关键词命中。"
        )
    if kept:
        result.answer = f"以下仅列出与完整关键词「{keyword}」有可追溯原文关联的结论：\n" + "\n".join(
            f"- {claim.text}" for claim in kept
        )
    else:
        result.evidence_status = "insufficient"
        result.answer = (
            f"本轮没有获得可核验的完整关键词「{keyword}」匹配证据。"
            "这不代表相关政策不存在；不会将拆分词或相近概念当作命中。"
        )
    result.meta["answer_rebuilt"] = "literal_keyword"
