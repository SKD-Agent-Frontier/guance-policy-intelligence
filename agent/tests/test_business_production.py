"""Offline submit coverage for modules registered by the production entry point."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from guance_agent import business, orchestrator, runtime
from guance_agent.business import BusinessServices
from guance_agent.business.compare import PolicyComparisonModule, SchemeComparisonModule
from guance_agent.business.organize import OrganizeModule
from guance_agent.main import submit

from . import fake_model
from .organize_fakes import MultiDocKB, mock_payload
from .test_compare import ComparisonKB, ComparisonModel


@pytest.fixture
def production_modules():
    assert set(business._MODULES) == {"compare", "compare_plans", "organize"}
    assert isinstance(business.get_module("compare"), PolicyComparisonModule)
    assert isinstance(business.get_module("compare_plans"), SchemeComparisonModule)
    assert isinstance(business.get_module("organize"), OrganizeModule)


@pytest.mark.parametrize("targets", [None, ["P000", "P001"]])
async def test_production_organize_submit_supports_open_topic_and_targets(
        env, monkeypatch, production_modules, targets):
    settings = replace(env[0], llm_base_url="https://example.invalid/v1",
                       llm_api_key="offline-test-key")
    kb = MultiDocKB()
    monkeypatch.setattr(orchestrator, "get_kb", lambda _: kb)
    monkeypatch.setattr(runtime, "get_client", lambda _: object())
    payload = mock_payload(kb)

    async def call_model(services, messages, max_tokens=None):
        services.telemetry["model_calls"] += 1
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(BusinessServices, "call_model", call_model)
    result = await submit("梳理十四五时期的科技创新政策", settings=settings,
                          task_type="organize", targets=targets)

    assert result.status == "completed" and result.error is None
    assert result.evidence_status == "verified" and result.structured["schema_version"] == "organize/1"
    assert result.route["targets"] == targets
    assert result.structured["scope"]["targets"] == targets
    assert all(call[-1] == (tuple(targets) if targets is not None else None)
               for call in kb.search_calls)


@pytest.mark.parametrize("task_type", ["compare", "compare_plans"])
async def test_production_comparison_submit_remains_available(
        env, monkeypatch, production_modules, task_type):
    settings = replace(env[0], llm_base_url="https://example.invalid/v1",
                       llm_api_key="offline-test-key")
    kb, model = ComparisonKB(), ComparisonModel()
    client = SimpleNamespace(chat=SimpleNamespace(completions=model))
    monkeypatch.setattr(orchestrator, "get_kb", lambda _: kb)
    monkeypatch.setattr(runtime, "get_client", lambda _: client)

    result = await submit("比较两项人才政策", settings=settings, task_type=task_type,
                          targets=["T001", "T002"])

    assert result.status == "completed" and result.evidence_status == "verified"
    assert result.task_type == task_type
    assert result.route["targets"] == ["T001", "T002"]
    assert [item["planid"] for item in result.structured["objects"]] == ["T001", "T002"]
    assert len(model.calls) == 4


async def test_production_qa_and_extract_submit_keep_claim_evidence_links(
        env, monkeypatch, production_modules):
    settings = replace(env[0], llm_base_url="https://example.invalid/v1",
                       llm_api_key="offline-test-key")
    kb = MultiDocKB()
    kb.hits = [hit.model_copy(update={
        "planid": f"policy{chr(65 + index // 3)}",
        "chunk_id": f"policy{chr(65 + index // 3)}-c{index % 3}",
    }) for index, hit in enumerate(kb.hits)]
    monkeypatch.setattr(orchestrator, "get_kb", lambda _: kb)
    monkeypatch.setattr(runtime, "get_client", lambda _: object())
    quote = "完善成果转化服务体系。"
    claims = [{"text": quote, "chunk_ids": ["policyA-c0"], "quotes": [quote],
               "dimension": "政策工具"}]
    qa_model = fake_model.FakeModel([
        [fake_model.tool("kb_search", query="科技创新", area=None, fyp=None, top_k=8)],
        [fake_model.tool("kb_read", planid="policyA", chunk_id="policyA-c0")],
        fake_model.answer(quote, claims),
    ])
    monkeypatch.setattr(orchestrator, "chat_model", lambda _settings, _client: qa_model)

    qa = await submit("科技创新平台有哪些安排", settings=settings, task_type="qa")

    class ExtractModel:
        async def create(self, **_kwargs):
            payload = json.dumps({"answer": quote, "claims": claims}, ensure_ascii=False)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=payload), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10))

    client = SimpleNamespace(chat=SimpleNamespace(completions=ExtractModel()))
    monkeypatch.setattr(runtime, "get_client", lambda _settings: client)
    extracted = await submit("提取科技创新政策工具", settings=settings, task_type="extract",
                             targets=["policyA"], dimensions=["政策工具"])

    for result, task_type in ((qa, "qa"), (extracted, "extract")):
        assert result.status == "completed" and result.task_type == task_type
        assert result.run_id and result.session_id and result.claims and result.evidence_refs, \
            result.model_dump_json()
        ref_ids = {ref.chunk_id for ref in result.evidence_refs}
        assert all(set(claim.chunk_ids) <= ref_ids for claim in result.claims)
    assert qa.structured is None
    assert extracted.structured["elements"][0]["dimension"] == "政策工具"
    assert qa.run_id != extracted.run_id


async def test_production_business_calls_in_one_session_do_not_share_results(
        env, monkeypatch, production_modules):
    settings = replace(env[0], llm_base_url="https://example.invalid/v1",
                       llm_api_key="offline-test-key")
    kb_state = {"value": MultiDocKB()}
    monkeypatch.setattr(orchestrator, "get_kb", lambda _: kb_state["value"])
    monkeypatch.setattr(runtime, "get_client", lambda _: object())
    original_call_model = BusinessServices.call_model

    async def call_organize_model(services, _messages, max_tokens=None):
        services.telemetry["model_calls"] += 1
        return json.dumps(mock_payload(kb_state["value"]), ensure_ascii=False)

    monkeypatch.setattr(BusinessServices, "call_model", call_organize_model)
    session_id = "s_production_sequence"
    organized = await submit("梳理十四五科技创新政策", settings=settings,
                             task_type="organize", session_id=session_id)

    kb_state["value"] = ComparisonKB()
    monkeypatch.setattr(BusinessServices, "call_model", original_call_model)
    comparison_model = ComparisonModel()
    client = SimpleNamespace(chat=SimpleNamespace(completions=comparison_model))
    monkeypatch.setattr(runtime, "get_client", lambda _: client)
    compared = await submit("比较两项人才政策", settings=settings, task_type="compare",
                            targets=["T001", "T002"], session_id=session_id)
    planned = await submit("分析两项人才政策方案", settings=settings,
                           task_type="compare_plans", targets=["T001", "T002"],
                           session_id=session_id)

    results = [organized, compared, planned]
    assert len({result.run_id for result in results}) == 3
    assert {result.session_id for result in results} == {session_id}
    assert [result.task_type for result in results] == ["organize", "compare", "compare_plans"]
    for result in results:
        assert result.status == "completed" and result.claims and result.evidence_refs, \
            f"{result.task_type}: {result.model_dump_json()}"
    assert organized.structured["schema_version"] == "organize/1"
    assert compared.structured["objects"] and planned.structured["scheme_analysis"]
    assert {ref.planid for ref in organized.evidence_refs} <= {"P000", "P001", "P002", "P003"}
    assert all({ref.planid for ref in result.evidence_refs} <= {"T001", "T002"}
               for result in (compared, planned))
