"""引文逐字核验：引句只能来自本次实际提供、且后端原文明确认的已见范围。

这一步只做字面核对，不判断语义是否支撑结论；输出必须按这个边界说明。
"""
import asyncio
import re
import unicodedata

from .output import rebuild_trusted_answer

MAX_QUOTES = 20          # 问答：一次运行最多核这么多句，避免核验本身拖垮回合预算
EXTRACT_MAX_QUOTES = 120  # 提取：六个维度各若干条、每条一两句，实测 20 句远远不够


def quote_budget(result) -> int:
    """按任务类型选引句数量上限，不改变逐句核验的内容与已读范围要求。

    比较可有八个对象、多个维度及每格多句原文，沿用 Extract 的 120 句上限，
    避免被 QA 的 20 句额度截断。未知类型仍用普通上限，不因此获得无限核验预算。
    """
    return (EXTRACT_MAX_QUOTES if getattr(result, "task_type", "qa")
            in ("extract", "compare", "compare_plans") else MAX_QUOTES)
NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
# 模型爱用有序列表：行首的 "1." 与句中标点后的 "1）" "2、" 都不是数据，先去掉再抽数字
LIST_MARKER = re.compile(r"(?m)(?:^[ \t]*|[；;。：:!？!?]\s*)\d{1,3}\s*[.．、)）]\s*")


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
        if not claim.anchors and getattr(result, "task_type", "qa") != "extract":
            kept.append(claim)               # qa/业务无引文路径不要求 anchor 回填
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


# 政策身份字段：planid / 篇名 / 地区 / 规划期 / 章节路径。
# 身份由本轮真实检索返回的元数据核对，不要求出现在原文引句中；
# 政策事实（金额、百分比、人数、年份、期限、量化指标）仍必须逐条在原文中出现。
IDENTITY_FIELDS = ("planid", "title", "area", "fyp", "section_path")
IDENTITY_MASK = "□"          # 非数字占位符：身份值排除后不再参与数字事实核验
FACT_UNITS = frozenset("元万亿人年月日个家项次倍岁%％")
FACT_LEADS = ("达到", "为", "约", "共", "增长", "提高", "下降", "补贴", "投入",
              "规模", "超过", "不足", "占比", "覆盖", "金额", "标准", "额度",
              "比例", "总量", "共计", "累计", "不低于", "不超过")
IDENTITY_MARKERS = (
    re.compile(r"(?:planid|policyid|政策编号|编号)\s*[=:：]?\s*(?:为|是)?\s*"
               r"([0-9０-９A-Za-zＡ-Ｚａ-ｚ_-]{1,64})", re.IGNORECASE),
    re.compile(r"([0-9０-９]{1,64})\s*号政策"),
)
TITLE_REF = re.compile(r"《([^《》]{2,80})》|〈([^〈〉]{2,80})〉")


def _identity_values(refs) -> set[str]:
    """从真实来源元数据取出可核对的身份值；空值不参与。"""
    values = set()
    for ref in refs or ():
        for field in IDENTITY_FIELDS:
            value = getattr(ref, field, None)
            if isinstance(value, str) and value.strip():
                values.add(value.strip())
    return values


def _all_refs(ctx, result) -> list:
    """本轮实际返回过的来源：结论引用到的证据，加上证据注册表中全部已登记来源。"""
    refs = list(getattr(result, "evidence_refs", None) or [])
    registry = getattr(ctx, "evidence", None) if ctx is not None else None
    ids = getattr(registry, "ids", None)
    if callable(ids):
        refs.extend(ref for ref in (registry.get(cid) for cid in ids()) if ref is not None)
    elif isinstance(registry, dict):
        refs.extend(ref for ref in registry.values() if ref is not None)
    seen, unique = set(), []
    for ref in refs:
        key = getattr(ref, "chunk_id", None) or id(ref)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def _user_identity_values(ctx) -> set[str]:
    """用户自己指定/问题文本里出现的身份编号不是模型生成，属于输入身份。"""
    values = set()
    for planid in getattr(ctx, "scope", None) or ():
        if isinstance(planid, str) and planid.strip():
            values.add(planid.strip())
    route = getattr(ctx, "route", None)
    for planid in getattr(route, "planids", None) or ():
        if isinstance(planid, str) and planid.strip():
            values.add(planid.strip())
    return values


_FACT_TRIM = " \t，,。；;：:、（）()【】[]\"'“”‘’"
_FACT_TRIM_TAIL = " \t，,。；;：:、（）()【】[]\"'“”‘’"


