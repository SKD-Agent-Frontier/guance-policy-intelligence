"""引文逐字核验：引句只能来自本次实际提供、且后端原文明确认的已见范围。

这一步只做字面核对，不判断语义是否支撑结论；输出必须按这个边界说明。
"""
import asyncio
import re

MAX_QUOTES = 20   # 一次运行最多核这么多句，避免核验本身拖垮回合预算
NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
# 模型爱用有序列表，行首的 "1." "2、" 不是数据，先去掉再抽数字
LIST_MARKER = re.compile(r"(?m)^\s*\d+\s*[.．、)）]\s*")


async def verify_quotes(result, ctx) -> int:
    """剔除不合格引文，返回被丢弃的引文条数。

    后端没有 verify_quote（mock/sample）时直接跳过：宁可少做，也不假装核过。
    """
    checker = getattr(ctx.kb, "verify_quote", None) if ctx else None
    if checker is None or not result.claims:
        return 0
    dropped, checked, kept_claims = 0, 0, []
    for claim in result.claims:
        if not claim.quotes or checked >= MAX_QUOTES:
            kept_claims.append(claim)
            continue
        approved = []
        for quote in claim.quotes:
            checked += 1
            if await _approved(checker, ctx, claim, quote):
                approved.append(quote)
            else:
                dropped += 1
            if checked >= MAX_QUOTES:
                break
        # 有引文但一句都没通过：这条结论失去原文支撑，整条不采信
        if claim.quotes and not approved:
            dropped += len(claim.quotes) - (1 if dropped else 0)
            continue
        kept_claims.append(claim.model_copy(update={"quotes": approved}))
    if dropped:
        result.claims = kept_claims
        result.warnings.append(
            f"有 {dropped} 句引文未能在已提供的原文范围内逐字核对，已移除")
        if not result.claims:
            result.evidence_status = "insufficient"
    return dropped


async def _approved(checker, ctx, claim, quote) -> bool:
    """任一所引块能证明这句引文即通过；核验本身出错按不通过处理。"""
    for chunk_id in claim.chunk_ids:
        ref = ctx.evidence.get(chunk_id)
        if ref is None or not ref.observed_ranges:
            continue
        try:
            ok = await asyncio.to_thread(checker, chunk_id, quote, ref.observed_ranges)
        except Exception:                      # noqa: BLE001 - 核验失败按不通过
            continue
        if ok:
            return True
    return False


async def verify_numbers(result, ctx) -> int:
    """答案正文里的数字必须出现在所引原文的已见范围内。

    只查数字，不查语义：数字是政策问答里最容易被编造、也最容易确定性核对的部分。
    带百分号的数字缺失时降级为证据不足——这类数字几乎只能是凭空生成。
    """
    getter = getattr(ctx.kb, "get_chunk_source", None) if ctx else None
    if getter is None or not result.claims:
        return 0
    tokens = list(dict.fromkeys(NUMBER.findall(LIST_MARKER.sub("", result.answer))))
    if not tokens:
        return 0
    seen = await _seen_text(ctx, getter, result.claims)
    if not seen:
        return 0                      # 拿不到原文就不下结论，避免误报
    missing = [token for token in tokens if not _present(token, seen)]
    if not missing:
        return 0
    result.warnings.append(
        f"答案中有 {len(missing)} 个数字未在所引原文的已见范围内出现："
        + "、".join(missing[:8]))
    if any("%" in token for token in missing):
        result.evidence_status = "insufficient"
    return len(missing)


def _present(token: str, text: str) -> bool:
    """左右都不能紧邻数字，避免 100 命中 1000。"""
    start = 0
    while True:
        index = text.find(token, start)
        if index < 0:
            return False
        before = text[index - 1] if index else ""
        after = text[index + len(token)] if index + len(token) < len(text) else ""
        if not before.isdigit() and not after.isdigit():
            return True
        start = index + 1


async def _seen_text(ctx, getter, claims) -> str:
    """把每条所引证据的已见范围拼成文本；取不到原文的块跳过。"""
    ids = dict.fromkeys(cid for claim in claims for cid in claim.chunk_ids)
    sources, parts = {}, []
    for chunk_id in ids:
        ref = ctx.evidence.get(chunk_id)
        if ref is None or not ref.observed_ranges:
            continue
        source = sources.get(chunk_id)
        if source is None:
            try:
                source = await asyncio.to_thread(getter, chunk_id)
            except Exception:          # noqa: BLE001 - 取不到原文就不参与判断
                continue
            sources[chunk_id] = source
        base = source.chunk_char_start or 0
        for start, end in ref.observed_ranges:
            parts.append(source.content[max(0, start - base):max(0, end - base)])
    return "\n".join(parts)
