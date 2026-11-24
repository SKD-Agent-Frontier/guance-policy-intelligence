from types import SimpleNamespace

import pytest

from guance_agent.schemas import Claim
from guance_agent.verify import is_number_verified_claim, verify_numbers


def _case(*, claim_text="提供500万元补贴", source="提供500万元补贴。"):
    chunk_id = "C1"
    ref = SimpleNamespace(
        chunk_id=chunk_id,
        planid="P1",
        title="政策一",
        area="甲市",
        fyp="十四五",
        section_path="第一章",
        source="kb_read",
        document_id="D1",
        generation_id="G1",
        char_start=0,
        char_end=len(source),
        observed_ranges=[(0, len(source))],
        complete=True,
        out_of_section=False,
    )
    claim = Claim(text=claim_text, chunk_ids=[chunk_id])

    def get_source(actual_id, *, document_id=None, generation_id=None):
        assert actual_id == chunk_id
        assert document_id == "D1"
        assert generation_id == "G1"
        return SimpleNamespace(content=source, chunk_char_start=0)

    ctx = SimpleNamespace(
        question="提取政策要素",
        scope=("P1",),
        route=SimpleNamespace(planids=()),
        evidence={chunk_id: ref},
        kb=SimpleNamespace(get_chunk_source=get_source),
        settings=SimpleNamespace(tool_timeout=5),
    )
    result = SimpleNamespace(
        claims=[claim],
        answer=claim_text,
        evidence_refs=[ref],
        meta={},
        warnings=[],
        evidence_status="verified",
    )
    return result, ctx, claim, ref


@pytest.fixture(autouse=True)
def no_answer_rebuild(monkeypatch):
    monkeypatch.setattr(
        "guance_agent.verify.rebuild_trusted_answer",
        lambda result, ctx, reason: None,
    )


@pytest.mark.asyncio
async def test_verified_numeric_claim_receives_receipt():
    result, ctx, claim, _ = _case()

    assert not is_number_verified_claim(result, claim)
    assert await verify_numbers(result, ctx) == 0
    assert is_number_verified_claim(result, claim)


@pytest.mark.asyncio
async def test_numeric_claim_not_present_in_own_source_is_not_authorized():
    result, ctx, claim, _ = _case(source="提供100万元补贴。")

    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []
    assert not is_number_verified_claim(result, claim)


@pytest.mark.asyncio
async def test_claim_mutation_invalidates_number_receipt():
    result, ctx, claim, _ = _case()

    assert await verify_numbers(result, ctx) == 0
    assert is_number_verified_claim(result, claim)
    claim.text = "提供600万元补贴"
    assert not is_number_verified_claim(result, claim)


@pytest.mark.asyncio
async def test_citation_identity_mutation_invalidates_number_receipt():
    result, ctx, claim, ref = _case()

    assert await verify_numbers(result, ctx) == 0
    assert is_number_verified_claim(result, claim)
    ref.title = "另一篇政策"
    assert not is_number_verified_claim(result, claim)


@pytest.mark.asyncio
async def test_claim_without_numbers_is_authorized_after_identity_check():
    result, ctx, claim, _ = _case(claim_text="政策提出完善服务机制")

    assert not is_number_verified_claim(result, claim)
    assert await verify_numbers(result, ctx) == 0
    assert is_number_verified_claim(result, claim)


@pytest.mark.asyncio
async def test_claim_with_unknown_title_is_not_authorized():
    result, ctx, claim, _ = _case(claim_text="《未登记篇名》提供500万元补贴")

    assert await verify_numbers(result, ctx) > 0
    assert result.claims == []
    assert not is_number_verified_claim(result, claim)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["补助为30.5亿元", "占比为30%"])
async def test_numeric_substring_cannot_authorize_a_different_value(source):
    result, ctx, claim, _ = _case(claim_text="补助30亿元", source=source)
    assert await verify_numbers(result, ctx) > 0
    assert not is_number_verified_claim(result, claim)


@pytest.mark.asyncio
async def test_version_and_run_identity_are_part_of_authorization():
    result, ctx, claim, ref = _case()
    result.run_id = "first"
    ref.document_version_id = "v1"
    assert await verify_numbers(result, ctx) == 0
    ref.document_version_id = "v2"
    assert not is_number_verified_claim(result, claim)
    ref.document_version_id = "v1"
    result.run_id = "second"
    assert not is_number_verified_claim(result, claim)
