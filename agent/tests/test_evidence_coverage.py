"""Round 3: evidence coverage is derived from verified claims, not reads."""
from types import SimpleNamespace

import pytest

from guance_agent.evidence_coverage import build_coverage, multi_document_required
from guance_agent.qa_controlled import messages_for
from guance_agent.research_quality import requirements
from guance_agent.research_requirements import claim_fingerprint, requirements_fingerprint
from guance_agent.runner import ExecOutcome
from guance_agent.schemas import Claim, TaskRequest


class Registry:
    def __init__(self, refs):
        self.refs = {ref.chunk_id: ref for ref in refs}

    def get(self, chunk_id):
        return self.refs.get(chunk_id)

    def ids(self):
        return list(self.refs)


def _ref(chunk_id, planid, title):
    return SimpleNamespace(chunk_id=chunk_id, planid=planid, title=title,
                           area="某地", fyp="第十四个五年规划", source="kb_read")


def _claim(chunk_id, text):
    return Claim(text=text, chunk_ids=[chunk_id], quotes=[text])


def _ctx(refs, scope=None, kind="open", planids=()):
    return SimpleNamespace(
        evidence=Registry(refs),
        scope=tuple(scope) if scope else None,
        route=SimpleNamespace(kind=kind, planids=tuple(planids)),
    )


def _result(claims, refs):
    return SimpleNamespace(
        claims=list(claims), evidence_refs=list(refs),
        meta={"quote_verification": {"unverified": 0}},
        task_type="qa",
    )


def test_two_documents_with_verified_claims_satisfy_multi_document_requirement():
    refs = [_ref("c1", "23132", "海西州规划"), _ref("c2", "45100", "双流区规划")]
    claims = [_claim("c1", "加快数字基础设施建设"),
              _claim("c2", "推进新型基础设施建设")]
    result = _result(claims, refs)
    ctx = _ctx(refs)
    coverage = build_coverage(result, "请归纳两篇政策的共同做法与差异", ctx)
    assert multi_document_required("请比较两篇政策的异同")
    assert coverage["status"] == "complete"
    assert coverage["multi_document_satisfied"] is True
    assert coverage["covered_planids"] == ["23132", "45100"]


def test_read_but_unused_document_does_not_count():
    refs = [_ref("c1", "23132", "海西州规划"), _ref("c2", "45100", "双流区规划")]
    claims = [_claim("c1", "加快数字基础设施建设")]
    result = _result(claims, refs)
    ctx = _ctx(refs)
    coverage = build_coverage(result, "请归纳两篇政策的共同做法与差异", ctx)
    assert coverage["covered_planids"] == ["23132"]
    assert "multiple_documents_required" in coverage["gaps"]
    assert coverage["next_action"]["target_planid"] == "45100"


def test_semantic_requirement_support_overrides_legacy_literal_dimension_gate():
    ref = _ref("c1", "23132", "海西州规划")
    result = _result([_claim("c1", "到2025年完成产业升级")], [ref])
    question = "海西州规划的政策目标是什么？"
    units = requirements(question)["units"]
    result.meta["research_requirements"] = units
    target_id = next(unit["id"] for unit in units if unit["kind"] == "dimension")
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(units),
        "supports": {target_id: [0]},
    }

    coverage = build_coverage(result, question, _ctx([ref]))

    assert coverage["status"] == "complete"
    assert "政策目标" in coverage["covered_dimensions"]
    assert "dimension_uncovered:政策目标" not in coverage["gaps"]


def test_explicit_two_documents_missing_one_is_not_complete():
    refs = [_ref("c1", "A", "A规划"), _ref("c2", "B", "B规划")]
    claims = [_claim("c1", "A政策提出产业扶持措施")]
    result = _result(claims, refs)
    ctx = _ctx(refs, kind="objects", planids=("A", "B"))
    coverage = build_coverage(result, "比较A、B两篇政策的产业扶持", ctx)
    assert coverage["status"] == "partial"
    assert coverage["missing_planids"] == ["B"]
    assert coverage["next_action"]["target_planid"] == "B"


def test_unrelated_document_cannot_replace_required_document():
    refs = [_ref("c1", "C", "C规划")]
    claims = [_claim("c1", "C政策提出产业扶持措施")]
    result = _result(claims, refs)
    ctx = _ctx(refs, kind="objects", planids=("A", "B"))
    coverage = build_coverage(result, "比较A、B两篇政策的产业扶持", ctx)
    assert coverage["covered_planids"] == ["C"]
    assert coverage["missing_planids"] == ["A", "B"]
    assert coverage["multi_document_satisfied"] is False


