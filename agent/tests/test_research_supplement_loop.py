"""Bounded QA gap repair must revisit all measured requirements."""
import json
import time
from types import SimpleNamespace

import pytest

import guance_agent.qa_controlled as controlled_qa
import guance_agent.research_supplement as supplement
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import Claim, EvidenceRef


def _ref(chunk_id):
    return EvidenceRef(
        planid="2561", chunk_id=chunk_id, title="某地规划纲要", area="某市",
        fyp="十四五", admin_level=None, plantype_label=None,
        section_idx=1, section_path="发展方向", char_start=0, char_end=80,
        source="kb_read",
    )


def _part(text, chunk_id):
    return SimpleNamespace(
        status="completed", error=None, task_type="qa",
        claims=[Claim(text=text, chunk_ids=[chunk_id], quotes=[text])],
        evidence_refs=[_ref(chunk_id)], meta={},
        _qa_report_supplied=False, _qa_report_candidates=[],
        _qa_report_dimensions=[],
    )


def _part_many(values):
    claims = [Claim(text=text, chunk_ids=[chunk_id], quotes=[text])
              for text, chunk_id in values]
    return SimpleNamespace(
        status="completed", error=None, task_type="qa", claims=claims,
        evidence_refs=[_ref(chunk_id) for _, chunk_id in values],
        meta={}, _qa_report_supplied=False, _qa_report_candidates=[],
        _qa_report_dimensions=[],
    )


@pytest.mark.asyncio
async def test_supplement_batches_all_gaps_then_rechecks_until_covered(monkeypatch):
    result = SimpleNamespace(
        status="completed", error=None, task_type="qa", claims=[], evidence_refs=[],
        meta={"evidence_coverage": {"status": "insufficient"}},
        _qa_report_supplied=False, _qa_report_candidates=[], _qa_report_dimensions=[],
        answer="",
    )
    question = "概述该规划中产业、农业、服务业、科技和基础设施的部署及目标"
    actions = [
        {"requirement_id": "industry", "focus": "产业部署", "query": "产业部署",
         "target_planid": None, "gap_label": "产业部署缺口"},
        {"requirement_id": "agriculture", "focus": "农业部署", "query": "农业部署",
         "target_planid": None, "gap_label": "农业部署缺口"},
        {"requirement_id": "services", "focus": "服务业部署", "query": "服务业部署",
         "target_planid": None, "gap_label": "服务业部署缺口"},
        {"requirement_id": "science", "focus": "科技部署", "query": "科技部署",
         "target_planid": None, "gap_label": "科技部署缺口"},
        {"requirement_id": "infrastructure", "focus": "基础设施部署", "query": "基础设施部署",
         "target_planid": None, "gap_label": "基础设施部署缺口"},
    ]

    def build_coverage(current, _question, _ctx):
        covered = {claim.text for claim in current.claims}
        remaining = [action for action in actions
                     if action["requirement_id"] not in {
                         "industry" if any("工业部署" in text for text in covered) else "",
                         "agriculture" if any("农业部署" in text for text in covered) else "",
                         "services" if any("服务业部署" in text for text in covered) else "",
                             "science" if any("科技部署" in text for text in covered) else "",
                             "infrastructure" if any("基础设施部署" in text for text in covered) else "",
                     }]
        return {"status": "complete" if not remaining else "partial",
                "gaps": [item["gap_label"] for item in remaining],
                "next_actions": remaining}

    monkeypatch.setattr(supplement, "build_coverage", build_coverage, raising=False)
    batches, responses = [], [
        _part_many([("工业部署是六大支柱", "chunk-industry"),
                    ("农业部署包括特色种植", "chunk-agriculture"),
                    ("服务业部署包括现代物流", "chunk-services"),
                    ("科技部署包括平台建设", "chunk-science")]),
        _part("基础设施部署包括交通建设", "chunk-infrastructure"),
    ]

    async def controlled(_task, _settings, _ctx, _rt, _deadline, *, focus=None,
                         focuses=None, **_kwargs):
        batches.append(list(focuses or ([focus] if focus else [])))
        part = responses[len(batches) - 1]
        _rt.meta["qa_supplement_retrieval"] = {
            "read_chunks": [ref.chunk_id for ref in part.evidence_refs],
        }
        return ExecOutcome(str(len(batches)), None)

    async def forbidden_legacy(*_args, **_kwargs):
        raise AssertionError("controlled QA must handle bounded supplemental retrieval")

    async def recover(outcome, *_args, **_kwargs):
        return outcome, None

    async def noop(*_args, **_kwargs):
        return None

    def make_result(_task, outcome, _ctx, _meta):
        return responses[int(outcome.final_output) - 1]

    monkeypatch.setattr(supplement, "execute_controlled", controlled)
    monkeypatch.setattr(supplement, "build_result", make_result)
    monkeypatch.setattr(supplement, "verify_quotes", noop)
    monkeypatch.setattr(supplement, "verify_numbers", noop)
    monkeypatch.setattr(supplement, "enforce_literal_result", lambda *_args: None)
    monkeypatch.setattr(supplement, "finalize_controlled_facts", lambda *_args: None)

    task = SimpleNamespace(
        task_type="qa", question=question,
        model_copy=lambda update: SimpleNamespace(task_type="qa", question=update["question"]),
    )
    ctx = SimpleNamespace(route=SimpleNamespace(kind="open"), scope=None,
                          tool_calls=[], evidence=SimpleNamespace(ids=lambda: []))
    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0),
                         meta={"qa_execution_mode": "bounded_retrieval"})
    settings = SimpleNamespace(kb_backend="rag", max_turns=20, max_input_chars=4000,
                               max_tool_calls=20)

    await supplement.supplement_one(
        result, task, settings, ctx, rt, time.monotonic() + 170,
        forbidden_legacy, recover,
    )

    assert batches == [["产业部署", "农业部署", "服务业部署", "科技部署"],
                       ["基础设施部署"]], (batches, result.meta)
    assert [claim.text for claim in result.claims] == [
        "工业部署是六大支柱", "农业部署包括特色种植", "服务业部署包括现代物流",
        "科技部署包括平台建设", "基础设施部署包括交通建设",
    ]
    assert result.meta["research_supplement"]["stop_reason"] == "coverage_complete", result.meta["research_supplement"]["attempts"][0]
    assert result.meta["research_supplement"]["iterations"] == 2


