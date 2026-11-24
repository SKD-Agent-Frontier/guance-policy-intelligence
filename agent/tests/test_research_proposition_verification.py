import json

import pytest

from guance_agent.context import RunContext
from guance_agent.kb import get_kb
from guance_agent.output import build_result
from guance_agent.qa_report_verification import finalize_report
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import AgentOutput, Claim, QABlock, QAReportDraft


class FakeRuntime:
    def __init__(self, settings, response):
        self.settings = settings
        self.hooks = type("Hooks", (), {"turns": 1})()
        self.response = response
        self.calls = []

    async def complete_text(self, messages, *, max_tokens, timeout, purpose="text_completion"):
        self.calls.append((messages, max_tokens, timeout, purpose))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _analysis_case(env, text="两项安排侧重不同。政策覆盖范围进一步扩大。"):
    settings, task = env
    ctx = RunContext(settings, get_kb(settings), task.run_id, task.session_id)
    ctx.evidence.add_result(ctx.kb.search("农业", "甲", None, 1), "kb_search")
    ref = ctx.evidence.get(ctx.evidence.ids()[0])
    claims = [
        Claim(text="甲市支持农业生产。", chunk_ids=[ref.chunk_id],
              quotes=["支持农业生产"]),
        Claim(text="乙市支持农产品加工。", chunk_ids=[ref.chunk_id],
              quotes=["支持农产品加工"]),
    ]
    draft = QAReportDraft(dimensions=["综合判断"], blocks=[
        QABlock(id="analysis-main", title_path=["综合判断"],
                dimension="综合判断", kind="analysis", text=text,
                supporting_claim_indexes=[0, 1]),
    ])
    result = build_result(env[1], ExecOutcome(AgentOutput(
        answer="不得发布的原始草稿", claims=claims, report=draft), None), ctx, {})
    result.meta["quote_verification"] = {
        "checked": 2, "approved": 2, "unverified": 0,
    }
    return result, ctx


@pytest.mark.asyncio
async def test_analysis_propositions_are_reviewed_in_one_batch_and_partially_retained(env):
    result, ctx = _analysis_case(env)
    runtime = FakeRuntime(env[0], json.dumps({
        "facts": [{"index": 0, "supported": True}, {"index": 1, "supported": True}],
        "blocks": [
            {"id": "analysis-main::proposition-0", "supported": False},
            {"id": "analysis-main::proposition-1", "supported": True},
        ],
    }, ensure_ascii=False))

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert len(runtime.calls) == 1
    payload = runtime.calls[0][0][1]["content"]
    assert "两项安排侧重不同。" in payload
    assert "政策覆盖范围进一步扩大。" in payload
    blocks = result.structured["blocks"]
    assert [block["id"] for block in blocks] == ["analysis-main"]
    assert blocks[0]["text"] == "政策覆盖范围进一步扩大。"
    assert "两项安排侧重不同。" not in result.answer
    assert result.meta["qa_report"]["propositions_reviewed"] == 2
    assert result.meta["qa_report"]["propositions_withdrawn"] == 1


@pytest.mark.asyncio
async def test_semantic_review_failure_does_not_publish_analysis_propositions(env):
    result, ctx = _analysis_case(env)
    runtime = FakeRuntime(env[0], TimeoutError())

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert len(runtime.calls) == 1
    assert not result.structured["blocks"]
    assert "两项安排侧重不同。" not in result.answer
    assert "政策覆盖范围进一步扩大。" not in result.answer
    assert "不得发布的原始草稿" not in result.answer
    assert result.meta["qa_report"]["semantic_review"] == "timeout"


@pytest.mark.asyncio
async def test_mixed_comma_linked_assertions_fail_closed_as_one_proposition(env):
    text = "甲市支持农业生产，乙市已实现产业升级。"
    result, ctx = _analysis_case(env, text)
    runtime = FakeRuntime(env[0], json.dumps({
        "facts": [{"index": 0, "supported": True}, {"index": 1, "supported": True}],
        "blocks": [{"id": "analysis-main", "supported": False}],
    }, ensure_ascii=False))

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert len(runtime.calls) == 1
    payload = runtime.calls[0][0][1]["content"]
    assert text in payload
    assert not result.structured["blocks"]
    assert text not in result.answer
    assert result.meta["qa_report"]["propositions_withdrawn"] == 1


@pytest.mark.parametrize("text,expected", [
    ("命题甲。命题乙！", ["命题甲。", "命题乙！"]),
    ("地区不同，规划期延续；目标仍需核验。", ["地区不同，规划期延续；", "目标仍需核验。"]),
])
def test_analysis_proposition_split_preserves_source_punctuation(text, expected):
    from guance_agent.qa_report_verification import _analysis_propositions

    assert _analysis_propositions(text) == expected


@pytest.mark.asyncio
async def test_local_number_rejection_only_withdraws_unsupported_sentence(env):
    result, ctx = _analysis_case(env, "两项安排侧重不同。政策另新增1000亿元资金。")
    runtime = FakeRuntime(env[0], json.dumps({
        "facts": [{"index": 0, "supported": True}, {"index": 1, "supported": True}],
        "blocks": [{"id": "analysis-main::proposition-0", "supported": True}],
    }, ensure_ascii=False))

    await finalize_report(result, ctx, runtime, deadline=10**12)

    assert len(runtime.calls) == 1
    assert result.structured["blocks"][0]["text"] == "两项安排侧重不同。"
    assert "1000亿元" not in result.answer
    assert result.meta["qa_report"]["propositions_locally_withdrawn"] == 1
