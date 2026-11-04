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

from .context import same_source
from .routing import current_question, parse_areas, parse_planids, parse_titles

MAX_TURNS = 8
MAX_QUESTION_CHARS = 1000

FOLLOWUP_MARKERS = (
    "其中", "该", "这个", "上述", "它", "其", "那", "这份", "这一",
    "第", "部分", "具体", "怎么说的", "怎么说", "类似措施", "前面", "刚才",
    "上一轮", "此政策", "该政策", "这份政策", "那", "这些", "以上", "前三轮", "继续",
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

PERIOD_ALIASES += tuple((f"{n}五", f"第{n}个五年计划") for n in "一二三四五六七八九")
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


def _as_payload(value) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {name: getattr(value, name) for name in (
        "text", "chunk_ids", "quotes", "dimension",
        "chunk_id", "planid", "title", "area", "fyp", "source",
    ) if hasattr(value, name)}


def build_trusted_turn(question: str, ctx, result) -> dict:
    """Persist final quote-verified source identities, never merely-read hits.

    Empty turns deliberately act as a boundary: after an unanswered scope change,
    a later pronoun cannot resurrect an older document from the same session.
    """
    question = current_question(question)
    documents: dict[str, dict] = {}
    chunks: list[str] = []
    verified_facts: list[dict] = []
    verified_refs: dict[str, dict] = {}
    registry = getattr(ctx, "evidence", None)
    final_refs = {ref.chunk_id: ref for ref in getattr(result, "evidence_refs", ())}
    quote_check = getattr(result, "meta", {}).get("quote_verification") or {}
    trusted = (getattr(result, "status", None) == "completed"
               and not getattr(result, "error", None)
               and isinstance(quote_check, dict)
               and quote_check.get("approved", 0) > 0
               and not result.meta.get("verification_incomplete"))
    if trusted and registry is not None:
        for claim in result.claims:
            if (not claim.quotes or not claim.chunk_ids
                    or not set(claim.chunk_ids) <= set(final_refs)):
                continue
            claim_refs = []
            for chunk_id in claim.chunk_ids:
                ref = registry.get(chunk_id)
                final_ref = final_refs.get(chunk_id)
                if (ref is None or final_ref is None
                        or getattr(ref, "source", None) != "kb_read"
                        or final_ref.source != "kb_read"
                        or not same_source(ref, final_ref)):
                    break
                claim_refs.append((chunk_id, ref, final_ref))
            if len(claim_refs) != len(claim.chunk_ids):
                continue
            for chunk_id, ref, final_ref in claim_refs:
                chunks.append(chunk_id)
                verified_refs.setdefault(chunk_id, _as_payload(final_ref))
                documents.setdefault(ref.planid, {
                    "planid": ref.planid,
                    "title": getattr(ref, "title", "") or "",
                    "area": getattr(ref, "area", None),
                    "fyp": getattr(ref, "fyp", None),
                })
            verified_facts.append(_as_payload(claim))
    resolution = getattr(ctx, "qa_context_resolution", None) or {}
    route = getattr(ctx, "route", None)
    # Keep user-authored bounds even when discovery/retrieval failed. These
    # constraints are separate from source identities and never authorize a
    # planid by themselves.
    areas = list(dict.fromkeys(str(item) for item in
                               (getattr(route, "canonical_areas", ()) or
                                getattr(route, "areas", ()) or ()) if item))
    periods = list(dict.fromkeys(str(item) for item in
                                 (getattr(route, "fyps", ()) or ()) if item))
    titles = list(dict.fromkeys(str(item) for item in
                                (getattr(route, "titles", ()) or ()) if item))
    planids = list(dict.fromkeys(str(item) for item in
                                 (getattr(route, "planids", ()) or ()) if item))
    constraints = resolution.get("requested_constraints") or {}
    if resolution.get("status") in {"continued", "extended", "fresh_research", "scope_changed"}:
        areas = areas or [str(item) for item in constraints.get("areas", []) if item]
        periods = periods or [str(item) for item in constraints.get("periods", []) if item]
        titles = titles or [str(item) for item in constraints.get("titles", []) if item]
        planids = planids or [str(item) for item in constraints.get("planids", []) if item]
    if (resolution.get("status") == "continued" and not (areas or periods or titles or planids)):
        prior = next((turn for turn in reversed(getattr(ctx, "trusted_turns", []) or [])
                      if isinstance(turn, dict)), {})
        prior_scope = prior.get("user_scope") or {}
        areas = list(prior_scope.get("areas") or [])
        periods = list(prior_scope.get("periods") or [])
        titles = list(prior_scope.get("titles") or [])
        planids = list(prior_scope.get("planids") or [])
    prior_scope = {}
    for turn in reversed(getattr(ctx, "trusted_turns", []) or []):
        if isinstance(turn, dict):
            prior_scope = turn.get("user_scope") or {}
            break
    if resolution.get("status") in {"continued", "fresh_research", "extended"}:
        research_query = str(prior_scope.get("research_query") or question)
        if question and question not in research_query:
            research_query = (research_query + " " + question).strip()
    else:
        research_query = question
    pending_requirements = result.meta.get("research_requirements") or []
    if not pending_requirements and resolution.get("status") in {
        "continued", "fresh_research", "extended"
    }:
        pending_requirements = prior_scope.get("requirements") or []
    user_scope = {
        "areas": list(dict.fromkeys(areas))[:8],
        "periods": list(dict.fromkeys(periods))[:4],
        "titles": list(dict.fromkeys(titles))[:8],
        "planids": list(dict.fromkeys(planids))[:8],
        "research_query": research_query[:MAX_QUESTION_CHARS],
        "requirements": pending_requirements,
    }
    structured = getattr(result, "structured", None) or {}
    delivered_blocks = []
    if (isinstance(structured, dict)
            and structured.get("schema_version") == "qa-report/1"
            and structured.get("verification", {}).get("facts")
            == "semantic_reviewed_against_quotes"):
        delivered_blocks = [
            {"title_path": list(block.get("title_path") or []),
             "dimension": block.get("dimension"),
             "kind": block.get("kind"), "text": block.get("text"),
             "supporting_claim_indexes": list(block.get("supporting_claim_indexes") or [])}
            for block in structured.get("blocks") or []
            if isinstance(block, dict)
        ][:12]
    return {
        "version": 1,
        "question": (question or "")[:MAX_QUESTION_CHARS],
        "documents": list(documents.values()),
        "verified_chunk_ids": list(dict.fromkeys(chunks)),
        "verified_claim_count": len(verified_facts),
        "evidence_status": getattr(result, "evidence_status", "none"),
        "resolution_status": resolution.get("status"),
        "requested_constraints": resolution.get("requested_constraints"),
        "user_scope": user_scope,
        # Final, quote-verified facts are safe to reuse for a later
        # summarize/analyze turn.  They are never mixed with an unverified
        # historical answer or a merely-read hit.
        "verified_facts": verified_facts,
        "fact_trust": "quote_verified",
        "semantic_trust": (
            "reviewed" if (result.meta.get("research_core") or {}).get("status") == "complete"
            and ((result.meta.get("research_core") or {}).get("audit") or {}).get("passed")
            else "unreviewed"
        ),
        "evidence_refs": list(verified_refs.values()),
        "quote_verification": ({
            key: quote_check.get(key) for key in
            ("checked", "approved", "rejected", "unverified")
        } if trusted else {}),
        "delivered_blocks": delivered_blocks,
        # Only identities from a previously confirmed continuation, not claims.
        # A new topic or explicit scope change must clear this carry-forward.
        "context_documents": (
            list(resolution.get("documents") or [])[:4]
            if resolution.get("status") in {"continued", "extended"} else []
        ),
        "intent": getattr(ctx, "research_intent", None),
    }


def _mentioned_regions(question: str, route) -> list[str]:
    question = current_question(question)
    canonical = [str(item) for item in (getattr(route, "canonical_areas", ()) or ()) if item]
    if canonical:
        candidates = canonical
    else:
        candidates = [term for term in REGION_TERMS if term in question]
        area_text = re.sub(
            r"能否|可否|请|改为|改成|只看|仅看|结合|加上|新增|加入|纳入|与|和|以及|"
            r"比较|对比|相比|相较|异同|差异|共同",
            " ", question,
        )
        candidates.extend(parse_areas(area_text))
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


def _mentioned_source_periods(question: str, route=None) -> list[str]:
    """Return periods explicitly naming the source document, not its contents."""
    found = []
    value = current_question(question)
    normalized_by_marker = dict(PERIOD_ALIASES)
    for match in PERIOD_RE.finditer(value):
        marker = match.group(0)
        after = value[match.end():match.end() + 8]
        if any(word in after for word in ("规划", "计划", "纲要", "方案")):
            found.append(normalized_by_marker[marker])
    for period in getattr(route, "fyps", ()) or ():
        normalized = _normalize_prior_fyp(str(period))
        if not normalized:
            continue
        number = normalized.removeprefix("第").split("个五年", 1)[0]
        markers = {normalized, f"第{number}计划", f"{number}计划",
                   f"第{number}规划", f"{number}规划", f"{number}五"}
        for marker in markers:
            index = value.find(marker)
            if index < 0:
                continue
            has_source_cue = marker.endswith(("规划", "计划", "纲要", "方案"))
            after = value[index + len(marker):index + len(marker) + 8]
            if has_source_cue or any(word in after for word in ("规划", "计划", "纲要", "方案")):
                found.append(normalized)
                break
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


def _normalized_title(value: str) -> str:
    """Treat typographic quotes/spaces as punctuation, not document identity."""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(value or "")).lower()


