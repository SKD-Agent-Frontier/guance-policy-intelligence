"""S1: bounded search, shared document grouping and candidate-only post-filters."""

from dataclasses import dataclass, field

from guance_agent.business import PolicyRef, group_by_document
from guance_agent.schemas import Hit

from .constants import MAX_DOCS_READ, MAX_QUERIES, MAX_QUERY_CHARS
from .scope import OrganizeScope


@dataclass
class Discovery:
    candidates: list[PolicyRef] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    queries: list[dict] = field(default_factory=list)
    documents_found: int = 0
    missing_targets: list[str] = field(default_factory=list)


def _matches(policy: PolicyRef, kind: str) -> bool | None:
    if kind in {"总体规划纲要", "专项规划"}:
        return policy.plantype_label == kind if policy.plantype_label else None
    if kind == "政府工作报告":
        if policy.fyp == kind or kind in (policy.title or ""):
            return True
        return False if policy.fyp and policy.title else None
    return policy.fyp.endswith("五年规划") if policy.fyp else None


async def discover(scope: OrganizeScope, services) -> Discovery:
    found = Discovery()
    queries = [scope.topic]
    if scope.categories:
        queries.extend(f"{scope.topic} {category}" for category in scope.categories[:MAX_QUERIES - 1])
    seen_queries = set()
    for raw_query in queries:
        query = raw_query.strip()[:MAX_QUERY_CHARS]
        if not query or query in seen_queries or len(found.queries) >= MAX_QUERIES:
            continue
        seen_queries.add(query)
        before = {hit.planid for hit in found.hits}
        result = await services.search(query)
        hits = result.data if result.status == "ok" and isinstance(result.data, list) else []
        found.hits.extend(hits)
        found.queries.append(dict(query=query, status=result.status, hits=len(hits),
                                 new_documents=len({h.planid for h in hits} - before)))
        if len(found.queries) == 1 and not scope.categories:
            if len({hit.planid for hit in found.hits}) < MAX_DOCS_READ:
                queries.append(scope.fallback_query)
    grouped = group_by_document(found.hits)
    found.documents_found = len(grouped)
    found.missing_targets = [pid for pid in scope.targets or []
                             if pid not in {p.planid for p in grouped}]
    if not scope.plantypes:
        found.candidates = grouped
        return found
    unknown = mismatch = 0
    for policy in grouped:
        matches = [_matches(policy, kind) for kind in scope.plantypes]
        if True in matches:
            found.candidates.append(policy)
        elif None in matches:
            unknown += 1
        else:
            mismatch += 1
    scope.post_filters.append(dict(
        condition="plantypes", values=scope.plantypes, excluded_unknown=unknown,
        excluded_mismatch=mismatch, note="仅对本轮候选生效，不是全库严格筛选"))
    scope.unapplied.append(dict(condition="plantypes",
                               reason="检索接口无类型预过滤，已改为对本轮候选后过滤"))
    return found