@pytest.mark.asyncio
async def test_supplement_can_recheck_same_read_chunk_for_a_new_requirement(monkeypatch):
    initial = _part("新能源装备产业有32家企业", "chunk-metrics")
    initial.meta = {"quote_verification": {"approved": 1, "unverified": 0}}
    result = SimpleNamespace(
        status="completed", error=None, task_type="qa", claims=list(initial.claims),
        evidence_refs=list(initial.evidence_refs),
        meta={"evidence_coverage": {"status": "partial"}},
        _qa_report_supplied=False, _qa_report_candidates=[], _qa_report_dimensions=[],
        answer="",
    )
    question = "关于新能源装备产业的企业数量和年销售收入目标分别是多少？"
    calls = []

    def build_coverage(current, _question, _ctx):
        missing = not any("年营业收入" in claim.text for claim in current.claims)
        action = {"requirement_id": "revenue", "focus": "新能源装备产业年销售收入目标",
                  "query": "新能源装备产业年销售收入目标", "target_planid": "2561",
                  "gap_label": "年销售收入缺口"}
        return {"status": "partial" if missing else "complete",
                "gaps": [action["gap_label"]] if missing else [],
                "next_actions": [action] if missing else []}

    monkeypatch.setattr(supplement, "build_coverage", build_coverage, raising=False)

    async def controlled(_task, _settings, _ctx, _rt, _deadline, *, focus=None,
                         focuses=None, exclude_chunks=None, **_kwargs):
        calls.append((list(focuses or ([focus] if focus else [])), exclude_chunks))
        _rt.meta["qa_supplement_retrieval"] = {"read_chunks": ["chunk-metrics"]}
        return ExecOutcome("supplement", None)

    async def recover(outcome, *_args, **_kwargs):
        return outcome, None

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(supplement, "execute_controlled", controlled)
    monkeypatch.setattr(supplement, "build_result", lambda *_args: _part(
        "新能源装备产业年营业收入达到20亿元", "chunk-metrics"))
    monkeypatch.setattr(supplement, "verify_quotes", noop)
    monkeypatch.setattr(supplement, "verify_numbers", noop)
    monkeypatch.setattr(supplement, "enforce_literal_result", lambda *_args: None)
    monkeypatch.setattr(supplement, "finalize_controlled_facts", lambda *_args: None)

    task = SimpleNamespace(
        task_type="qa", question=question,
        model_copy=lambda update: SimpleNamespace(task_type="qa", question=update["question"]),
    )
    ctx = SimpleNamespace(route=SimpleNamespace(kind="open"), scope=None,
                          tool_calls=[], evidence=SimpleNamespace(ids=lambda: ["chunk-metrics"]))
    rt = SimpleNamespace(hooks=SimpleNamespace(turns=0),
                         meta={"qa_execution_mode": "bounded_retrieval"})
    settings = SimpleNamespace(kb_backend="rag", max_turns=20, max_input_chars=4000,
                               max_tool_calls=20)

    await supplement.supplement_one(
        result, task, settings, ctx, rt, time.monotonic() + 170,
        lambda *_args: None, recover,
    )

    assert len(calls) == 1
    assert calls[0][0] == ["新能源装备产业年销售收入目标"]
    assert calls[0][1] in (None, frozenset())
    assert "新能源装备产业年营业收入达到20亿元" in [claim.text for claim in result.claims]
    assert result.meta["research_supplement"]["stop_reason"] == "coverage_complete", result.meta["research_supplement"]["attempts"]


