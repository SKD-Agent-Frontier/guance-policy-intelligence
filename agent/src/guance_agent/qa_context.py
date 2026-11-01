"""Trusted, deterministic multi-turn context for controlled QA.

Only source identities that were actually read and verified in a previous turn are
persisted.  No model-generated summary and no unverified claim text is reused as
current evidence; the next turn must read and verify its own quotes.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

from .routing import current_question

MAX_TURNS = 8
MAX_QUESTION_CHARS = 1000

FOLLOWUP_MARKERS = (
    "其中", "该", "这个", "上述", "它", "其", "那", "这份", "这一",
    "第", "部分", "具体", "怎么说的", "怎么说", "类似措施", "前面", "刚才",
    "上一轮", "此政策", "该政策", "这份政策",
)

REGION_TERMS = (
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古",
    "广西", "西藏", "宁夏", "新疆", "香港", "澳门", "台湾",
)

PERIOD_ALIASES = (
    ("十五五", "第十五个五年规划"),
    ("十四五", "第十四个五年规划"),
    ("十三五", "第十三个五年规划"),
    ("十二五", "第十二个五年规划"),
    ("十一五", "第十一个五年规划"),
    ("第十五个五年规划", "第十五个五年规划"),
    ("第十四个五年规划", "第十四个五年规划"),
    ("第十三个五年规划", "第十三个五年规划"),
    ("第十二个五年规划", "第十二个五年规划"),
    ("第十一个五年规划", "第十一个五年规划"),
    ("十五计划", "第十个五年计划"),
    ("十五", "第十个五年计划"),
    ("第十个五年计划", "第十个五年计划"),
)

PERIOD_RE = re.compile("|".join(re.escape(marker) for marker, _ in PERIOD_ALIASES))


def _context_path(session_path, session_id: str) -> Path:
    base = Path(session_path)
    digest = sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return base.parent / f"{base.name}.{digest}.qa-context.json"


class TrustedContextStore:
    """Per-session JSON sidecar; only called after the session has been claimed."""

    def __init__(self, session_path, session_id: str):
        self.path = _context_path(session_path, session_id)

    def load(self) -> list[dict]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        turns = payload.get("turns") if isinstance(payload, dict) else None
        if not isinstance(turns, list):
            return []
        return [turn for turn in turns[-MAX_TURNS:] if isinstance(turn, dict)]

    def append(self, turn: dict) -> None:
        if not isinstance(turn, dict):
            return
        turns = self.load()
        turns.append(turn)
        turns = turns[-MAX_TURNS:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"version": 1, "turns": turns},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)


def build_trusted_turn(question: str, ctx, result) -> dict:
    """Persist final quote-verified source identities, never merely-read hits.

    Empty turns deliberately act as a boundary: after an unanswered scope change,
    a later pronoun cannot resurrect an older document from the same session.
    """
    question = current_question(question)
    documents: dict[str, dict] = {}
    chunks: list[str] = []
    registry = getattr(ctx, "evidence", None)
    final_refs = {ref.chunk_id: ref for ref in getattr(result, "evidence_refs", ())}
    quote_check = getattr(result, "meta", {}).get("quote_verification") or {}
    trusted = (getattr(result, "status", None) == "completed"
               and not getattr(result, "error", None)
               and isinstance(quote_check, dict)
               and quote_check.get("approved", 0) > 0
               and quote_check.get("unverified") == 0
               and not result.meta.get("verification_incomplete"))
    if trusted and registry is not None:
        for claim in result.claims:
            if (not claim.quotes or not claim.chunk_ids
                    or not set(claim.chunk_ids) <= set(final_refs)):
                continue
            for chunk_id in claim.chunk_ids:
                ref = registry.get(chunk_id)
                final_ref = final_refs.get(chunk_id)
                if (ref is None or final_ref is None
                        or getattr(ref, "source", None) != "kb_read"
                        or ref.planid != final_ref.planid):
                    continue
                chunks.append(chunk_id)
                documents.setdefault(ref.planid, {
                    "planid": ref.planid,
                    "title": getattr(ref, "title", "") or "",
                    "area": getattr(ref, "area", None),
                    "fyp": getattr(ref, "fyp", None),
                })
    resolution = getattr(ctx, "qa_context_resolution", None) or {}
    return {
        "version": 1,
        "question": (question or "")[:MAX_QUESTION_CHARS],
        "documents": list(documents.values()),
        "verified_chunk_ids": list(dict.fromkeys(chunks))[:24],
        "verified_claim_count": len(getattr(result, "claims", ()) or ()) if trusted else 0,
        "evidence_status": getattr(result, "evidence_status", "none"),
        "resolution_status": resolution.get("status"),
        "requested_constraints": resolution.get("requested_constraints"),
    }


def _mentioned_regions(question: str, route) -> list[str]:
    question = current_question(question)
    candidates = [term for term in REGION_TERMS if term in question]
    for area in getattr(route, "areas", ()) or ():
        value = str(area or "")
        if value and value in question:
            candidates.append(value)
    # Prefer the most specific form: "河北省" over its substring "河北".
    result = []
    for candidate in sorted(set(candidates), key=len, reverse=True):
        if not any(candidate in existing for existing in result):
            result.append(candidate)
    return result


def _mentioned_periods(question: str) -> list[str]:
    found = []
    remaining = current_question(question)
    for marker, normalized in sorted(PERIOD_ALIASES, key=lambda item: -len(item[0])):
        if marker in remaining:
            found.append(normalized)
            remaining = remaining.replace(marker, "")
    return list(dict.fromkeys(found))


def _normalize_prior_fyp(fyp: str | None) -> str | None:
    if not fyp:
        return None
    for marker, normalized in PERIOD_ALIASES:
        if marker == fyp or normalized == fyp:
            return normalized
    return fyp


def normalize_period(fyp: str | None) -> str | None:
    """Public alias used by controlled retrieval for explicit period filtering."""
    return _normalize_prior_fyp(fyp)


def _question_names_document(question: str, documents: list[dict]) -> list[dict]:
    matched = []
    for document in documents:
        title = str(document.get("title") or "")
        area = str(document.get("area") or "")
        fyp = str(document.get("fyp") or "")
        if (title and title in question) or (area and area in question) or (fyp and fyp in question):
            matched.append(document)
    return matched


def _clarification(documents: list[dict]) -> str:
    labels = []
    for document in documents:
        label = str(document.get("title") or document.get("planid") or "未命名文献")
        details = [str(item) for item in (document.get("area"), document.get("fyp")) if item]
        if details:
            label += "（" + "·".join(details) + "）"
        labels.append(label)
    return ("无法唯一确定您追问的是上一轮的哪一份政策文献："
            + "；".join(labels)
            + "。请明确具体文献或地区后再检索；本轮不会猜测，也不会混合不同文献的证据。")


def resolve_followup(question: str, turns: list[dict], route=None) -> dict:
    """Classify the current turn and resolve the trusted retrieval scope.

    The resolver is deliberately deterministic: no model call and no free-form
    history summary.  It only uses source identities persisted from prior reads.
    """
    # The latest turn is authoritative even if it has no accepted sources.
    # Never skip an unsuccessful scope change to resurrect an older document.
    question = current_question(question)
    prior = next((turn for turn in reversed(turns or [])
                  if isinstance(turn, dict)), None)
    base = {
        "status": "no_prior",
        "previous_question": None,
        "documents": [],
        "inherited_planids": [],
        "retrieval_query": None,
        "context_note": None,
        "reason": "没有可继承的上一轮已核验来源",
        "clarification": None,
        "requested_constraints": None,
    }
    if prior is None:
        return base
    documents = [doc for doc in prior.get("documents", [])
                 if isinstance(doc, dict) and doc.get("planid")]
    if not documents:
        previous_question = str(prior.get("question") or "")
        if any(marker in question for marker in FOLLOWUP_MARKERS):
            return {
                **base,
                "status": "ambiguous",
                "previous_question": previous_question,
                "reason": "上一轮未形成可以继承的已核验研究对象",
                "clarification": (
                    "上一轮未确认可引用的目标政策文献，无法确定“其中”所指对象。"
                    "请明确文献、地区或 planid 后再检索；不会退回更早轮次的旧文献。"
                ),
            }
        return {**base, "status": "new_topic", "previous_question": previous_question}
    previous_question = str(prior.get("question") or "")
    prior_areas = {str(doc.get("area") or "") for doc in documents}
    prior_periods = {_normalize_prior_fyp(doc.get("fyp")) for doc in documents
                     if _normalize_prior_fyp(doc.get("fyp"))}
    explicit_areas = _mentioned_regions(question, route)
    explicit_periods = _mentioned_periods(question)
    # A synthesized subject prefix may repeat the previous area/period. Only
    # genuinely new constraints may trigger a scope change; otherwise the old
    # research object leaks back into the reported new scope.
    new_areas = [area for area in explicit_areas
                 if area not in prior_areas
                 and not any(area in prior_area or prior_area in area
                             for prior_area in prior_areas if prior_area)]
    new_periods = [period for period in explicit_periods
                   if period not in prior_periods]
    area_changed = bool(new_areas)
    period_changed = bool(new_periods)
    # If the area changed, the current turn may legitimately restate the same
    # period; it is part of the new scope and must not be reported as stale.
    requested_periods = explicit_periods if area_changed else new_periods

    if area_changed or period_changed:
        details = []
        if area_changed:
            details.append("地区 " + "/".join(new_areas))
        if period_changed:
            details.append("时期 " + "/".join(new_periods))
        return {
            **base,
            "status": "scope_changed",
            "previous_question": previous_question,
            "documents": documents,
            "reason": "用户显式更换研究范围：" + "；".join(details),
            "requested_constraints": {
                "areas": new_areas,
                "periods": requested_periods,
            },
            "context_note": ("用户本轮显式更换了研究范围（" + "；".join(details)
                             + "），不得沿用上一轮研究对象；请按新范围重新检索。"
                             if details else None),
        }

    if not any(marker in question for marker in FOLLOWUP_MARKERS):
        return {
            **base,
            "status": "new_topic",
            "previous_question": previous_question,
            "documents": documents,
            "reason": "当前问题没有指向上一轮的指代标记，按新问题处理",
        }

    if len(documents) > 1:
        named = _question_names_document(question, documents)
        if len(named) != 1:
            return {
                **base,
                "status": "ambiguous",
                "previous_question": previous_question,
                "documents": documents,
                "reason": "上一轮涉及多份文献，当前追问没有唯一指向",
                "clarification": _clarification(documents),
            }
        inherited = [str(named[0]["planid"])]
        document = named[0]
    else:
        inherited = [str(documents[0]["planid"])]
        document = documents[0]

    context_note = (
        "多轮上下文（仅用于理解指代；以下对象是上一轮已核验读取的来源身份，"
        "不是本轮证据，必须基于本轮重新回读的原文回答）：\n"
        "上一轮问题：" + previous_question[:500] + "\n"
        "已确认研究对象：" + "；".join(
            f"{doc.get('title') or doc.get('planid')}（{doc.get('area') or '地区未标注'}"
            f"，{doc.get('fyp') or '时期未标注'}，planid={doc.get('planid')}）"
            for doc in documents)
    )
    return {
        **base,
        "status": "continued",
        "previous_question": previous_question,
        "documents": documents,
        "inherited_planids": inherited,
        "retrieval_query": (previous_question[:500] + " " + question)[:700],
        "context_note": context_note,
        "reason": "延续上一轮已确认研究对象：" + str(document.get("planid")),
    }


def public_resolution(resolution: dict) -> dict:
    """Small, serializable trace; never include previous answers or claim text."""
    keys = ("status", "previous_question", "documents", "inherited_planids",
            "reason", "clarification", "requested_constraints")
    public = {key: resolution.get(key) for key in keys}
    public["documents"] = [
        {"planid": doc.get("planid"), "title": doc.get("title"),
         "area": doc.get("area"), "fyp": doc.get("fyp")}
        for doc in resolution.get("documents", [])
    ]
    return public
