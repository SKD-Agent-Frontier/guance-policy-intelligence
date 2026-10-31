"""QA 输出格式恢复：失败分类、确定性修复与一次受预算约束的格式修复调用。

本模块只处理格式层：不新增或删除 Claim，不编造 chunk_id、引文或原文内容；
多个 JSON 对象或截断输出不被挑选用作结果。业务含义始终由后续证据核验决定。
"""
import json
import re
import unicodedata

from .schemas import AgentOutput, Claim

OK = "ok"
NOT_JSON = "not_json"
INCOMPLETE_JSON = "incomplete_json"
INVALID_JSON = "invalid_json"
MULTIPLE_OBJECTS = "multiple_objects"
SCHEMA_INVALID = "schema_invalid"

# 只有「语法坏但内容明显存在」的类别允许一次格式修复调用；
# 多个对象、截断、非 JSON 一律失败关闭，避免把模型断片当成完整结论。
REPAIRABLE = frozenset({INVALID_JSON, SCHEMA_INVALID})

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_INVISIBLE = re.compile(r"[\u200b-\u200f\ufeff]")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")

REPAIR_SYSTEM_PROMPT = (
    "你是 JSON 格式修复器。只修复格式与字段类型，不改变、增加或删除任何回答内容、"
    "结论、引用、编号与原文；不得合并多个 JSON 对象，不得编造内容；"
    "无法安全修复时输出 {\"unrecoverable\": true}。只输出一个 JSON 对象，不要解释或 Markdown。"
    "输出结构：{\"answer\": 字符串, \"claims\": [{\"text\": 字符串, "
    "\"chunk_ids\": [字符串], \"quotes\": [字符串]}], \"insufficient\": 布尔值}。"
)


class OutputParseError(ValueError):
    """解析失败，携带固定分类码；不携带模型原文。"""

    def __init__(self, classification: str, detail: str = ""):
        super().__init__(detail or classification)
        self.classification = classification


def clean_output(raw: str) -> str:
    """去掉思考标签与零宽字符；这两类处理不改变 JSON 内容。"""
    text = _THINK.sub("", raw or "")
    return _INVISIBLE.sub("", text)


def _balanced_extent(text: str, start: int) -> int | None:
    """从 start 处的 { 起找到配对的 } 之后位置；字符串内的花括号不计数。"""
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _scan(text: str) -> tuple[list[tuple[int, object]], json.JSONDecodeError | None]:
    decoder, found, offset, first_error = json.JSONDecoder(), [], 0, None
    while (start := text.find("{", offset)) >= 0:
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            first_error = first_error or exc
            offset = start + 1
            continue
        found.append((start, value))
        offset = start + end
    return found, first_error


def _looks_nested(cleaned: str, first_brace: int, object_start: int) -> bool:
    """唯一可解析对象前面是否像「未闭合容器的键值前缀」，而不是正文说明。"""
    segment = cleaned[first_brace:object_start].rstrip()
    return bool(segment) and segment[-1] in ":,[{"


def _classify_clean(cleaned: str) -> tuple[str, AgentOutput | None]:
    found, error = _scan(cleaned)
    first_brace = cleaned.find("{")
    extent = _balanced_extent(cleaned, first_brace) if first_brace >= 0 else None
    # 外层 QA JSON 被截断时，扫描器仍会看到多个完整的内层 Claim。
    # 这些不是多个顶层对象；不能把输出截断误报成 multiple_objects。
    started_as_qa = bool(re.match(
        r'\s*\{\s*"(?:answer|claims|insufficient|analyses)"\s*:', cleaned))
    if extent is None and started_as_qa:
        return INCOMPLETE_JSON, None
    if len(found) > 1:
        return MULTIPLE_OBJECTS, None
    if len(found) == 1:
        start, value = found[0]
        if start != first_brace:
            # 唯一可解析对象出现在更早的、未解析成功的 `{` 之后：无法安全区分
            # 它是容器内层对象还是正文说明，一律失败关闭（不取内层对象）。
            if extent is None:
                return INCOMPLETE_JSON, None
            if start < extent:
                return INVALID_JSON, None
        try:
            return OK, AgentOutput.model_validate(value)
        except (ValueError, TypeError):
            return SCHEMA_INVALID, None
    if first_brace < 0:
        return NOT_JSON, None
    if error is not None:
        # 未闭合字符串，或花括号没有配平，视为输出不完整；其余是语法错乱。
        if "Unterminated string" in error.msg or cleaned.count("{") > cleaned.count("}"):
            return INCOMPLETE_JSON, None
        return INVALID_JSON, None
    return NOT_JSON, None


