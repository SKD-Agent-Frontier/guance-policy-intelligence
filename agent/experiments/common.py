"""Common read-only RAG adapter for A-E experiments. Never mutates the knowledge base."""
import json
import time
from collections import Counter

from guance_agent.kb import get_kb


def collect_passages(question, settings, *, limit=8, per_doc=3):
    """Deterministic baseline: search once, then read each selected hit from source."""
    kb = get_kb(settings, require_warm=(settings.kb_backend == "rag"))
    started = time.monotonic()
    hits = kb.search(question, None, None, settings.top_k_max)
    found = []
    seen = set()
    counts = Counter()
    for hit in hits:
        if hit.chunk_id in seen or counts[hit.planid] >= per_doc:
            continue
        seen.add(hit.chunk_id)
        counts[hit.planid] += 1
        try:
            passage = kb.read(hit.planid, None, min(settings.read_max_chars, 3200),
                              chunk_id=hit.chunk_id)
        except Exception:
            continue
        if not passage.content.strip():
            continue
        found.append({
            "id": "S" + str(len(found) + 1), "planid": hit.planid,
            "chunk_id": hit.chunk_id, "title": hit.title,
            "area": hit.area, "fyp": hit.fyp, "section": hit.section_path,
            "content": passage.content[:3200],
        })
        if len(found) >= limit:
            break
    return found, {"search_hits": len(hits), "read_count": len(found),
                   "retrieval_ms": round((time.monotonic() - started) * 1000)}


def build_kb_tools(settings, calls):
    """Build plain LangChain callable tools, keeping only search/read access."""
    from langchain_core.tools import tool

    kb = get_kb(settings, require_warm=(settings.kb_backend == "rag"))
    discovered = set()

    @tool
    def search_policy(query: str, top_k: int = 8) -> str:
        """Search policy knowledge-base text; returns true source IDs and snippets."""
        start = time.monotonic()
        try:
            hits = kb.search(query[:200], None, None, max(1, min(12, int(top_k))))
            discovered.update(item.planid for item in hits)
            data = [{"planid": x.planid, "chunk_id": x.chunk_id, "title": x.title,
                     "area": x.area, "fyp": x.fyp, "section": x.section_path,
                     "snippet": x.content[:240]} for x in hits[:12]]
            calls.append({"tool": "search_policy", "status": "ok",
                          "ms": round((time.monotonic()-start)*1000), "count": len(data)})
            return json.dumps({"status": "ok", "hits": data}, ensure_ascii=False)
        except Exception as exc:
            calls.append({"tool": "search_policy", "status": type(exc).__name__})
            return json.dumps({"status": "error", "kind": type(exc).__name__})

    @tool
    def read_policy(planid: str, chunk_id: str) -> str:
        """Read original policy text by planid and chunk_id discovered via search_policy."""
        start = time.monotonic()
        if planid not in discovered:
            return json.dumps({"status": "out_of_scope", "message": "先检索该文献"})
        try:
            passage = kb.read(planid, None, min(settings.read_max_chars, 5000), chunk_id=chunk_id)
            calls.append({"tool": "read_policy", "status": "ok",
                          "ms": round((time.monotonic()-start)*1000), "planid": planid})
            return json.dumps({"status": "ok", "planid": planid, "chunk_id": chunk_id,
                               "text": passage.content[:5000]}, ensure_ascii=False)
        except Exception as exc:
            calls.append({"tool": "read_policy", "status": type(exc).__name__})
            return json.dumps({"status": "error", "kind": type(exc).__name__})
    return [search_policy, read_policy]


def answer_text(result):
    messages = result.get("messages") or []
    content = getattr(messages[-1], "content", "") if messages else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(v.get("text", "") for v in content if isinstance(v, dict))
    return str(content)


def model_call_count(result):
    return sum(1 for m in result.get("messages", [])
               if getattr(m, "type", "") == "ai")
