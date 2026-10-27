"""A–K offline mock integration through real submit, services and verification.

Only the KB and model are doubles. Passing these tests is not real RAG/model acceptance.
"""

import json
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from guance_agent import business, orchestrator, runtime
from guance_agent.business import BusinessError, BusinessServices
from guance_agent.business.organize import OrganizeModule
from guance_agent.main import submit
from guance_agent.verify import NUMBER

from .organize_fakes import MultiDocKB, mock_payload


@pytest.fixture
def mock_setup(env, monkeypatch):
    kb = MultiDocKB()
    state = SimpleNamespace(kb=kb, messages=[], payload=mock_payload(kb), responses=[], error=None,
                            calls=[], deadline_after_model=None)
    settings = replace(env[0], llm_base_url="https://example.invalid/v1",
                       llm_api_key="offline-mock-key")
    monkeypatch.setattr(business, "_MODULES", {})
    monkeypatch.setattr(orchestrator, "get_kb", lambda _: kb)
    monkeypatch.setattr(runtime, "get_client", lambda _: object())
    original = OrganizeModule.__call__

    async def observed(module, task, ctx, services):
        state.calls.append((task.targets, ctx.scope, ctx.route))
        return await original(module, task, ctx, services)

    async def mock_call_model(services, messages, max_tokens=None):
        state.messages.append(deepcopy(messages))
        services.telemetry["model_calls"] += 1
        if state.deadline_after_model is not None:
            services.deadline = time.monotonic() + state.deadline_after_model
        if state.error:
            raise BusinessError(state.error)
        return state.responses.pop(0) if state.responses else json.dumps(state.payload, ensure_ascii=False)

    monkeypatch.setattr(OrganizeModule, "__call__", observed)
    monkeypatch.setattr(BusinessServices, "call_model", mock_call_model)
    business.register(OrganizeModule())
    return settings, state


async def run_mock(setup, question="梳理十四五时期的科技创新政策", **kwargs):
    settings, _ = setup
    return await submit(question, settings=settings, task_type="organize", **kwargs)


async def test_mock_a_open_topic_verified_structured_and_exact_coverage(mock_setup):
    result = await run_mock(mock_setup)
    _, state = mock_setup
    assert result.status == "completed" and result.error is None
    assert result.route["targets"] is None and state.calls[0][:2] == (None, None)
    assert result.evidence_status == "verified" and len(result.claims) == 4
    assert "answer_rebuilt" not in result.meta
    coverage = result.structured["coverage"]
    assert {k: v for k, v in coverage.items() if isinstance(v, int)} == dict(
        documents_found=4, documents_after_filter=4, documents_read=4,
        documents_summarized=4, chunks_read=8)
    assert result.structured["scope"]["effective"]["fyp"] == "第十四个五年规划"
    assert len(result.structured["groups"]) == 1
    assert result.meta["quote_verification"]["approved"] == 4
    assert result.meta["number_verification"] == "literal_only"
    for claim in result.claims:
        source = state.kb.get_chunk_source(claim.chunk_ids[0])
        assert len(claim.quotes) == 1 and claim.quotes[0] in source.content


async def test_mock_b_multiple_queries_and_chunks_count_each_policy_once(mock_setup):
    _, state = mock_setup
    for policy in state.payload["policies"]:
        policy["points"][0]["topic"] = "平台建设"
    result = await run_mock(mock_setup, "梳理科技创新，按平台建设和人才培养分类")
    assert len(state.kb.search_calls) == 3
    assert result.structured["coverage"]["documents_found"] == 4
    assert len({p["planid"] for p in result.structured["policies"]}) == 4


async def test_mock_c_one_policy_is_explicitly_insufficient(mock_setup):
    _, state = mock_setup
    state.kb.hits = [h for h in state.kb.hits if h.planid == "P000"]
    result = await run_mock(mock_setup)
    assert result.status == "completed" and result.evidence_status == "insufficient"
    assert result.structured["coverage"]["documents_summarized"] == 1
    assert "有效摘要文献不足" in result.structured["coverage"]["note"]
    assert "证据仍不充分" in result.answer
    assert "全面" not in result.answer and "全部政策" not in result.answer


async def test_mock_d_empty_results_never_call_model(mock_setup):
    _, state = mock_setup
    state.kb.hits = []
    result = await run_mock(mock_setup)
    assert result.status == "completed" and result.structured == {}
    assert not state.messages and "未检索到相关政策原文" in result.answer


