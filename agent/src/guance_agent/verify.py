"""引文逐字核验：引句只能来自本次实际提供、且后端原文明确认的已见范围。

这一步只做字面核对，不判断语义是否支撑结论；输出必须按这个边界说明。
"""
import asyncio
import re

from .output import rebuild_trusted_answer

MAX_QUOTES = 20          # 问答：一次运行最多核这么多句，避免核验拖垮回合预算
EXTRACT_MAX_QUOTES = 120  # 提取：六个维度各若干条、每条一两句引文，20 句明显不够


def quote_budget(result) -> int:
    """提取任务要核的引文天生比问答多，给它更大但仍有限的上限。"""
    return EXTRACT_MAX_QUOTES if getattr(result, "task_type", "qa") == "extract" else MAX_QUOTES
NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
# 模型爱用有序列表，行首的 "1." "2、" 不是数据，先去掉再抽数字
LIST_MARKER = re.compile(r"(?m)^\s*\d+\s*[.．、)）]\s*")


async def verify_quotes(result, ctx) -> int:
    """剔除不合格引文，返回被丢弃的引文条数。

    后端没有 verify_quote（mock/sample）时直接跳过：宁可少做，也不假装核过。
    """
    checker = getattr(ctx.kb, "verify_quote", None) if ctx else None
    if not result.claims:
        return 0
    checked = approved_count = rejected = unverified = removed_claims = removed_quotes = 0
    budget = quote_budget(result)
    kept_claims = []
    for claim in result.claims:
        if not claim.quotes:
            kept_claims.append(claim)
            continue
        approved = []
        for quote in claim.quotes:
            if checker is None or checked >= budget:
                unverified += 1
                continue
            checked += 1
            if await _approved(checker, ctx, claim, quote):
                approved.append(quote)
                approved_count += 1
            else:
                rejected += 1
        # 一条结论的所有模型引句都必须逐字核验；否则不能保留其政策判断。
        if len(approved) != len(claim.quotes):
            removed_claims += 1
            removed_quotes += len(claim.quotes)
            continue
        kept_claims.append(claim)
    result.meta["quote_verification"] = {
        "checked": checked,
        "approved": approved_count,
        "rejected": rejected,
        "unverified": unverified,
        "removed_claims": removed_claims,
        "removed_quotes": removed_quotes,
    }
    if unverified:
        reason = "当前后端不支持逐字核验" if checker is None else "超出本次核验上限"
        result.warnings.append(f"有 {unverified} 句引文{reason}，未逐字核对")
    if rejected:
        result.warnings.append(f"有 {rejected} 句引文未能在已提供的原文范围内逐字核对")
    if removed_claims:
        result.claims = kept_claims
        result.warnings.append(f"为避免未核验引文被采信，已移除 {removed_claims} 条结论及其 {removed_quotes} 句引文")
        rebuild_trusted_answer(result, ctx, "quote_verification")
    return removed_quotes


async def _approved(checker, ctx, claim, quote) -> bool:
    """任一同版本已见块能证明这句引文即通过；核验异常或超时按不通过。"""
    for chunk_id in claim.chunk_ids:
        ref = ctx.evidence.get(chunk_id)
        if ref is None or not ref.observed_ranges:
            continue
        try:
            ok = await asyncio.wait_for(
                asyncio.to_thread(checker, chunk_id, quote, ref.observed_ranges,
                                  document_id=ref.document_id, generation_id=ref.generation_id),
                timeout=ctx.settings.tool_timeout)
        except Exception:                      # noqa: BLE001 - 核验失败按不通过
            continue
        if ok:
            return True
    return False


async def verify_numbers(result, ctx) -> int:
    """核对每条结论自身证据中的数字字面量，不把字面存在误报为语义支持。"""
    getter = getattr(ctx.kb, "get_chunk_source", None) if ctx else None
    if not result.claims:
        return 0
    cache, failed, problems, rejected_claims = {}, set(), 0, []
    for claim in result.claims:
        tokens = _tokens(claim.text)
        if not tokens:
            continue
        result.meta["number_verification"] = "literal_only"
        own = await _seen_text(ctx, getter, [claim], cache, failed) if getter else None
        if own is None:
            result.warnings.append("数字核验未完成：无法取回该结论所引证据的原文")
            problems += 1
            rejected_claims.append(claim)
            continue
        missing = [token for token in tokens if not _present(token, own)]
        if missing:
            result.warnings.append(
                "结论「" + _clip(claim.text) + "」中的 " + "、".join(missing[:6])
                + " 未在其所引原文中出现")
            problems += len(missing)
            rejected_claims.append(claim)

    # 答案中的数字必须被某条结论承接；不允许跨结论/跨证据作宽松匹配。
    bound = {token for claim in result.claims for token in _tokens(claim.text)}
    # 用户自己写进问题的数字（planid、年份、指标）不是模型编的，不能当作无据数字
    asked = set(_tokens(getattr(ctx, "question", "") or ""))
    orphan = [token for token in _tokens(result.answer)
              if token not in bound and token not in asked]
    if orphan:
        result.meta["number_verification"] = "literal_only"
        result.warnings.append("答案中的 " + "、".join(orphan[:6]) + " 未与任何结论绑定，无法核验")
        problems += len(orphan)
        rejected_claims = list(result.claims)

    if rejected_claims:
        rejected_ids = {id(claim) for claim in rejected_claims}
        result.claims = [claim for claim in result.claims if id(claim) not in rejected_ids]
        result.warnings.append("数字核验只确认字面存在，不代表其单独足以支持政策结论")
        rebuild_trusted_answer(result, ctx, "number_verification")
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
    """只看这些结论自己同版本的已见证据；取不到时不能当作通过。"""
    refs = []
    for claim in claims:
        for chunk_id in dict.fromkeys(claim.chunk_ids):
            ref = ctx.evidence.get(chunk_id)
            if ref is not None and ref.observed_ranges:
                refs.append(ref)
    parts = []
    for ref in refs:
        key = (ref.chunk_id, ref.document_id, ref.generation_id)
        source = cache.get(key)
        if source is None:
            if key in failed:
                continue
            try:
                source = await asyncio.wait_for(
                    asyncio.to_thread(getter, ref.chunk_id, document_id=ref.document_id,
                                      generation_id=ref.generation_id),
                    timeout=ctx.settings.tool_timeout)
            except Exception:                  # noqa: BLE001 - 不可读取就不参与判断
                failed.add(key)
                continue
            cache[key] = source
        base = source.chunk_char_start or 0
        for start, end in ref.observed_ranges:
            parts.append(source.content[max(0, start - base):max(0, end - base)])
    return "\n".join(parts) if parts else None