def test_missing_dimension_produces_concrete_next_action():
    refs = [_ref("c1", "A", "A规划")]
    claims = [_claim("c1", "A政策提出产业发展目标")]
    result = _result(claims, refs)
    ctx = _ctx(refs, scope=("A",), kind="objects", planids=("A",))
    coverage = build_coverage(result, "A政策的产业扶持措施有哪些？", ctx)
    assert "产业扶持" in coverage["missing_dimensions"] or "重点措施" in coverage["missing_dimensions"]
    assert coverage["next_action"]
    assert "A规划" in coverage["next_action"]["query"]


def test_comparison_action_targets_only_the_missing_side():
    question = "这些工业发展目标与农业领域的特色产业培育相比，支持方式有何不同？"
    ref = _ref("c1", "1831", "示例规划")
    claim = _claim("c1", "农业领域的特色产业培育采用标准化和产业化支持方式")
    result = _result([claim], [ref])
    units = requirements(question)["units"]
    result.meta["research_requirements"] = units
    relation = next(unit for unit in units if unit["kind"] == "comparison")
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(units),
        "supports": {relation["right_requirement_id"]: [0]},
    }

    coverage = build_coverage(result, question, _ctx([ref], scope=("1831",)))

    actions = coverage["next_actions"]
    assert any(action["query"] == "工业发展目标" for action in actions)
    assert not any("农业领域的特色产业培育" in action["query"] for action in actions)
    assert any(relation["id"] in gap for gap in coverage["gaps"])


def test_single_document_simple_question_does_not_require_two_documents():
    refs = [_ref("c1", "2561", "沧州市十五计划")]
    claims = [_claim("c1", "产业布局按照一点一带两横两纵展开")]
    result = _result(claims, refs)
    ctx = _ctx(refs, scope=("2561",), kind="objects", planids=("2561",))
    coverage = build_coverage(result, "沧州市十五计划产业布局是什么？", ctx)
    assert coverage["multi_document_required"] is False
    assert coverage["status"] == "complete"


def test_multi_document_generation_prompt_requires_two_sources():
    task = SimpleNamespace(question="请归纳两篇政策在基础设施方面的共同点与差异")
    messages = messages_for(task, [{"chunk_id": "c1", "planid": "A", "title": "A",
                                    "section_path": "s", "text": "原文"}])
    assert "至少为两篇不同 planid" in messages[1]["content"]


@pytest.mark.asyncio
async def test_supplement_is_scoped_to_missing_document(monkeypatch):
    from guance_agent.research_supplement import supplement_one

    refs = [_ref("c1", "A", "A规划"), _ref("c2", "B", "B规划")]
    claims = [_claim("c1", "A政策提出产业扶持措施")]
    result = _result(claims, refs)
    result.status = "completed"
    result.error = None
    result.meta["quote_verification"]["unverified"] = 0
    ctx = _ctx(refs, kind="open")
    coverage = build_coverage(result, "比较A、B两篇政策的产业扶持措施", ctx)
    result.meta["evidence_coverage"] = coverage
    result.meta["qa_execution_mode"] = "bounded_retrieval"
    assert coverage["next_action"]["target_planid"] == "B"

    seen = {}

    async def fake_execute(task, settings, context, runtime, deadline, **kwargs):
        seen["scope"] = context.scope
        seen["focus"] = kwargs.get("focus")
        return ExecOutcome(None, "RAG_TIMEOUT")

    monkeypatch.setattr("guance_agent.research_supplement.execute_controlled",
                        fake_execute)
    settings = SimpleNamespace(kb_backend="rag", max_turns=8, max_input_chars=4000)
    runtime = SimpleNamespace(meta={"qa_execution_mode": "bounded_retrieval"},
                              hooks=SimpleNamespace(turns=0), settings=settings)
    task = TaskRequest(run_id="r", session_id="s", created_at="2026-11-01T00:00:00Z",
                       question="比较A、B两篇政策的产业扶持措施")
    await supplement_one(result, task, settings, ctx, runtime,
                         __import__("time").monotonic() + 180,
                         None, None)
    assert seen["scope"] == ("B",)
    assert "B规划" in seen["focus"] or "产业扶持" in seen["focus"]
    assert ctx.scope is None  # restored after the bounded supplement