async def test_mock_e_invalid_points_are_removed_before_foundation_verification(mock_setup):
    _, state = mock_setup
    valid = state.payload["policies"][0]["points"][0]
    state.payload["policies"].append(dict(planid="forged", relevance="high", points=[valid]))
    bad_cid = {**valid, "chunk_id": "forged"}
    cross = {**valid, "chunk_id": "P001-c0"}
    no_anchor = {**valid, "anchors": ["不存在的原文"]}
    wrong_number = {**valid, "text": "建设科技创新平台9999个"}
    state.payload["policies"][0]["points"] = [bad_cid, cross, no_anchor, wrong_number, valid]
    result = await run_mock(mock_setup)
    assert result.evidence_status == "verified" and result.structured
    assert len(result.claims) == 4 and "answer_rebuilt" not in result.meta
    assert result.meta["quote_verification"]["rejected"] == 0


async def test_mock_truncated_number_cannot_borrow_digit_elsewhere_in_source(mock_setup, monkeypatch):
    from guance_agent.business.organize import prompt

    _, state = mock_setup
    cap = 120
    partial = "建设科技创新平台2"
    prefix = "甲" * (cap - len(partial) - 1) + "。"
    # The real value is 20. A separate sentence has 2, so the foundation's
    # whole-observed-range numeric check alone cannot reject the wrong claim.
    content = prefix + "建设科技创新平台20个。另安排资金2万元。"
    hit = state.kb.hits[0]
    state.kb.hits[0] = hit.model_copy(update=dict(
        content=content, char_end=len(content), chunk_char_end=len(content),
        section_char_end=len(content)))
    monkeypatch.setattr(prompt, "CHUNK_SHOW_CHARS", cap)
    state.payload["policies"][0]["points"][0]["text"] = "建设科技创新平台2个"
    result = await run_mock(mock_setup)
    assert content[:cap] == prefix + partial
    assert prefix + partial + "\n\n" in state.messages[0][1]["content"]
    assert result.evidence_status == "verified" and len(result.claims) == 3
    assert result.structured["coverage"]["documents_summarized"] == 3
    assert result.structured["policies"][0]["read_status"] == "read_no_valid_points"
    assert "answer_rebuilt" not in result.meta
    assert result.meta["quote_verification"]["approved"] == 3


async def test_mock_f_targets_fixed_and_missing_target_has_no_fabricated_metadata(mock_setup):
    _, state = mock_setup
    result = await run_mock(mock_setup, targets=["P000", "P001", "missing"])
    assert state.calls[0][:2] == (["P000", "P001", "missing"], ("P000", "P001", "missing"))
    assert all(call[-1] == ("P000", "P001", "missing") for call in state.kb.search_calls)
    missing = result.structured["policies"][-1]
    assert missing["planid"] == "missing" and missing["read_status"] == "not_retrieved"
    assert missing["title"] is missing["summary"] is None
    assert all(pid != "missing" for pid, _, _ in state.kb.read_calls)
    assert result.structured["scope"]["effective"]["fyp"] is None


@pytest.mark.parametrize("question", [
    "梳理北京市和上海市科技创新政策", "梳理十三五和十四五科技创新政策",
])
async def test_mock_g_multi_scope_does_not_invoke_module(mock_setup, question):
    result = await run_mock(mock_setup, question)
    assert result.status == "completed" and "缩小范围" in result.answer
    assert mock_setup[1].calls == []


async def test_mock_h_long_question_produces_bounded_queries(mock_setup):
    result = await run_mock(mock_setup, "梳理科技创新" + "相关政策" * 100)
    assert result.status == "completed" and result.structured
    assert all(len(c[0]) <= 40 for c in mock_setup[1].kb.search_calls)


@pytest.mark.parametrize(("code", "status"), [
    ("MODEL_UNAVAILABLE", "failed"), ("MODEL_UNAUTHORIZED", "failed"),
    ("RUN_TIMEOUT", "limit_reached"), ("MAX_TURNS", "limit_reached"),
    ("OUTPUT_PARSE", "failed"),
])
async def test_mock_i_model_errors_propagate_without_retry(mock_setup, code, status):
    mock_setup[1].error = code
    result = await run_mock(mock_setup)
    assert result.status == status and result.error.code == code
    assert len(mock_setup[1].messages) == 1


async def test_mock_j_parse_retry_and_tool_model_counts(mock_setup):
    settings, state = mock_setup
    state.responses = ['{"policies": [']
    result = await run_mock(mock_setup)
    assert result.evidence_status == "verified" and result.structured
    assert len(state.messages) == result.meta["business_telemetry"]["model_calls"] == 2
    assert len(result.meta["tool_calls"]) <= settings.max_tool_calls
    assert "只输出一个完整的 JSON" in state.messages[-1][-1]["content"]


