"""Whole-phrase matching: split-token candidates must never be promoted to QA evidence."""
from types import SimpleNamespace

import pytest

from guance_agent.literal_keywords import enforce_literal_result, literal_hit, literal_keyword
from guance_agent.schemas import Claim


@pytest.mark.parametrize(("request", "expected"), [
    ("数字经济相关政策", "数字经济"),
    ("数字经济的相关政策", "数字经济"),
    ("帮我查询数字经济相关政策；关注产业政策", "数字经济"),
    ("关于数字经济产业有哪些政策", "数字经济产业"),
    ("环保政策", "环保"),
    ("数字经济政策有哪些", "数字经济"),
    ("十四五期间如何推动数字化改造？", None),
    ("你好吗？", None),
    ("政策有哪些", None),
])
def test_full_keyword_is_extracted_without_splitting(request, expected):
    assert literal_keyword(request) == expected


def test_candidate_needs_contiguous_term():
    assert literal_hit(SimpleNamespace(title="数字经济产业发展规划", content="推动数字化升级", section_path=""), "数字经济")
    assert literal_hit(SimpleNamespace(title="十四五规划", content="促进数字经济化", section_path=""), "数字经济")
    assert not literal_hit(SimpleNamespace(title="经济发展规划", content="数字化和经济", section_path=""), "数字经济")
    assert not literal_hit(SimpleNamespace(title="数字化规划", content="数字和经济并列", section_path=""), "数字经济")


def _result():
    refs = [
        SimpleNamespace(chunk_id="split", title="产业数字化发展规划"),
        SimpleNamespace(chunk_id="strict", title="国家十四五纲要"),
        SimpleNamespace(chunk_id="title", title="数字经济产业规划"),
    ]
    claims = [
        Claim(text="产业数字化与经济增长并进", chunk_ids=["split"], quotes=["推动数字化转型"]),
        Claim(text="推动数字经济产业发展", chunk_ids=["strict"], quotes=["促进数字经济产业发展"]),
        Claim(text="支持产业生态建设", chunk_ids=["title"], quotes=["完善数据要素体系"]),
    ]
    return SimpleNamespace(
        status="completed", error=None, claims=claims, evidence_refs=refs,
        answer="模型原答案包含不匹配的产业数字化结论", evidence_status="verified",
        meta={}, warnings=[],
    )


def test_final_answer_uses_only_whole_phrase_supporting_evidence():
    result = _result()
    context = SimpleNamespace(literal_keyword="数字经济", literal_hit_ids={"strict"})
    enforce_literal_result(result, context)
    assert [claim.text for claim in result.claims] == [
        "推动数字经济产业发展", "支持产业生态建设",
    ]
    assert [ref.chunk_id for ref in result.evidence_refs] == ["strict", "title"]
    assert "产业数字化与经济增长并进" not in result.answer
    assert "推动数字经济产业发展" in result.answer
    assert result.evidence_status == "insufficient"
    assert result.meta["literal_keyword"]["removed_claims"] == 1
    assert result.meta["answer_rebuilt"] == "literal_keyword"


def test_filtered_search_hit_counts_even_if_quote_is_shorter():
    result = _result()
    result.claims = [Claim(text="符合检索主题", chunk_ids=["split"], quotes=["具体实施措施"])]
    result.evidence_refs = result.evidence_refs[:1]
    context = SimpleNamespace(literal_keyword="数字经济", literal_hit_ids={"split"})
    enforce_literal_result(result, context)
    assert len(result.claims) == 1
    assert result.evidence_status == "verified"


def test_all_split_matches_are_refused_without_claiming_policy_absent():
    result = _result()
    result.claims = result.claims[:1]
    result.evidence_refs = result.evidence_refs[:1]
    enforce_literal_result(result, SimpleNamespace(literal_keyword="数字经济", literal_hit_ids=set()))
    assert result.claims == []
    assert result.evidence_refs == []
    assert "没有获得可核验" in result.answer
    assert "不代表相关政策不存在" in result.answer
    assert result.evidence_status == "insufficient"


def test_ordinary_explanatory_qa_keeps_previous_behavior():
    result = _result()
    enforce_literal_result(result, SimpleNamespace(literal_keyword=None, literal_hit_ids=set()))
    assert len(result.claims) == 3
    assert result.answer == "模型原答案包含不匹配的产业数字化结论"