def _explicit_document_matches(question: str, documents: list[dict]) -> list[dict]:
    normalized_question = _normalized_title(question)
    return [
        doc for doc in documents
        if len(_normalized_title(doc.get("title") or "")) >= 8
        and _normalized_title(doc["title"]) in normalized_question
    ]


def _question_names_document(question: str, documents: list[dict]) -> list[dict]:
    # A full verified title is a stronger locator than a shared planning period.
    by_title = _explicit_document_matches(question, documents)
    if by_title:
        return by_title
    by_area = [doc for doc in documents
               if doc.get("area") and str(doc["area"]) in question]
    if by_area:
        return by_area
    return [doc for doc in documents
            if doc.get("fyp") and str(doc["fyp"]) in question]


def _document_matches_user_scope(document: dict, areas: list[str], periods: list[str]) -> bool:
    area = str(document.get("area") or "")
    title = str(document.get("title") or "")
    area_match = not areas or area in areas
    document_period = _normalize_prior_fyp(document.get("fyp"))
    title_period_match = any(
        alias in title for alias, normalized in PERIOD_ALIASES if normalized in periods
    )
    period_match = not periods or document_period in periods or title_period_match
    return area_match and period_match


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
    if not documents and prior.get("resolution_status") == "continued":
        # Failed evidence acquisition must not erase confirmed research scope.
        # This carries an identity only; no unverified answer is reused.
        documents = [doc for doc in prior.get("context_documents", [])
                     if isinstance(doc, dict) and doc.get("planid")]
    saved_scope = prior.get("user_scope") or {}
    saved_areas = [str(item) for item in saved_scope.get("areas", []) if item]
    saved_periods = [_normalize_prior_fyp(str(item)) for item in
                     saved_scope.get("periods", []) if item]
    saved_periods = [item for item in saved_periods if item]
    if saved_areas or saved_periods:
        documents = [doc for doc in documents
                     if _document_matches_user_scope(doc, saved_areas, saved_periods)]
    if not documents:
        previous_question = str(prior.get("question") or "")
        if any(marker in question for marker in FOLLOWUP_MARKERS):
            user_scope = prior.get("user_scope") or {}
            areas = saved_areas
            periods = saved_periods
            titles = [str(item) for item in user_scope.get("titles", []) if item]
            explicit_areas = _mentioned_regions(question, route)
            explicit_periods = _mentioned_source_periods(question, route)
            explicit_titles = list(parse_titles(question))
            explicit_planids = list(parse_planids(question))
            replace_scope = bool(re.search(
                r"改为|改成|只看|仅看|不要旧对象|只研究新对象", question))
            if replace_scope:
                if explicit_areas or explicit_periods or explicit_titles or explicit_planids:
                    return {
                        **base,
                        "status": "scope_changed",
                        "previous_question": previous_question,
                        "requested_constraints": {
                            "areas": explicit_areas,
                            "periods": explicit_periods,
                            "titles": explicit_titles,
                            "planids": explicit_planids,
                        },
                        "reason": "用户明确替换尚未确认来源的研究范围",
                        "context_note": "本轮按用户明确的新范围重新研究，不沿用旧范围或候选文献。",
                    }
                return {
                    **base,
                    "status": "ambiguous",
                    "previous_question": previous_question,
                    "reason": "用户要求替换范围，但新范围无法唯一解析",
                    "clarification": "您要求更换研究对象，但尚未明确新的地区、时期、篇名或planid。"
                                    "请补充新范围；不会继续使用旧研究范围。",
                }
            if areas or periods or titles:
                expansion = bool(re.search(
                    r"结合|同时|另外|以及|并且|再看|与|和|比较|对比|相比|相较|差异|异同|共同",
                    question,
                ))
                if expansion:
                    areas = list(dict.fromkeys(areas + explicit_areas))
                    periods = list(dict.fromkeys(periods + explicit_periods))
                    titles = list(dict.fromkeys(titles + explicit_titles))
                elif explicit_areas or explicit_periods:
                    areas = explicit_areas or areas
                    periods = explicit_periods or periods
                    titles = explicit_titles or titles
                constraints = {
                    "areas": areas,
                    "periods": periods,
                    "titles": titles,
                }
                return {
                    **base,
                    "status": "fresh_research",
                    "previous_question": previous_question,
                    "requested_constraints": constraints,
                    "retrieval_query": (str(user_scope.get("research_query") or previous_question)[:400]
                                        + " " + question)[:700],
                    "reason": "上一轮保留了用户明示范围，本轮重新检索且不继承未核验文献",
                    "context_note": "上一轮尚未确认可引用的政策文献；本轮按原始用户范围重新检索，"
                                   "不继承候选文献编号或未核验结论。",
                }
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
    explicit_periods = _mentioned_source_periods(question, route)
    replace_scope = bool(re.search(r"改为|改成|只看|仅看|不要旧对象|只研究新对象", question))
    expansion = bool(re.search(
        r"结合|同时|另外|以及|并且|再看|与|和|比较|对比|相比|相较|差异|异同|共同", question
    ))
    named_titles = _explicit_document_matches(question, documents)
    if named_titles:
        documents = named_titles
        prior_areas = {str(doc.get("area") or "") for doc in documents}
        prior_periods = {_normalize_prior_fyp(doc.get("fyp")) for doc in documents
                         if _normalize_prior_fyp(doc.get("fyp"))}
    # A verified title is an explicit document reference even when the catalog
    # area stores only a district (e.g. '顺庆区') and the user says '南充市'.
    # Do not misclassify the parent city present in that same trusted title
    # as a change of research scope.
    title_areas = [str(doc.get("title") or "") for doc in named_titles]
    # A synthesized subject prefix may repeat the previous area/period. Only
    # genuinely new constraints may trigger a scope change; otherwise the old
    # research object leaks back into the reported new scope.
    new_areas = [area for area in explicit_areas
                 if area not in prior_areas
                 and not any(area in prior_area or prior_area in area
                             for prior_area in prior_areas if prior_area)
                 and not any(area in title for title in title_areas)]
    new_periods = [period for period in explicit_periods
                   if period not in prior_periods]
    area_changed = bool(new_areas)
    period_changed = bool(new_periods)
    # If the area changed, the current turn may legitimately restate the same
    # period; it is part of the new scope and must not be reported as stale.
    requested_periods = explicit_periods if area_changed else new_periods

    if (area_changed or period_changed) and (replace_scope or not expansion):
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

    if area_changed or period_changed:
        details = []
        if area_changed:
            details.append("新增地区 " + "/".join(new_areas))
        if period_changed:
            details.append("新增时期 " + "/".join(new_periods))
        context_note = (
            "多轮研究范围扩展：保留上一轮已核验来源身份用于对照和回读，"
            "并按本轮新增范围检索新来源；旧来源不能替代新范围证据。\n"
            "上一轮问题：" + previous_question[:500] + "\n"
            "已确认研究对象：" + "；".join(
                f"{doc.get('title') or doc.get('planid')}（{doc.get('area') or '地区未标注'}"
                f"，{doc.get('fyp') or '时期未标注'}，planid={doc.get('planid')}）"
                for doc in documents)
        )
        return {
            **base,
            "status": "extended",
            "previous_question": previous_question,
            "documents": documents,
            "inherited_planids": [str(doc["planid"]) for doc in documents],
            "retrieval_query": (previous_question[:400] + " " + question)[:700],
            "context_note": context_note,
            "reason": "保留既有研究对象并扩展范围：" + "；".join(details),
            "requested_constraints": {
                "areas": list(dict.fromkeys([area for area in prior_areas if area] + new_areas)),
                "periods": list(dict.fromkeys(
                    [period for period in prior_periods if period] + requested_periods)),
            },
        }

    if (not named_titles and not expansion
            and not any(marker in question for marker in FOLLOWUP_MARKERS)):
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
        "retrieval_query": (question[:700] if named_titles else
                            (previous_question[:500] + " " + question)[:700]),
        "context_note": context_note,
        "reason": "延续上一轮已确认研究对象：" + str(document.get("planid")),
    }


def public_resolution(resolution: dict) -> dict:
    """Small, serializable trace; never include previous answers or claim text."""
    keys = ("status", "previous_question", "documents", "inherited_planids",
            "reason", "clarification", "requested_constraints", "retrieval_query",
            "context_note")
    public = {key: resolution.get(key) for key in keys}
    public["documents"] = [
        {"planid": doc.get("planid"), "title": doc.get("title"),
         "area": doc.get("area"), "fyp": doc.get("fyp")}
        for doc in resolution.get("documents", [])
    ]
    return public