def _fact_position(text: str, start: int, end: int) -> bool:
    """数字出现在计量位置时是政策事实，不能当身份编号放行。

    允许用冒号、括号、逗号把数字与单位/计量动词隔开（如「补贴：23132（万元）」），
    否则这些真实数字会被身份掩码吞掉。
    """
    tail = text[end:].lstrip(_FACT_TRIM_TAIL)
    if tail[:1] and tail[:1] in FACT_UNITS:
        return True
    head = text[:start].rstrip(_FACT_TRIM)
    return any(head.endswith(lead) for lead in FACT_LEADS)


def _fullwidth_digit_class(value: str) -> str:
    """ASCII 数字同时匹配全角数字，避免真实 planid 的全角写法被误降级。"""
    parts = []
    for char in value:
        if "0" <= char <= "9":
            parts.append("[" + char + chr(ord(char) + 0xFEE0) + "]")
        else:
            parts.append(re.escape(char))
    return "".join(parts)


def _marker_spans(text: str) -> list[tuple[int, int]]:
    """显式身份写法中编号值的位置；这些位置永远按身份处理，不被计量词误判。"""
    spans = []
    for pattern in IDENTITY_MARKERS:
        spans.extend((match.start(1), match.end(1)) for match in pattern.finditer(text))
    return spans


def _is_cjk_word(char: str) -> bool:
    return char.isalpha() and ord(char) > 127


def _cjk_neighbor(text: str, start: int, end: int) -> bool:
    """去掉标点与空白后仍紧邻中文词：更像计量表述（“23132（吨）”）。"""
    before = text[:start].rstrip(_FACT_TRIM)
    after = text[end:].lstrip(_FACT_TRIM_TAIL)
    return ((bool(before) and _is_cjk_word(before[-1]))
            or (bool(after) and _is_cjk_word(after[0])))


_OPEN_BRACKETS = "（([【"
_IDENTITY_TRAIL = "，,、）)]】;；"


def _parenthesized_identity(text: str, start: int, end: int) -> bool:
    """紧邻左括号且后接分隔/右括号（如“（23132，苏州市）”）按身份处理。

    事实优先：只有明确的括号列举位置才放行，避免“计划：23132（吨）”这类
    计量写法借身份提示词绕过事实核验。
    """
    before = text[:start].rstrip(" \t")
    after = text[end:].lstrip(" \t")
    if not before or before[-1] not in _OPEN_BRACKETS:
        return False
    return not after or after[0] in _IDENTITY_TRAIL


def _mask_value(text: str, value: str, *, strict: bool) -> str:
    """替换身份值。

    strict=True 用于检索元数据里的纯数字 planid：只有明确的身份写法或独立出现
    （不紧邻中文词、不在计量位置）才当身份；否则保留为待核验事实，避免
    “23132 吨 / 23132（吨）”这类编造数字因等于 planid 被放行。
    """
    pattern = re.compile(r"(?<![0-9０-９A-Za-zＡ-Ｚａ-ｚ_-])" + _fullwidth_digit_class(value)
                         + r"(?![0-9０-９A-Za-zＡ-Ｚａ-ｚ_-])")
    has_digits = any(ch.isdigit() for ch in value)
    strict_digits = strict and value.isdigit()
    spans = _marker_spans(text) if has_digits else []
    out, index = [], 0
    for match in pattern.finditer(text):
        if has_digits and not any(start <= match.start() < end for start, end in spans):
            if strict_digits and _parenthesized_identity(text, match.start(), match.end()):
                pass                    # 明确的括号列举位置：身份
            elif _fact_position(text, match.start(), match.end()):
                continue
            elif strict_digits and _cjk_neighbor(text, match.start(), match.end()):
                continue
        out.append(text[index:match.start()])
        out.append(IDENTITY_MASK)
        index = match.end()
    out.append(text[index:])
    return "".join(out)


def _mask_identities(text: str, *, strict=(), loose=()) -> str:
    """按长度降序排除身份字段：元数据身份从严，用户输入身份从宽。"""
    strict_set, loose_set = set(strict), set(loose)
    masked = text or ""
    for value in sorted(strict_set | loose_set, key=len, reverse=True):
        masked = _mask_value(masked, value, strict=value in strict_set)
    return masked


def _titles(text: str) -> list[str]:
    return [left or right for left, right in TITLE_REF.findall(text or "")]


def _identity_markers(text: str) -> list[str]:
    """显式身份写法（planid= / 编号 / N号政策）中的编号值；兼容全角数字。"""
    found = {}
    for source in (text or "", unicodedata.normalize("NFKC", text or "")):
        for pattern in IDENTITY_MARKERS:
            for match in pattern.finditer(source):
                value = match.group(1)
                found.setdefault(unicodedata.normalize("NFKC", value), value)
    return list(found.values())


