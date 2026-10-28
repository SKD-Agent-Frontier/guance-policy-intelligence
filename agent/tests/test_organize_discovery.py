"""S1 mock unit tests; search scope is inherited through the service contract."""

import pytest

from guance_agent.business.organize.discovery import discover
from guance_agent.business.organize.scope import parse_scope

from .organize_fakes import MockServices, MultiDocKB, mock_context


async def test_mock_dedup_first_seen_order_queries_and_scope_inheritance():
    task, ctx = mock_context("梳理科技创新，按人才培养和平台建设分类")
    services = MockServices(ctx)
    found = await discover(parse_scope(task, ctx), services)
    assert [p.planid for p in found.candidates] == ["P000", "P001", "P002", "P003"]
    assert found.documents_found == 4
    assert [q["new_documents"] for q in found.queries] == [4, 0, 0]
    assert all(len(p.chunk_ids) == 3 for p in found.candidates)
    assert all(kwargs == {} for _, kwargs in services.queries)


async def test_mock_post_filter_unknown_and_conflicting_metadata():
    kb = MultiDocKB()
    kb.hits[0].plantype_label = None
    for hit in kb.hits:
        if hit.planid == "P001":
            hit.plantype_label = "总体规划纲要"
    task, ctx = mock_context("梳理专项规划中科技创新政策", kb=kb)
    scope = parse_scope(task, ctx)
    found = await discover(scope, MockServices(ctx))
    assert found.documents_found == 4
    assert [p.planid for p in found.candidates] == ["P002", "P003"]
    assert scope.post_filters[0]["excluded_unknown"] == 1
    assert scope.post_filters[0]["excluded_mismatch"] == 1
    assert "仅对本轮候选" in scope.post_filters[0]["note"]
    assert scope.unapplied[0]["condition"] == "plantypes"


@pytest.mark.parametrize("status", ["empty", "timeout", "backend_error", "invalid_argument"])
async def test_mock_query_failure_continues(status):
    task, ctx = mock_context("梳理科技创新，按人才培养分类")
    services = MockServices(ctx)
    services.statuses = [status]
    found = await discover(parse_scope(task, ctx), services)
    assert found.queries[0]["status"] == status
    assert found.documents_found == 4 and len(found.queries) == 2


async def test_mock_long_queries_bounded_and_duplicates_removed():
    task, ctx = mock_context("科技创新" * 100 + "按人才培养和平台建设分类")
    services = MockServices(ctx)
    found = await discover(parse_scope(task, ctx), services)
    assert len(found.queries) == 1
    assert all(len(q) <= 40 for q, _ in services.queries)


async def test_mock_missing_targets_not_retrieved_and_no_extra_fallback_when_enough():
    task, ctx = mock_context(targets=["P000", "missing"])
    found = await discover(parse_scope(task, ctx), MockServices(ctx))
    assert found.missing_targets == ["missing"]
    task, ctx = mock_context(kb=MultiDocKB(count=8))
    found = await discover(parse_scope(task, ctx), MockServices(ctx))
    assert len(found.queries) == 1


@pytest.mark.parametrize(("kind", "metadata"), [
    ("政府工作报告", {"fyp": "政府工作报告"}),
    ("政府工作报告", {"title": "市政府工作报告", "fyp": "其他"}),
    ("规划文件", {"fyp": "第十四个五年规划"}),
])
async def test_mock_type_filter_uses_actual_metadata(kind, metadata):
    kb = MultiDocKB(count=1)
    kb.hits = [h.model_copy(update=metadata) for h in kb.hits]
    task, ctx = mock_context(f"梳理{kind}中的科技创新政策", kb=kb)
    found = await discover(parse_scope(task, ctx), MockServices(ctx))
    assert len(found.candidates) == 1
