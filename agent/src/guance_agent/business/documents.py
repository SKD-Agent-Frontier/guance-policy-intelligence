"""Small helpers for grouping search hits by policy document."""
from pydantic import BaseModel, Field

from ..schemas import Hit


class PolicyRef(BaseModel):
    planid: str
    title: str | None = None
    area: str | None = None
    fyp: str | None = None
    admin_level: str | None = None
    plantype_label: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)


def group_by_document(hits: list[Hit]) -> list[PolicyRef]:
    """Keep first-seen document order and the first hit's metadata for each planid."""
    grouped: dict[str, PolicyRef] = {}
    for hit in hits:
        policy = grouped.get(hit.planid)
        if policy is None:
            # 后续命中即使元数据冲突，也不覆盖首条或用后条补全。
            policy = PolicyRef(
                planid=hit.planid,
                title=hit.title,
                area=hit.area,
                fyp=hit.fyp,
                admin_level=hit.admin_level,
                plantype_label=hit.plantype_label,
            )
            grouped[hit.planid] = policy
        if hit.chunk_id not in policy.chunk_ids:
            policy.chunk_ids.append(hit.chunk_id)
    return list(grouped.values())
