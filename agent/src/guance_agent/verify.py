"""引文逐字核验：引句只能来自本次实际提供、且后端原文明确认的已见范围。

这一步只做字面核对，不判断语义是否支撑结论；输出必须按这个边界说明。
"""
import asyncio

MAX_QUOTES = 20   # 一次运行最多核这么多句，避免核验本身拖垮回合预算


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
