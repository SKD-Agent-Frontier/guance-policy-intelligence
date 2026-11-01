"""QA multi-aspect report formatting must survive evidence filtering."""
from types import SimpleNamespace

from guance_agent.output import rebuild_trusted_answer
from guance_agent.qa_analysis import finalize_qa_analysis
from guance_agent.qa_report import is_canonical_outline, needs_outline, verified_qa_outline
from guance_agent.schemas import Claim, RunResult

QUESTION = "密云区十一五期间的发展机遇和面临的挑战分别是什么？"


def _claim(text: str, category: str, cid: str) -> Claim:
    return Claim(text=text, chunk_ids=[cid], quotes=[text], dimension=category)


FACTS = [
    _claim("规划指出区位条件带来发展空间。", "总体发展环境", "a"),
    _claim("城乡统筹带来政策机遇，支持产业布局。", "主要发展机遇", "b"),
    _claim("北京奥运会带来旅游产业市场机会。", "主要发展机遇", "c"),
    _claim("经济发展水平低，基础仍然薄弱。", "面临挑战", "d"),
    _claim("部分基础设施有待进一步完善。", "面临挑战", "e"),
]


def _result(claims=None) -> RunResult:
    return RunResult(
        run_id="run", session_id="session", status="completed",
        evidence_status="verified", answer="原模型未加分级的普通回答",
        claims=list(FACTS if claims is None else claims), evidence_refs=[],
        dropped_chunk_ids=[], warnings=[], error=None, meta={}, task_type="qa",
    )


def test_multi_aspect_report_is_total_detail_total_with_canonical_outline():
    answer, missing = verified_qa_outline(FACTS, QUESTION)
    assert not missing
    assert answer.startswith("根据本轮")
    assert "一、主要发展机遇" in answer
    assert "二、面临挑战" in answer
    assert "规划指出区位条件带来发展空间。" in answer.split("一、")[0]
    assert "（一）" in answer and "（二）" in answer
    assert answer.rstrip().endswith("穷尽。")
    assert answer.index("北京奥运会") < answer.index("二、面临挑战")


def test_verification_rebuild_preserves_sections_and_removes_invalid_claims():
    result = _result([FACTS[1], FACTS[3]])
    result.evidence_refs = [SimpleNamespace(chunk_id="b"), SimpleNamespace(chunk_id="d")]
    ctx = SimpleNamespace(question=QUESTION, tool_calls=[])
    rebuild_trusted_answer(result, ctx, "quote_verification")
    assert result.answer.startswith("根据本轮")
    assert "一、主要发展机遇" in result.answer
    assert "二、面临挑战" in result.answer
    assert "城乡统筹" in result.answer and "经济发展水平" in result.answer
    assert "北京奥运会" not in result.answer
    assert result.meta["answer_rebuilt"] == "quote_verification"
    assert result.meta["report_outline"]["coverage_missing"] == []


def test_missing_dimension_is_flagged_without_fabrication():
    result = _result([FACTS[1]])
    finalize_qa_analysis(result, QUESTION)
    assert result.evidence_status == "insufficient"
    assert "二、面临挑战" in result.answer
    assert "暂不能作出判断" in result.answer
    assert "经济发展水平低" not in result.answer
    assert "面临挑战" in result.meta["report_outline"]["coverage_missing"]


def test_single_fact_question_is_not_rewritten_as_a_report():
    result = _result([FACTS[1]])
    finalize_qa_analysis(result, "规划提到了哪些产业？")
    assert result.answer == "原模型未加分级的普通回答"
    assert not needs_outline("规划提到了哪些产业？")


def test_numbering_requires_parent_headings_and_sequential_levels():
    valid = ("概述。\n一、发展机遇\n（一）奥运因素\n1、旅游业\n"
             "（1）景区发展\n二、面临挑战\n（一）产业基础\n总结。")
    assert is_canonical_outline(valid)
    assert not is_canonical_outline("概述。\n一、发展机遇\n1、旅游业\n二、面临挑战")
    assert not is_canonical_outline("一、发展机遇\n（三）旅游业\n二、面临挑战")
    assert not is_canonical_outline("一、发展机遇\n- 旅游业\n二、面临挑战")


def test_noncanonical_model_answer_is_rebuilt_into_correct_sections():
    result = _result()
    result.answer = "一、机遇\n- 奥运会\n二、挑战\n1、经济薄弱"
    finalize_qa_analysis(result, QUESTION)
    assert is_canonical_outline(result.answer)
    assert "- 奥运会" not in result.answer
    assert result.meta["report_outline"]["source"] == "verified_claims"


def test_plain_answer_with_heading_numbers_does_not_trigger_unbound_number_check():
    from guance_agent.verify import _tokens
    narrative = ("一、发展机遇\n（一）交通改善\n1、城乡统筹\n"
                 "（1）政策支持\n2008年奥运会带来机遇。")
    assert _tokens(narrative) == ["2008"]
