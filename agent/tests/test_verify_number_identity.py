"""Regression coverage for policy identity numbers vs evidence-backed numeric facts.

Pure unit tests exercise verify_numbers directly; no RAG fixture or live model is needed.
"""
from types import SimpleNamespace

import pytest

from guance_agent.schemas import Claim
from guance_agent.verify import verify_numbers


def _case(
    *,
    answer: str,
    claim: str = "推动养老服务人才队伍建设",
    source: str = "推动养老服务人才队伍建设。",
    scope: tuple[str, ...] = (),
    route_ids: tuple[str, ...] = (),
    evidence_ids: tuple[str, ...] = ("20699",),
    register_evidence: bool = True,
):
    chunk_id = "chunk-for-20699"

    def get_source(actual_id, *, document_id=None, generation_id=None):
        assert actual_id == chunk_id
        return SimpleNamespace(content=source, chunk_char_start=0)

    registered = SimpleNamespace(
        chunk_id=chunk_id,
        document_id="doc",
        generation_id="v1",
        observed_ranges=[(0, len(source))],
    )
    ctx = SimpleNamespace(
        question="提取养老服务方面的政策要素",
        scope=scope,
        route=SimpleNamespace(planids=route_ids),
        evidence={chunk_id: registered} if register_evidence else {},
        kb=SimpleNamespace(get_chunk_source=get_source),
        settings=SimpleNamespace(tool_timeout=5),
    )
    result = SimpleNamespace(
        claims=[Claim(text=claim, chunk_ids=[chunk_id])],
        answer=answer,
        evidence_refs=[SimpleNamespace(planid=pid) for pid in evidence_ids],
        meta={},
        warnings=[],
        evidence_status="verified",
    )
    return result, ctx


@pytest.fixture(autouse=True)
def no_answer_rebuild(monkeypatch):
    # This suite targets numeric rejection, not the answer reconstruction formatter.
    monkeypatch.setattr(
        "guance_agent.verify.rebuild_trusted_answer",
        lambda result, ctx, reason: None,
    )


@pytest.mark.asyncio
async def test_target_planid_is_not_orphan_when_absent_from_question():
    result, ctx = _case(answer="20699号政策：推动养老服务人才队伍建设。", scope=("20699",))
    assert await verify_numbers(result, ctx) == 0
    assert len(result.claims) == 1
    assert result.evidence_status == "verified"


@pytest.mark.asyncio
async def test_multiple_scope_planids_are_checked_separately():
    result, ctx = _case(
        answer="对20699和37982号政策进行提取：推动养老服务人才队伍建设。",
        scope=("20699", "37982"),
        evidence_ids=(),
    )
    assert await verify_numbers(result, ctx) == 0
    assert len(result.claims) == 1


@pytest.mark.asyncio
async def test_multiple_route_planids_are_checked_separately():
    result, ctx = _case(
        answer="政策20699、37982：推动养老服务人才队伍建设。",
        route_ids=("20699", "37982"),
        evidence_ids=(),
    )
    assert await verify_numbers(result, ctx) == 0
    assert len(result.claims) == 1


@pytest.mark.asyncio
async def test_registered_evidence_planid_is_not_orphan():
    result, ctx = _case(
        answer="20699号政策：推动养老服务人才队伍建设。",
        scope=(),
    )
    assert await verify_numbers(result, ctx) == 0
    assert len(result.claims) == 1


@pytest.mark.asyncio
async def test_unrelated_number_in_answer_keeps_independently_verified_claims():
    # 答案级未绑定数字只说明答案里有无法追溯的表述：重建答案并降级，
    # 但不得据此删除逐条通过原文核验的结论（单条辅助数字不扩大打击面）。
    result, ctx = _case(
        answer="20699号政策提供9999万元支持人才培养。",
        scope=("20699",),
    )
    assert await verify_numbers(result, ctx) > 0
    assert len(result.claims) == 1
    assert result.evidence_status == "insufficient"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "source"),
    [
        ("提供500万元补贴", "提供100万元补贴。"),
        ("补助比例为20%", "补助比例为10%。"),
        ("支持期限为5年", "支持期限为3年。"),
        ("2026年开始补助", "2025年开始补助。"),
        ("计划培养300人", "计划培养100人。"),
    ],
)
async def test_numeric_fact_must_appear_in_its_own_evidence(claim, source):
    result, ctx = _case(
        answer=f"20699号政策：{claim}",
        claim=claim,
        source=source,
        scope=("20699",),
    )
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []
    assert result.evidence_status == "insufficient"


@pytest.mark.asyncio
async def test_missing_registered_evidence_still_rejects_numeric_fact():
    result, ctx = _case(
        answer="20699号政策提供100万元补贴。",
        claim="提供100万元补贴",
        source="提供100万元补贴。",
        scope=("20699",),
        register_evidence=False,
    )
    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []
    assert result.evidence_status == "insufficient"


@pytest.mark.asyncio
async def test_evidence_backed_numeric_fact_survives():
    result, ctx = _case(
        answer="20699号政策提供100万元补贴。",
        claim="提供100万元补贴",
        source="提供100万元补贴。",
        scope=("20699",),
    )
    assert await verify_numbers(result, ctx) == 0
    assert len(result.claims) == 1
    assert result.evidence_status == "verified"
