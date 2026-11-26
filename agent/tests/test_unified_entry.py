import json
import time
from dataclasses import replace

import pytest

import guance_agent.main as agent_main
import guance_agent.orchestrator as orchestrator
from guance_agent.business.contract import BusinessOutcome
from guance_agent.context import RunContext
from guance_agent.kb import MockKB
from guance_agent.orchestrator import allowed_capabilities_for
from guance_agent.research_core import ResearchState, _import_capability_claims
from guance_agent.routing import decide
from guance_agent.runtime import Runtime
from guance_agent.schemas import Claim, EvidenceRef, RunResult

from .fake_model import FakeModel, tool


def test_explicit_task_type_locks_capability_and_qa_can_choose_registered_modules():
    registered = ("extract", "organize", "compare", "compare_plans")

    assert allowed_capabilities_for("compare", registered) == ("compare",)
    assert allowed_capabilities_for("qa", registered) == registered
    assert allowed_capabilities_for("unknown", registered) == ()


@pytest.mark.asyncio
async def test_submit_routes_every_valid_task_type_through_sdk(env, monkeypatch):
    settings, _ = env
    settings = replace(settings, llm_base_url="https://model.invalid/v1",
                       llm_api_key="offline-test-key")
    seen = []

    class RuntimeStub:
        def __init__(self, *_args):
            self.meta = {}

        def received(self):
            pass

        def prepare(self):
            pass

        def finish(self, *_args):
            pass

        def close(self):
            pass

    async def sdk(task, *_args, **_kwargs):
        seen.append(task.task_type)
        return RunResult(
            run_id=task.run_id, session_id=task.session_id, task_type=task.task_type,
            status="completed", evidence_status="none", answer="", claims=[],
            evidence_refs=[], dropped_chunk_ids=[], error=None, meta={},
        )

    monkeypatch.setattr(agent_main, "Runtime", RuntimeStub)
    monkeypatch.setattr(agent_main, "run_sdk_task", sdk)
    for task_type in ("qa", "extract", "organize", "compare", "compare_plans"):
        result = await agent_main.submit("按要求研究政策", settings=settings,
                                         task_type=task_type)
        assert result.task_type == task_type

    assert seen == ["qa", "extract", "organize", "compare", "compare_plans"]


@pytest.mark.asyncio
async def test_submit_sdk_setup_failure_uses_gateway_before_runtime_finish(env, monkeypatch):
    settings, _ = env
    settings = replace(settings, llm_base_url="https://model.invalid/v1",
                       llm_api_key="offline-test-key")
    events = []

    class RuntimeStub:
        def __init__(self, *_args):
            self.meta = {}

        def received(self):
            pass

        def prepare(self):
            pass

        def log(self, *_args, **_kwargs):
            pass

        def finish(self, *_args):
            events.append("runtime.finish")

        def close(self):
            pass

    async def sdk(*_args, **_kwargs):
        raise RuntimeError("SDK initialization failed")

    async def gateway(result, _task, ctx, _runtime, *, research_state=None):
        events.append("gateway")
        assert ctx is None
        assert result.error.code == "INTERNAL"
        return result

    monkeypatch.setattr(agent_main, "Runtime", RuntimeStub)
    monkeypatch.setattr(agent_main, "run_sdk_task", sdk)
    import guance_agent.delivery_gateway as gateway_module
    monkeypatch.setattr(gateway_module, "finalize_public_delivery", gateway)

    result = await agent_main.submit("按要求研究政策", settings=settings)

    assert result.error.code == "INTERNAL"
    assert events == ["gateway", "runtime.finish"]


@pytest.mark.asyncio
async def test_submit_qa_uses_compare_capability_and_publishes_only_verified_fact(
        env, monkeypatch):
    settings, _ = env
    settings = replace(settings, llm_base_url="https://model.invalid/v1",
                       llm_api_key="offline-test-key")
    question = "比较 planid TEST-001 与 planid TEST-002 的农业灌溉设施措施"
    targets = ["TEST-001", "TEST-002"]
    proposal = "两项政策中的农业灌溉设施措施"
    quote = "完善农业灌溉设施"
    claim_text = "政策提出完善农业灌溉设施。"
    kb = MockKB(settings.mock_kb_path)
    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)
    monkeypatch.setattr(kb, "verify_quote", lambda _chunk, candidate, *_a, **_k:
                        candidate == quote, raising=False)

    task_context = RunContext(
        settings, kb, "run_capability_seed", "session_capability_seed",
        question=question, route=decide(question), scope=tuple(targets),
    )
    seed_state = ResearchState.create(
        task_context, question, time.monotonic() + 60,
        explicit_targets=tuple(targets), allowed_planids=tuple(targets),
    )
    requirements = seed_state.freeze_requirements([proposal])["requirements"]
    requirement_ids = [item["id"] for item in requirements]

    capability_calls = []

    class CompareCapability:
        name = "compare"

        async def __call__(self, task, _ctx, services):
            capability_calls.append(task.task_type)
            found = await services.search("农业灌溉设施", top_k=8)
            assert found.status == "ok"
            hit = next(item for item in found.data
                       if item.planid == "TEST-001"
                       and item.chunk_id == "TEST-001__s01__p00")
            read = await services.read(hit.planid, chunk_id=hit.chunk_id)
            assert read.status == "ok" and quote in read.data.content
            return BusinessOutcome(
                answer="未审核的对比判断不得发布。",
                claims=[Claim(text=claim_text, chunk_ids=[hit.chunk_id], quotes=[quote])],
                structured={"unreviewed_business_payload": "do not publish"},
            )

    monkeypatch.setattr(agent_main, "research_capabilities",
                        lambda: {"compare": CompareCapability()})
    model = FakeModel([
        [tool("search", query="农业灌溉设施", top_k=8, requirements=[proposal])],
        [tool("run_capability", capability="compare", targets=targets)],
        [tool("record_evidence_batch", items=[{
            "candidate_id": "capability-requirement-link", "claim": claim_text,
            "quote": quote, "chunk_id": "TEST-001__s01__p00",
            "requirement_ids": requirement_ids,
        }])],
        [tool("submit_draft", evidence_candidates=[], blocks=[{
            "id": "capability-fact", "title": "农业灌溉设施措施", "kind": "fact",
            "text": claim_text, "requirement_ids": requirement_ids,
            "evidence_ids": ["f1"],
        }])],
    ])
    monkeypatch.setattr(orchestrator, "chat_model", lambda *_args: model)

    async def audit(self, messages, **_kwargs):
        payload = json.loads(messages[1]["content"])
        return json.dumps({
            "passed": True,
            "covered_requirement_ids": [item["id"]
                                         for item in payload["frozen_requirements"]],
            "approved_block_ids": [item["id"] for item in payload["blocks"]],
            "gaps": [], "unsupported_claims": [],
        }, ensure_ascii=False)

    monkeypatch.setattr(Runtime, "complete_text", audit)

    result = await agent_main.submit(question, settings=settings, task_type="qa",
                                     targets=targets)

    assert capability_calls == ["compare"]
    assert model.calls == 4
    assert result.status == "completed"
    assert result.evidence_status == "verified"
    assert result.claims[0].text == claim_text
    assert result.structured["schema_version"] == "qa-report/1"
    assert "unreviewed_business_payload" not in json.dumps(result.structured)