def _strip_trailing_commas(text: str) -> str:
    """字符串感知地去掉对象/数组末尾逗号；字符串内容保持原样。"""
    out, in_string, escaped = [], False, False
    for index, char in enumerate(text):
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == ",":
            look = index + 1
            while look < len(text) and text[look] in " \t\r\n":
                look += 1
            if look < len(text) and text[look] in "}]":
                continue
        out.append(char)
    return "".join(out)


def _deterministic_candidates(cleaned: str) -> list[tuple[str, str]]:
    """只做不改变语义的修复：解一层双重编码、取唯一代码块内容、去对象尾逗号。"""
    candidates = []
    stripped = cleaned.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        try:
            inner = json.loads(stripped)
        except json.JSONDecodeError:
            inner = None
        if isinstance(inner, str):
            candidates.append(("double_encoded", inner))
    if "```" in cleaned:
        blocks = [block.strip() for block in _FENCE.findall(cleaned) if block.strip()]
        if len(blocks) == 1:
            candidates.append(("code_fence", blocks[0]))
    if _TRAILING_COMMA.search(cleaned):
        candidates.append(("trailing_comma", _strip_trailing_commas(cleaned)))
    return candidates


def classify_output(raw: str) -> tuple[str, AgentOutput | None, str | None]:
    """返回 (分类, 解析结果, 确定性修复方式)；分类为固定码，不含模型原文。"""
    cleaned = clean_output(raw)
    classification, parsed = _classify_clean(cleaned)
    if parsed is not None:
        return classification, parsed, None
    if classification == MULTIPLE_OBJECTS:
        # 已是多个完整对象：不得用任何候选修复替模型挑一个当结果。
        return classification, None, None
    for name, candidate in _deterministic_candidates(cleaned):
        candidate_classification, candidate_parsed = _classify_clean(clean_output(candidate))
        if candidate_parsed is not None:
            return OK, candidate_parsed, name
        if candidate_classification in (MULTIPLE_OBJECTS, INCOMPLETE_JSON):
            # 候选仍然歧义或不完整：原失败形态更保守，按更严重的分类处理
            return candidate_classification, None, None
    return classification, None, None


_ESCAPES = (("\\n", "\n"), ("\\t", "\t"), ("\\r", "\r"), ('\\"', '"'), ("\\\\", "\\"))


def _preservation_key(text: str) -> str:
    value = text or ""
    for source, target in _ESCAPES:
        value = value.replace(source, target)
    return "".join(char for char in unicodedata.normalize("NFKC", value)
                   if not char.isspace())


_INSUFFICIENT_FLAG = re.compile(r'"insufficient"\s*:\s*(true|false)', re.IGNORECASE)
_ANSWER_VALUE = re.compile(r'"answer"\s*:\s*"((?:\\.|[^"\\])*)"')
_TEXT_VALUE = re.compile(r'"text"\s*:\s*"((?:\\.|[^"\\])*)"')
_ARRAY_IDS = re.compile(r'"chunk_ids"\s*:\s*\[(.*?)\]', re.DOTALL)
_ARRAY_QUOTES = re.compile(r'"quotes"\s*:\s*\[(.*?)\]', re.DOTALL)
_INNER_STRING = re.compile(r'"((?:\\.|[^"\\])*)"')


def _raw_insufficient_flag(raw: str) -> bool | None:
    match = _INSUFFICIENT_FLAG.search(raw or "")
    if match is None:
        return None
    return match.group(1).lower() == "true"


def _unescape(value: str) -> str | None:
    try:
        return json.loads('"' + value + '"')
    except json.JSONDecodeError:
        return None


def _strict_json_object(raw: str):
    try:
        value = json.loads(raw or "")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _tolerant_structure(raw: str):
    """从语法损坏的原输出里抽取 answer/claims（逐条 text/chunk_ids/quotes）。

    抽不出成对结构时返回 None：此时不能安全修复，宁可失败关闭。
    """
    answer = None
    answer_match = _ANSWER_VALUE.search(raw or "")
    if answer_match:
        answer = _unescape(answer_match.group(1))
        if answer is None:
            return None
    texts = []
    for match in _TEXT_VALUE.finditer(raw or ""):
        text = _unescape(match.group(1))
        if text is None:
            return None
        texts.append((match.start(), text))
    claims = []
    for index, (start, text) in enumerate(texts):
        end = texts[index + 1][0] if index + 1 < len(texts) else len(raw)
        segment = raw[start:end]
        ids_match = _ARRAY_IDS.search(segment)
        ids = []
        if ids_match:
            ids = [_unescape(item) for item in _INNER_STRING.findall(ids_match.group(1))]
            if None in ids:
                return None
        quotes_match = _ARRAY_QUOTES.search(segment)
        quotes = []
        if quotes_match:
            quotes = [_unescape(item) for item in _INNER_STRING.findall(quotes_match.group(1))]
            if None in quotes:
                return None
        claims.append((text, tuple(ids), tuple(quotes)))
    if answer is None and not claims:
        return None
    return {"answer": answer, "claims": claims,
            "insufficient": _raw_insufficient_flag(raw)}


