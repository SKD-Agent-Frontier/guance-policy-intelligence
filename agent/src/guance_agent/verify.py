"""引文逐字核验：引句只能来自本次实际提供、且后端原文明确认的已见范围。

这一步只做字面核对，不判断语义是否支撑结论；输出必须按这个边界说明。
"""
import asyncio
import re

from .output import rebuild_trusted_answer

MAX_QUOTES = 20          # 问答：一次运行最多核这么多句，避免核验本身拖垮回合预算
EXTRACT_MAX_QUOTES = 120  # 提取：六个维度各若干条、每条一两句，实测 20 句远远不够


def quote_budget(result) -> int:
    """提取任务要核的引文天生比问答多，给它更大但仍有限的上限。"""
    return EXTRACT_MAX_QUOTES if getattr(result, "task_type", "qa") == "extract" else MAX_QUOTES
NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
# 模型爱用有序列表，行首的 "1." "2、" 不是数据，先去掉再抽数字
LIST_MARKER = re.compile(r"(?m)^\s*\d+\s*[.．、)）]\s*")


def _squash(text: str) -> tuple[str, list[int]]:
    """去掉空白并记录每个字符在原串里的位置，用于把归一化命中映射回原坐标。"""
    index = [i for i, ch in enumerate(text) if not ch.isspace()]
    return "".join(text[i] for i in index), index


def snap_quote(quote: str, text: str) -> str:
    """把模型的引文回贴到原文：只吸收空白差异，找不到就原样返回。

    不放松核验标准——回贴后存进结果的仍是原文的精确切片；
    只是把「模型少打一个空格/多换一次行」这类误差吸收掉。
    """
    if not quote or not text or quote in text:
        return quote
    needle, _ = _squash(quote)
    if len(needle) < 6:                 # 太短的片段不值得回贴，交给核验判
        return quote
    haystack, index = _squash(text)
    at = haystack.find(needle)
    if at < 0:
        return quote
    return text[index[at]: index[at + len(needle) - 1] + 1]


def snap_claims(claims, ctx, getter) -> int:
    """逐条把引文回贴到本条所引证据的已见范围；返回改写句数。"""
    if getter is None or not claims:
        return 0
    changed = 0
    for claim in claims:
        for chunk_id in claim.chunk_ids:
            ref = ctx.evidence.get(chunk_id)
            if ref is None or not ref.observed_ranges:
                continue
            try:
                source = getter(chunk_id, document_id=ref.document_id,
                                generation_id=ref.generation_id)
            except Exception:               # noqa: BLE001 - 取不到就跳过回贴
                continue
            base = source.chunk_char_start or 0
            windows = [source.content[max(0, a - base):max(0, b - base)]
                       for a, b in ref.observed_ranges]
            for i, quote in enumerate(claim.quotes):
                for window in windows:
                    snapped = snap_quote(quote, window)
                    if snapped != quote:
                        claim.quotes[i] = snapped
                        changed += 1
                        break
                else:
                    continue
                break
    return changed



SENTENCE = re.compile(r"[^。；;！？!?\n]{5,}[。；;！？!?]?")

def _numbers(text: str) -> list[str]:
    return NUMBER.findall(text or "")


def locate_quotes(claim, ctx, getter) -> str | None:
    """在 claim 所引 chunk 里定位支持该要素的原文句，返回原文精确切片。

    支持性不是靠"文字能匹配"就算过：必须同时满足
      1) 命中至少一个定位线索 anchors（模型说"这一段讲的是这个"）
      2) 要素正文里的每个数字都出现在同一句（避免张冠李戴到同 chunk 的另一句）
    找不到就返回 None，由调用方丢弃该要素，不强行保留。
    """
    anchors = [a for a in (claim.anchors or []) if a and len(a) >= 2]
    if not anchors or getter is None:
        return None
    want_numbers = _numbers(claim.text)
    for chunk_id in claim.chunk_ids:
        ref = ctx.evidence.get(chunk_id)
        if ref is None or not ref.observed_ranges:
            continue
        try:
            source = getter(chunk_id, document_id=ref.document_id,
                            generation_id=ref.generation_id)
        except Exception:                   # noqa: BLE001 - 取不到原文就不定位
            continue
        base = source.chunk_char_start or 0
        # 各已见范围用换行隔开：跨范围拼出来的"句子"在原文里并不连续，
        # 逐字核验必然失败。换行让 SENTENCE 只在单段之内匹配。
        for a, b in ref.observed_ranges:
            window = source.content[max(0, a - base):max(0, b - base)]
            for match in SENTENCE.finditer(window):
                # 窗口可能从句子中间开始：首个不完整的残句一律跳过，
                # 只用从句子边界起算的完整句，避免引文从半句开始
                if match.start() > 0 and window[match.start() - 1] not in "。；;！？!?\n":
                    continue
                sentence = match.group(0)
                if not any(anchor in sentence for anchor in anchors):
                    continue
                if any(number not in sentence for number in want_numbers):
                    continue
                return sentence        # 原样返回，不 strip，保证仍是精确子串
    return None


def fill_quotes_from_anchors(claims, ctx, getter, result=None) -> tuple[int, int]:
    """把定位线索回填成原文引文；返回 (成功定位数, 丢弃要素数)。"""
    if getter is None:
        return 0, 0
    filled = dropped = 0
    kept = []
    for claim in claims:
        if claim.quotes:                    # 已有引文的（qa 等）原样保留
            kept.append(claim)
            continue
        quote = locate_quotes(claim, ctx, getter)
        if quote is None:
            dropped += 1                    # 定位不到有效证据：不强行保留
            continue
        claim.quotes = [quote]
        filled += 1
        kept.append(claim)
    claims[:] = kept
    if dropped and result is not None:
        # 要素被删掉后正文里可能还留着这些结论，必须同步重建，复用既有实现
        rebuild_trusted_answer(result, ctx, "anchor_lookup")
    return filled, dropped


async def verify_quotes(result, ctx) -> int:
    """剔除不合格引文，返回被丢弃的引文条数。

    后端没有 verify_quote（mock/sample）时直接跳过：宁可少做，也不假装核过。
    """
    checker = getattr(ctx.kb, "verify_quote", None) if ctx else None
    if not result.claims:
        return 0
    checked = approved_count = rejected = unverified = removed_claims = removed_quotes = 0
    kept_claims = []
    budget = quote_budget(result)
    # 先把引文回贴到原文，吸收空白差异；回贴后仍是原文的精确切片
    filled, unfound = fill_quotes_from_anchors(
        result.claims, ctx, getter=getattr(ctx.kb, "get_chunk_source", None), result=result)
    if filled or unfound:
        result.meta["quotes_located"] = filled
        result.meta["elements_dropped_no_evidence"] = unfound
    snapped = snap_claims(result.claims, ctx, getter=getattr(ctx.kb, "get_chunk_source", None))
    if snapped:
        result.meta["quotes_snapped"] = snapped
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
    # 用户自己写进问题的数字（planid、年份、指标）不是模型编的，不能当无据数字
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