def test_mock_k_production_import_does_not_register():
    code = ("from guance_agent import business; "
            "from guance_agent.business.organize import OrganizeModule; "
            "assert business.get_module('organize') is None; assert business._MODULES == {}")
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)


async def test_mock_parse_retry_exhausted_or_not_enough_time(mock_setup):
    _, state = mock_setup
    state.responses = ["bad", "bad"]
    result = await run_mock(mock_setup)
    assert result.status == "failed" and result.error.code == "OUTPUT_PARSE"
    assert len(state.messages) == 2
    state.messages.clear()
    state.responses = ["bad"]
    state.deadline_after_model = 50
    result = await run_mock(mock_setup)
    assert result.error.code == "OUTPUT_PARSE" and len(state.messages) == 1


async def test_mock_no_valid_points_preserve_only_discovered_identity(mock_setup):
    _, state = mock_setup
    state.payload = {"policies": []}
    result = await run_mock(mock_setup)
    assert result.status == "completed" and not result.claims
    assert set(result.structured) == {"documents", "verification"}
    assert result.structured["verification"]["policy_facts"] == "unverified"
    assert len(result.structured["documents"]) == 4


async def test_mock_partial_search_failure_and_no_text_remain_honest(mock_setup):
    _, state = mock_setup
    state.kb.search_statuses = ["backend_error"]
    state.kb.no_text.add("P000")
    state.kb.fail_reads.add("P003")
    result = await run_mock(mock_setup)
    assert result.status == "completed" and result.structured
    assert result.structured["scope"]["effective"]["queries"][0]["status"] == "backend_error"
    assert "部分查询" in result.structured["coverage"]["note"]
    policies = {p["planid"]: p for p in result.structured["policies"]}
    assert policies["P000"]["read_status"] == "no_text"
    assert policies["P003"]["read_status"] == "read_failed"
    assert policies["P003"]["read_attempts"][0]["status"] == "not_found"
    assert policies["P000"]["summary"] is policies["P003"]["summary"] is None


async def test_mock_low_budget_keeps_verified_partial_result(mock_setup):
    settings, state = mock_setup
    settings = replace(settings, max_tool_calls=5)
    result = await run_mock((settings, state))
    assert len(result.meta["tool_calls"]) <= 5
    assert result.evidence_status == "insufficient"
    assert result.structured["coverage"]["documents_read"] == 1
    assert "max_reads" in result.structured["coverage"]["limits"]


async def test_mock_read_budget_error_keeps_earlier_windows(mock_setup, monkeypatch):
    original = BusinessServices.read
    calls = []

    async def read(services, *args, **kwargs):
        if calls:
            raise BusinessError("TOOL_BUDGET")
        calls.append(1)
        return await original(services, *args, **kwargs)

    monkeypatch.setattr(BusinessServices, "read", read)
    result = await run_mock(mock_setup)
    assert result.status == "completed" and result.evidence_status == "insufficient"
    assert result.structured["coverage"]["limits"] == ["stopped_for_tool_budget"]


async def test_mock_no_time_to_read_preserves_identity_without_model(mock_setup):
    settings, state = mock_setup
    settings = replace(settings, run_timeout=60)
    result = await run_mock((settings, state))
    assert not state.messages and not state.kb.read_calls
    assert result.structured["verification"]["policy_facts"] == "unverified"


async def test_mock_type_filter_and_answer_number_rule(mock_setup):
    _, state = mock_setup
    for hit in state.kb.hits:
        if hit.planid == "P000":
            hit.plantype_label = None
    question = "梳理十四五时期的专项规划中有关数字经济的政策"
    result = await run_mock(mock_setup, question)
    assert result.structured["coverage"]["documents_after_filter"] == 3
    assert result.structured["scope"]["post_filters"][0]["excluded_unknown"] == 1
    allowed = set(NUMBER.findall(question)) | {n for c in result.claims for n in NUMBER.findall(c.text)}
    assert set(NUMBER.findall(result.answer)) <= allowed
    assert "仅对本轮候选生效" in result.answer


def test_mock_quote_checker_respects_observed_ranges():
    kb = MultiDocKB(count=1)
    cid = kb.hits[0].chunk_id
    source = kb.get_chunk_source(cid)
    quote = source.content.split("。")[1] + "。"
    assert not kb.verify_quote(cid, quote, [(0, 10)])
    assert kb.verify_quote(cid, quote, [(0, len(source.content))])
