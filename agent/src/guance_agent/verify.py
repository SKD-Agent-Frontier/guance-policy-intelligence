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
    dropped, checked, skipped, kept_claims = 0, 0, 0, []
    for claim in result.claims:
        if not claim.quotes:
            kept_claims.append(claim)
            continue
        if checked >= MAX_QUOTES:
            skipped += len(claim.quotes)
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
    if skipped:
        result.warnings.append(f"有 {skipped} 句引文超出本次核验上限，未逐字核对")
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
    """数字必须与本条结论所引的原文对应，不能靠别的结论的证据蒙混过关。

    只查数字，不查语义。三件事都必须做到：
    1) 按 Claim 分别核对，只拿这条结论自己引的证据；
    2) 任何数字对不上都降级，不区分是不是百分号；
    3) 取不到原文时明确记为「核验未完成」，不能当成通过。
    """
    getter = getattr(ctx.kb, "get_chunk_source", None) if ctx else None
    if getter is None or not result.claims:
        return 0
    cache, failed, problems = {}, set(), 0
    for claim in result.claims:
        tokens = _tokens(claim.text)
        if not tokens:
            continue
        own = await _seen_text(ctx, getter, [claim], cache, failed)
        if own is None:
            result.warnings.append("数字核验未完成：无法取回该结论所引证据的原文")
            problems += 1
            continue
        missing = [token for token in tokens if not _present(token, own)]
        if missing:
            result.warnings.append(
                "结论「" + _clip(claim.text) + "」中的 " + "、".join(missing[:6])
                + " 未在其所引原文中出现")
            problems += len(missing)

    # 没有结论承接的数字（叙述性表述、章节编号等）：放宽到任一所引证据里找。
    # 仍找不到才算问题——不能因为「没被某条 claim 复述」就一律判错。
    bound = {token for claim in result.claims for token in _tokens(claim.text)}
    orphan = [token for token in _tokens(result.answer) if token not in bound]
    if orphan:
        union = await _seen_text(ctx, getter, result.claims, cache, failed)
        if union is None:
            result.warnings.append("数字核验未完成：无法取回所引证据的原文")
            problems += 1
        else:
            stray = [token for token in orphan if not _present(token, union)]
            if stray:
                result.warnings.append(
                    "答案中的 " + "、".join(stray[:6]) + " 未在所引原文的已见范围内出现")
                problems += len(stray)

    if problems:
        result.evidence_status = "insufficient"
    return problems


def _tokens(text: str) -> list[str]:
    """剥掉有序列表编号后抽数字，去重保序。"""
    return list(dict.fromkeys(NUMBER.findall(LIST_MARKER.sub("", text))))


def _clip(text: str, width: int = 24) -> str:
    return text if len(text) <= width else text[:width] + "…"


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


async def _seen_text(ctx, getter, claims, cache, failed) -> str | None:
    """只看这些结论自己引的证据；一个都取不到时返回 None，不能当成空串通过。"""
    ids = dict.fromkeys(chunk_id for claim in claims for chunk_id in claim.chunk_ids)
    parts = []
    for chunk_id in ids:
        ref = ctx.evidence.get(chunk_id)
        if ref is None or not ref.observed_ranges:
            continue
        source = cache.get(chunk_id)
        if source is None:
            if chunk_id in failed:
                continue
            try:
                source = await asyncio.to_thread(getter, chunk_id)
            except Exception:          # noqa: BLE001 - 取不到原文就不参与判断
                failed.add(chunk_id)
                continue
            cache[chunk_id] = source
        base = source.chunk_char_start or 0
        for start, end in ref.observed_ranges:
            parts.append(source.content[max(0, start - base):max(0, end - base)])
    return "\n".join(parts) if parts else None