def test_capability_import_requires_exact_quote_from_this_run(env):
    settings, task = env
    settings = replace(settings, llm_base_url="https://model.invalid/v1",
                       llm_api_key="offline-test-key")
    question = "研究农业灌溉设施政策"
    task = task.model_copy(update={"question": question, "targets": ["TEST-001"]})
    run = RunContext(settings, MockKB(settings.mock_kb_path), task.run_id,
                     task.session_id, question=question, route=decide(question),
                     scope=("TEST-001",))
    state = ResearchState.create(
        run, question, time.monotonic() + 60,
        explicit_targets=("TEST-001",), allowed_planids=("TEST-001",),
    )
    state.freeze_requirements(["农业灌溉设施事实"])
    chunk_id = "TEST-001__s01__p00"
    page = "完善农业灌溉设施，发展绿色农业。"
    run.evidence.register(EvidenceRef(
        planid="TEST-001", chunk_id=chunk_id, title="农业规划", area="甲市",
        fyp=None, admin_level=None, plantype_label=None, section_idx=1,
        section_path="农业发展", char_start=0, char_end=len(page),
        chunk_char_start=0, chunk_char_end=len(page), source="kb_read",
        observed_ranges=[(0, len(page))], complete=True,
    ))
    state.read_text[chunk_id] = [page]

    valid = Claim(text="政策提出完善农业灌溉设施。", chunk_ids=[chunk_id],
                  quotes=["完善农业灌溉设施"])
    invalid = Claim(text="政策新增 99 个设施。", chunk_ids=[chunk_id],
                    quotes=["原文没有的引文"])

    composite = Claim(text="综合两个来源得出的结论", chunk_ids=[chunk_id, "another"],
                      quotes=["完善农业灌溉设施", "第二来源"])
    assert _import_capability_claims(state, "organize", [valid, invalid, composite]) == 1
    assert len(state.claims) == 1
    assert state.claims[0].quotes == ["完善农业灌溉设施"]

@pytest.mark.asyncio
async def test_search_candidates_cannot_select_or_replace_comparison_objects(env):
    settings, task = env
    ctx = RunContext(settings, MockKB(settings.mock_kb_path), task.run_id, task.session_id)
    state = ResearchState.create(ctx, task.question, time.monotonic() + 10)
    state.candidate_planids.update(["first", "second", "third"])

    async def forbidden(*_args):
        pytest.fail("A search hit cannot silently become a comparison object")

    async def compare(targets):
        return await orchestrator._execute_research_capability(
            "compare", state, targets, task, settings, None, ctx, decide(task.question),
            state.deadline, {"compare": forbidden})

    assert (await compare(["first", "second"]))["needs_targets"]
    state.confirmed_targets = ("second", "third")
    assert (await compare(["first", "third"]))["needs_targets"]


@pytest.mark.asyncio
async def test_extract_inherits_only_the_confirmed_policy(env):
    settings, task = env
    ctx = RunContext(settings, MockKB(settings.mock_kb_path), task.run_id, task.session_id)
    state = ResearchState.create(ctx, task.question, time.monotonic() + 10)
    state.confirmed_targets = ("selected",)
    state.candidate_planids.update(["selected", "other"])
    calls = []

    async def extract(bound_task, bound_context, _services):
        calls.append((bound_task.targets, bound_context.scope))
        return BusinessOutcome(answer="")

    runtime = Runtime(settings, task)
    try:
        for requested in (None, [], ["other"]):
            result = await orchestrator._execute_research_capability(
                "extract", state, requested, task, settings, runtime, ctx, decide(task.question),
                state.deadline, {"extract": extract})
            assert bool(result.get("needs_targets")) == (requested == ["other"])
        assert calls == [(["selected"], ("selected",))] * 2
    finally:
        runtime.close()