@pytest.mark.asyncio
async def test_supplement_35_second_caller_reserve_keeps_real_40_second_callee_eligible(monkeypatch):
    task = SimpleNamespace(
        task_type="qa", question="该规划年销售收入目标是多少？",
        model_copy=lambda update: SimpleNamespace(
            task_type="qa", question=update["question"]),
    )
    result = SimpleNamespace(
        status="completed", error=None, task_type="qa", claims=[], evidence_refs=[],
        meta={"quote_verification": {"approved": 1, "unverified": 0}},
        _qa_report_supplied=False, _qa_report_candidates=[], _qa_report_dimensions=[],
        answer="",
    )
    ctx = SimpleNamespace(route=SimpleNamespace(kind="open"), scope=None,
                          tool_calls=[], evidence=SimpleNamespace(ids=lambda: []))
    evidence = [{"chunk_id": "c-revenue", "planid": "2561", "title": "某地规划",
                 "section_path": "产业发展", "text": "年销售收入达到20亿元。"}]
    async def acquire(*_args, **_kwargs):
        return evidence

    monkeypatch.setattr(controlled_qa, "acquire", acquire)
    request_timeouts = []

    async def create(**kwargs):
        request_timeouts.append(kwargs["timeout"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "answer": "年销售收入达到20亿元。",
                "claims": [{"text": "年销售收入达到20亿元。",
                           "chunk_ids": ["c-revenue"],
                           "quotes": ["年销售收入达到20亿元。"], "anchors": []}],
                "insufficient": False,
            }, ensure_ascii=False)), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=5),
        )

    rt = SimpleNamespace(
        hooks=SimpleNamespace(turns=0),
        meta={"qa_execution_mode": "bounded_retrieval"},
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        log=lambda *_args, **_kwargs: None,
    )
    settings = SimpleNamespace(
        kb_backend="rag", max_turns=8, max_input_chars=4000,
        max_tool_calls=20, llm_request_timeout=60, llm_model="test",
        llm_max_output_tokens=2200, llm_temperature=None, llm_disable_thinking=True,
    )
    rt.settings = settings
    def build_coverage(current, _question, _ctx):
        if current.claims:
            return {"status": "complete", "gaps": [], "next_actions": []}
        action = {"requirement_id": "revenue", "kind": "metric",
                  "focus": "年销售收入目标", "query": "年销售收入目标",
                  "target_planid": None, "gap_label": "年销售收入", "retrievable": True}
        return {"status": "insufficient", "gaps": ["年销售收入"],
                "next_actions": [action]}

    monkeypatch.setattr(supplement, "build_coverage", build_coverage)
    async def noop_support(*_args, **_kwargs):
        return None

    async def recover(outcome, *_args, **_kwargs):
        return outcome, None

    monkeypatch.setattr("guance_agent.qa_report_verification.review_requirement_support",
                        noop_support)
    monkeypatch.setattr(supplement, "build_result",
                        lambda *_args: _part("年销售收入达到20亿元。", "c-revenue"))

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(supplement, "verify_quotes", noop)
    monkeypatch.setattr(supplement, "verify_numbers", noop)
    monkeypatch.setattr(supplement, "enforce_literal_result", lambda *_args: None)
    monkeypatch.setattr(supplement, "finalize_controlled_facts", lambda *_args: None)

    await supplement.supplement_one(
        result, task, settings, ctx, rt, time.monotonic() + 76,
        lambda *_args, **_kwargs: None, recover,
    )

    assert request_timeouts, result.meta["research_supplement"]["attempts"]
    assert request_timeouts[0] >= 40
    assert result.meta["research_supplement"]["stop_reason"] == "coverage_complete", result.meta["research_supplement"]["attempts"][0].get("failure_detail")
