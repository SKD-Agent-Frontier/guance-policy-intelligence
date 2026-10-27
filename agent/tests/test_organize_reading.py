"""S2 mock tests: limits, partial failure and registered coordinates."""

import time

import pytest

from guance_agent.business import BusinessError
from guance_agent.business.organize.discovery import discover
from guance_agent.business.organize.reading import read_candidates
from guance_agent.business.organize.scope import parse_scope
from guance_agent.config import Settings

from .organize_fakes import MockServices, MultiDocKB, mock_context


async def prepare_mock(*, count=8, settings=None):
    task, ctx = mock_context(kb=MultiDocKB(count=count), settings=settings)
    services = MockServices(ctx)
    found = await discover(parse_scope(task, ctx), services)
    return found, ctx, services


async def test_mock_read_caps_and_first_pass_document_coverage():
    found, ctx, services = await prepare_mock()
    reading = await read_candidates(found, ctx, services)
    assert len(services.reads) == len(reading.windows) == 10
    assert len({w.planid for w in reading.windows}) == 6
    assert max(s.chunks_read for s in reading.policies.values()) == 2
    assert reading.policies["P006"].read_status == "not_read"
    assert set(reading.limits) == {"max_docs_read", "max_reads"}


async def test_mock_prefers_different_sections_after_best_scoring_chunk():
    found, ctx, services = await prepare_mock(count=1)
    found.hits[1].section_path = found.hits[0].section_path
    await read_candidates(found, ctx, services)
    assert [cid for _, cid in services.reads] == ["P000-c0", "P000-c2"]


async def test_mock_settings_budget_preserves_reserve():
    found, ctx, services = await prepare_mock(settings=Settings(max_tool_calls=5))
    reading = await read_candidates(found, ctx, services)
    assert len(services.reads) == 2  # one search, two reads, two reserved
    assert "max_reads" in reading.limits


async def test_mock_tool_budget_retains_read_windows():
    found, ctx, services = await prepare_mock(count=3)
    original = services.read

    async def read(*args, **kwargs):
        if services.reads:
            raise BusinessError("TOOL_BUDGET")
        return await original(*args, **kwargs)

    services.read = read
    reading = await read_candidates(found, ctx, services)
    assert len(reading.windows) == 1
    assert reading.limits == ["stopped_for_tool_budget"]


async def test_mock_reserves_model_time():
    found, ctx, services = await prepare_mock()
    services.deadline = time.monotonic() + 74
    reading = await read_candidates(found, ctx, services)
    assert reading.limits == ["max_docs_read", "stopped_for_time"]
    assert not services.reads


@pytest.mark.parametrize("error", ["RUN_TIMEOUT", "MAX_TURNS", "MODEL_UNAVAILABLE"])
async def test_mock_non_budget_errors_propagate(error):
    found, ctx, services = await prepare_mock()
    services.read_error = error
    with pytest.raises(BusinessError) as exc:
        await read_candidates(found, ctx, services)
    assert exc.value.code == error


@pytest.mark.parametrize(("status", "expected"), [
    ("no_text", "no_text"), ("not_found", "read_failed"),
    ("invalid_argument", "read_failed"), ("timeout", "read_failed"),
])
async def test_mock_raw_read_outcomes_preserved(status, expected):
    found, ctx, services = await prepare_mock(count=1)
    services.read_statuses = [status, status]
    reading = await read_candidates(found, ctx, services)
    assert not reading.windows
    state = reading.policies["P000"]
    assert state.read_status == expected
    assert all(a["status"] == status for a in state.read_attempts)


async def test_mock_unregistered_evidence_never_forms_window():
    found, ctx, services = await prepare_mock(count=1)
    services.register_reads = False
    reading = await read_candidates(found, ctx, services)
    assert not reading.windows
    assert reading.policies["P000"].chunks_read == 0


async def test_mock_invalid_read_coordinates_cannot_reuse_search_evidence():
    found, ctx, services = await prepare_mock(count=1)
    ctx.evidence.add_result(found.hits, "kb_search")
    ctx.kb.bad_coordinates.add("P000")
    reading = await read_candidates(found, ctx, services)
    assert not reading.windows


async def test_mock_expired_deadline_is_timeout_not_graceful_stop():
    found, ctx, services = await prepare_mock()
    services.deadline = time.monotonic() - 1
    with pytest.raises(BusinessError) as exc:
        await read_candidates(found, ctx, services)
    assert exc.value.code == "RUN_TIMEOUT"