def _unknown_identities(text: str, known: set[str]) -> list[str]:
    known_norm = {unicodedata.normalize("NFKC", value) for value in known}
    return [value for value in _identity_markers(text)
            if unicodedata.normalize("NFKC", value) not in known_norm]


def _question_titles(question: str) -> set[str]:
    return {title.strip() for title in _titles(question) if title.strip()}


def _title_known(title: str, known: set[str]) -> bool:
    """篇名允许完全一致或互为包含（模型常缩写篇名），但不能编造全新篇名。"""
    if title in known:
        return True
    return any(title in known_title or known_title in title
               for known_title in known if len(known_title) >= 4)


def _unknown_titles(text: str, known: set[str]) -> list[str]:
    return [title for title in _titles(text)
            if len(title) >= 3 and not _title_known(title, known)]


async def verify_numbers(result, ctx) -> int:
    """核对政策事实数字；文献身份字段走元数据核对，不要求出现在原文引句中。"""
    getter = getattr(ctx.kb, "get_chunk_source", None) if ctx else None
    if not result.claims:
        return 0
    run_refs = _all_refs(ctx, result)
    # 身份核对依据是本轮检索实际返回的元数据；用户输入的 targets/路由编号属于输入身份。
    user_identities = _user_identity_values(ctx)
    metadata_identities = _identity_values(run_refs)
    known_identities = metadata_identities | user_identities
    run_titles = metadata_identities | _question_titles(getattr(ctx, "question", "") or "")
    cache, failed, problems, rejected_claims = {}, set(), 0, []
    for claim in result.claims:
        # 身份字段先按本轮真实来源元数据核对，再排除出数字事实核验。
        unknown_ids = _unknown_identities(claim.text, known_identities)
        unknown_titles = _unknown_titles(claim.text, run_titles)
        if unknown_ids or unknown_titles:
            if unknown_ids:
                result.warnings.append(
                    "结论「" + _clip(claim.text) + "」引用的身份编号 "
                    + "、".join(unknown_ids[:6]) + " 不在本轮检索返回的来源元数据中")
            if unknown_titles:
                result.warnings.append(
                    "结论「" + _clip(claim.text) + "」引用的篇名 "
                    + "、".join("《" + title + "》" for title in unknown_titles[:3])
                    + " 不在本轮检索返回的来源元数据中")
            problems += len(unknown_ids) + len(unknown_titles)
            rejected_claims.append(claim)
            continue
        tokens = _tokens(_mask_identities(claim.text, strict=metadata_identities,
                                          loose=user_identities))
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

    if rejected_claims:
        rejected_ids = {id(claim) for claim in rejected_claims}
        result.claims = [claim for claim in result.claims if id(claim) not in rejected_ids]
        result.warnings.append("数字核验只确认字面存在，不代表其单独足以支持政策结论")
        rebuild_trusted_answer(result, ctx, "number_verification")

    # 答案级：先排除身份字段与列表序号，再判断是否还有未绑定数字。
    # 未绑定数字只说明答案中有无法追溯的表述，不能据此删掉逐条通过原文核验的结论：
    # 改为重建答案（只展示可追溯的结论），保留有效 Claim。
    bound = {token for claim in result.claims for token in _tokens(claim.text)}
    asked = set(_tokens(getattr(ctx, "question", "") or ""))
    masked_answer = _mask_identities(result.answer, strict=metadata_identities,
                                     loose=user_identities)
    orphan = [token for token in _tokens(masked_answer)
              if token not in bound and token not in asked]
    answer_ids = _unknown_identities(result.answer, known_identities)
    answer_titles = _unknown_titles(result.answer, run_titles)
    if orphan or answer_ids or answer_titles:
        result.meta["number_verification"] = "literal_only"
        if answer_ids:
            result.warnings.append("答案引用的身份编号 " + "、".join(answer_ids[:6])
                                   + " 不在本轮检索返回的来源元数据中")
        if answer_titles:
            result.warnings.append("答案引用的篇名 "
                                   + "、".join("《" + title + "》" for title in answer_titles[:3])
                                   + " 不在本轮检索返回的来源元数据中")
        if orphan:
            result.warnings.append("答案中的 " + "、".join(orphan[:6])
                                   + " 未与任何结论绑定，无法核验")
        problems += len(orphan) + len(answer_ids) + len(answer_titles)
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