def _matches_strict(original: dict, parsed: AgentOutput) -> bool:
    """原输出是合法 JSON 时，按字段做结构化比对（允许最小类型纠正）。"""
    answer = original.get("answer")
    if isinstance(answer, str) and answer != parsed.answer:
        return False
    if answer is not None and not isinstance(answer, str) and str(answer) != parsed.answer:
        return False
    claims = original.get("claims")
    if claims is not None:
        if not isinstance(claims, list) or len(claims) != len(parsed.claims):
            return False
        for raw_claim, claim in zip(claims, parsed.claims):
            if not isinstance(raw_claim, dict):
                return False
            text = raw_claim.get("text")
            if isinstance(text, str) and text != claim.text:
                return False
            ids = raw_claim.get("chunk_ids")
            if ids is not None and [str(item) for item in ids] != claim.chunk_ids:
                return False
            quotes = raw_claim.get("quotes")
            if quotes is not None and [str(item) for item in quotes] != claim.quotes:
                return False
    flag = original.get("insufficient")
    if isinstance(flag, bool) and flag != parsed.insufficient:
        return False
    return True


def repair_preserves_content(raw: str, parsed: AgentOutput) -> bool:
    """格式修复必须只改格式：逐条比对回答、结论、引用与证据映射。

    这是格式修复的最后一道闸门：内容被改写、结论被增删、引用或 chunk_ids 被
    重新映射、证据不足标记被改动，或原始结构无法还原时，整次修复作废。
    """
    strict = _strict_json_object(raw)
    if strict is not None:
        return _matches_strict(strict, parsed)
    structure = _tolerant_structure(raw)
    if structure is None:
        return False                    # 无法还原原结构，不做模型修复
    if structure["answer"] is not None and structure["answer"] != parsed.answer:
        return False
    expected = sorted(structure["claims"])
    actual = sorted((claim.text, tuple(claim.chunk_ids), tuple(claim.quotes))
                    for claim in parsed.claims)
    if expected != actual:
        return False
    flag = structure["insufficient"]
    return flag is None or bool(parsed.insufficient) == flag


def recover_complete_claims(raw: str, *, max_claims: int = 5) -> AgentOutput | None:
    """只从截断的 QA JSON 的 claims 数组复制完整对象，绝不补写残缺引文。

    必须由调用方确认输出额度耗尽；恢复出的事实仍经过原有证据登记、
    原文逐字及数字核验。丢弃不完整的最后一项与全部分析草稿。
    """
    cleaned = clean_output(raw)
    if not re.match(r'\s*\{\s*"(?:answer|claims|insufficient|analyses)"\s*:', cleaned):
        return None
    if _balanced_extent(cleaned, cleaned.find("{")) is not None:
        return None
    matches = list(re.finditer(r'"claims"\s*:\s*\[', cleaned))
    if len(matches) != 1:
        return None
    decoder = json.JSONDecoder()
    cursor = matches[0].end()
    recovered: list[Claim] = []
    while len(recovered) < max_claims:
        while cursor < len(cleaned) and cleaned[cursor] in " \t\r\n,":
            cursor += 1
        if cursor >= len(cleaned) or cleaned[cursor] in "]}" or cleaned[cursor] != "{":
            break
        try:
            value, end = decoder.raw_decode(cleaned, cursor)
            claim = Claim.model_validate(value)
        except (json.JSONDecodeError, ValueError, TypeError):
            break
        if (not claim.text.strip() or not claim.chunk_ids or not claim.quotes
                or not all(isinstance(q, str) and q.strip() for q in claim.quotes)):
            break
        # 完整对象后必须紧接逗号或 claims 数组结束；拒绝不明拼接。
        tail = end
        while tail < len(cleaned) and cleaned[tail].isspace():
            tail += 1
        if tail < len(cleaned) and cleaned[tail] not in ",]":
            break
        recovered.append(claim)
        cursor = end
    if not recovered:
        return None
    return AgentOutput(
        answer="本轮模型输出被截断，仅保留已完整生成的政策事实；其引文仍须通过逐字核验。",
        claims=recovered, insufficient=True, analyses=[],
    )


def parse_agent_output(raw: str) -> AgentOutput:
    """严格解析；失败时抛出带分类码的 OutputParseError。"""
    classification, parsed, _ = classify_output(raw)
    if parsed is None:
        raise OutputParseError(classification)
    return parsed


def repair_messages(raw: str, limit: int) -> list[dict]:
    """格式修复请求：只带模型原文，不重新检索，也不携带工具。"""
    return [{"role": "system", "content": REPAIR_SYSTEM_PROMPT},
            {"role": "user", "content": (raw or "")[:limit]}]
