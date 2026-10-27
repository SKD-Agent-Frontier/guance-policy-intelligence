"""S4/S5: validate against shown text, then derive every summary from retained claims."""

import json
import re
import unicodedata
from dataclasses import dataclass, field

from guance_agent.business import BusinessOutcome, PolicyRef
from guance_agent.schemas import Claim
from guance_agent.verify import LIST_MARKER, NUMBER, SENTENCE

from .constants import (
    MAX_CLAIMS,
    MAX_LABEL_CHARS,
    MAX_POINT_CHARS,
    MAX_POINTS_PER_DOC,
    MAX_TOPICS,
    MIN_ANCHOR_CHARS,
    MIN_LABEL_CHARS,
    MIN_POINT_CHARS,
    MIN_SUMMARIZED_DOCS,
)
from .reading import PolicyReading

BOUNDARIES = "。；;！？!?\n"
RELEVANCE_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class ValidatedPoint:
    planid: str
    topic: str
    claim: Claim


@dataclass
class Synthesis:
    points: list[ValidatedPoint] = field(default_factory=list)
    relevance: dict[str, str] = field(default_factory=dict)


def parse_output(raw: str) -> dict:
    """Accept one complete object, never an inner object from a truncated wrapper."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    decoder, values, offset = json.JSONDecoder(), [], 0
    while (start := cleaned.find("{", offset)) >= 0:
        if any(c in cleaned[offset:start] for c in "}[]"):
            raise ValueError("JSON wrapper is not an object")
        # Failing closed here also rejects a complete object followed by a broken object.
        value, length = decoder.raw_decode(cleaned[start:])
        values.append(value)
        offset = start + length
    if len(values) != 1 or any(c in cleaned[offset:] for c in "}[]"):
        raise ValueError("expected exactly one complete JSON object")
    value = values[0]
    if not isinstance(value, dict) or not isinstance(value.get("policies"), list):
        raise ValueError("policies must be a list")
    return value


def clean_topic(value) -> str | None:
    if (not isinstance(value, str) or any(c.isdigit() for c in value)
            or "《" in value or "》" in value):
        return None
    label = "".join(c for c in value if not c.isspace()
                    and not unicodedata.category(c).startswith("P"))
    if not MIN_LABEL_CHARS <= len(label) <= MAX_LABEL_CHARS:
        return None
    return label


def _number_present(number: str, sentence: str) -> bool:
    # Match the foundation's numeric boundary rule: 100 cannot borrow 1000.
    return re.search(r"(?<!\d)" + re.escape(number) + r"(?!\d)", sentence) is not None


def _locate_quote(window, text, anchors):
    passage, shown = window.passage, window.shown
    numbers = NUMBER.findall(text)
    for match in SENTENCE.finditer(shown):
        if match.start() > 0 and shown[match.start() - 1] not in BOUNDARIES:
            continue
        # Without a preceding visible delimiter, a non-initial source range may
        # start mid-sentence. Be conservative rather than assert a whole sentence.
        if match.start() == 0 and (passage.char_start or 0) > (passage.section_char_start or 0):
            continue
        sentence = match.group(0)
        # A display cap can split a sentence (even a number). The absence of
        # another digit at shown's edge must not turn that prefix into evidence.
        if (len(shown) < len(passage.content) and match.end() == len(shown)
                and sentence[-1] not in BOUNDARIES):
            continue
        if any(a in sentence for a in anchors) and all(_number_present(n, sentence) for n in numbers):
            return sentence
    return None


def parse_and_validate(raw, reading, ctx, scope) -> Synthesis:
    payload = parse_output(raw)
    windows = {(w.planid, w.chunk_id): w for w in reading.windows if w.shown}
    known = {pid for pid, _ in windows}
    synthesis, queues, used_quotes = Synthesis(), {}, set()
    for policy in payload["policies"]:
        if not isinstance(policy, dict):
            continue
        pid = policy.get("planid")
        if not isinstance(pid, str) or pid not in known or pid in synthesis.relevance:
            continue
        relevance = policy.get("relevance")
        if not isinstance(relevance, str) or relevance not in {*RELEVANCE_ORDER, "irrelevant"}:
            relevance = "low"
        synthesis.relevance[pid] = relevance
        if relevance == "irrelevant" or not isinstance(policy.get("points"), list):
            continue
        queues[pid] = []
        for point in policy["points"]:
            if not isinstance(point, dict):
                continue
            cid, text = point.get("chunk_id"), point.get("text")
            if not isinstance(cid, str) or not isinstance(text, str):
                continue
            window = windows.get((pid, cid))
            ref = ctx.evidence.get(cid)
            if window is None or ref is None or ref.planid != pid:
                continue
            # A later conflicting read can revoke an earlier window's registration.
            start, end = window.passage.char_start, window.passage.char_end
            if start is None or end is None or not any(a <= start < end <= b for a, b in ref.observed_ranges):
                continue
            text = LIST_MARKER.sub("", text.strip()).strip()
            topic = clean_topic(point.get("topic"))
            if (not MIN_POINT_CHARS <= len(text) <= MAX_POINT_CHARS or topic is None
                    or (scope.categories and topic not in [*scope.categories, "其他"])):
                continue
            raw_anchors = point.get("anchors")
            if not isinstance(raw_anchors, list):
                continue
            anchors = list(dict.fromkeys(a for a in raw_anchors
                                         if isinstance(a, str) and len(a) >= MIN_ANCHOR_CHARS))
            quote = _locate_quote(window, text, anchors)
            if quote is None or quote in used_quotes:
                continue
            used_quotes.add(quote)
            queues[pid].append(ValidatedPoint(pid, topic, Claim(
                text=text, chunk_ids=[cid], quotes=[quote], anchors=anchors, dimension=None)))
            if len(queues[pid]) >= MAX_POINTS_PER_DOC:
                break
    ordered = sorted(queues, key=lambda pid: RELEVANCE_ORDER[synthesis.relevance[pid]])
    for index in range(MAX_POINTS_PER_DOC):
        for pid in ordered:
            if index < len(queues[pid]) and len(synthesis.points) < MAX_CLAIMS:
                synthesis.points.append(queues[pid][index])
    topics = list(dict.fromkeys(point.topic for point in synthesis.points))
    if len(topics) > MAX_TOPICS:
        # Reserve one of the eight slots for the overflow label, even if it was late.
        retained = set([t for t in topics if t != "其他"][:MAX_TOPICS - 1])
        for point in synthesis.points:
            if point.topic not in retained:
                point.topic = "其他"
    return synthesis


def insufficient_outcome() -> BusinessOutcome:
    return BusinessOutcome(answer="", claims=[], structured={}, insufficient=True)


def _safe_label(value: str, allowed_numbers: set[str]) -> str:
    """Avoid orphan numeric fragments introduced by deterministic topic cleanup."""
    return NUMBER.sub(lambda m: m.group(0) if m.group(0) in allowed_numbers else "", value)


def assemble(scope, found, reading, synthesis) -> BusinessOutcome:
    points = synthesis.points
    if not points:
        return insufficient_outcome()
    claims = [p.claim for p in points]
    grouped = {}
    for index, point in enumerate(points):
        group = grouped.setdefault(point.topic, dict(topic=point.topic, planids=[], claim_indexes=[]))
        if point.planid not in group["planids"]:
            group["planids"].append(point.planid)
        group["claim_indexes"].append(index)
    policies = []
    candidates = found.candidates + [PolicyRef(planid=pid) for pid in found.missing_targets]
    for policy in candidates:
        pid = policy.planid
        state = reading.policies.get(pid, PolicyReading(read_status="not_retrieved"))
        indexes = [i for i, p in enumerate(points) if p.planid == pid]
        topics = list(dict.fromkeys(points[i].topic for i in indexes))
        relevance = synthesis.relevance.get(pid) if state.chunks_read else None
        status = ("summarized" if indexes else "irrelevant" if relevance == "irrelevant"
                  else state.read_status)
        queries = [q["query"] for q in found.queries if q["status"] == "ok"]
        note = ("检索查询词：" + "、".join(queries) if policy.chunk_ids else "本轮未检索命中")
        if topics:
            note += "；已读片段归入：" + "、".join(topics)
        policies.append(dict(
            **policy.model_dump(mode="json", exclude={"chunk_ids"}),
            read_status=status, relevance=relevance, relevance_note=note,
            topics=topics, summary="；".join(claims[i].text for i in indexes) if indexes else None,
            claim_indexes=indexes, chunk_ids_hit=policy.chunk_ids, chunks_read=state.chunks_read,
            full_text_covered=False, read_attempts=state.read_attempts,
        ))
    summarized = sum(bool(p["claim_indexes"]) for p in policies)
    insufficient = summarized < MIN_SUMMARIZED_DOCS
    note = ("documents_found 为本轮检索候选按 planid 去重后的数量，不代表全库全部相关政策；"
            "摘要只基于已读取片段，不是整份政策的完整摘要。")
    if insufficient:
        note += "有效摘要文献不足，不能据此形成充分的跨文献梳理。"
    if any(q["status"] != "ok" for q in found.queries):
        note += "部分查询未取得结果，具体状态见 scope.effective.queries。"
    if reading.limits:
        note += "读取受到选文、工具或时间上限限制，具体原因见 limits。"
    if any(p["read_status"] in {"no_text", "read_failed", "not_retrieved"} for p in policies):
        note += "部分候选无可用正文、读取失败或指定对象未被检索命中，具体状态见 policies。"
    structured = dict(
        schema_version="organize/1",
        scope=dict(topic=scope.topic, areas=scope.areas, fyps=scope.fyps,
                   plantypes=scope.plantypes, categories=scope.categories,
                   dimensions=scope.dimensions, targets=scope.targets,
                   effective=dict(queries=found.queries, area=scope.effective_area,
                                  fyp=scope.effective_fyp, top_k=scope.top_k),
                   post_filters=scope.post_filters, unapplied=scope.unapplied),
        coverage=dict(documents_found=found.documents_found,
                      documents_after_filter=len(found.candidates),
                      documents_read=sum(s.chunks_read > 0 for s in reading.policies.values()),
                      documents_summarized=summarized, chunks_read=len(reading.windows),
                      limits=reading.limits, note=note),
        policies=policies, groups=list(grouped.values()),
        verification=dict(policy_facts="claims_only",
                          analysis_fields=["relevance", "relevance_note", "topics", "groups"],
                          note="只有 claims 中的内容经过原文引句与数字字面核验；"
                          "相关性与主题归类是分析结果，不是政策事实。"),
    )
    allowed = set(NUMBER.findall(scope.question)) | {n for c in claims for n in NUMBER.findall(c.text)}
    topic = _safe_label(scope.topic, allowed)
    area = _safe_label(scope.effective_area or "未限定地区", allowed)
    fyp = _safe_label(scope.effective_fyp or "未限定规划期", allowed)
    restriction = "；类型后过滤：" + "、".join(scope.plantypes) + "，仅对本轮候选生效" if scope.plantypes else ""
    target_note = "；指定对象梳理" if scope.targets is not None else ""
    lines = [f"研究主题：{topic}；检索范围：{area}，{fyp}{target_note}{restriction}",
             "以下内容基于本轮检索候选中已读取的政策片段整理，不代表全库全部相关政策，"
             "也不是整份政策的完整摘要。"]
    if insufficient:
        lines.append("有效摘要文献不足，本次梳理的证据仍不充分。")
    for group in grouped.values():
        lines.append(f"\n【{group['topic']}】")
        lines.extend("- " + claims[i].text for i in group["claim_indexes"])
    lines.append("\n候选文献清单、阅读覆盖情况和未纳入原因见结构化结果。")
    return BusinessOutcome(answer="\n".join(lines), claims=claims,
                           structured=structured, insufficient=insufficient)
