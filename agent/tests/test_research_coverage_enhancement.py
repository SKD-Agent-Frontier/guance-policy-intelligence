from types import SimpleNamespace

from guance_agent.evidence_coverage import build_coverage
from guance_agent.research_core import ResearchState


def _run():
    return SimpleNamespace(
        route=SimpleNamespace(canonical_areas=(), areas=(), fyps=(), planids=()),
        tool_calls=[], scope=None,
    )


def test_research_core_freezes_planner_evidence_perspectives_for_comparison():
    state = ResearchState.create(
        _run(), "比较两地人工智能产业政策的主要做法和实施约束",
        deadline=10_000,
        planned_requirements=[
            {"id": "planned:goals", "kind": "dimension", "label": "政策目标",
             "phase": "evidence", "actionable": True},
            {"id": "planned:tools", "kind": "dimension", "label": "支持工具",
             "phase": "evidence", "actionable": True},
            {"id": "planned:synthesis", "kind": "analysis", "label": "比较实施差异",
             "phase": "synthesis", "actionable": False},
            {"id": "planned:ignored", "kind": "dimension", "label": "不可检索的预设项",
             "phase": "evidence", "actionable": False},
        ],
    )

    frozen = state.freeze_requirements(["人工智能产业政策的研究要求"])

    labels = [item["label"] for item in frozen["requirements"]]
    assert "政策目标" in labels
    assert "支持工具" in labels
    assert "比较实施差异" not in labels
    assert "不可检索的预设项" not in labels
    assert len(labels) == len(set(labels))
    assert {item["label"] for item in state.planned_requirements} == {"政策目标", "支持工具"}


def test_simple_fact_lookup_does_not_inherit_planner_dimensions():
    state = ResearchState.create(
        _run(), "某政策提出的产值目标是多少？",
        deadline=10_000,
        planned_requirements=[
            {"id": "planned:target", "kind": "metric", "label": "产值目标",
             "phase": "evidence", "actionable": True},
            {"id": "planned:extra", "kind": "dimension", "label": "政策优势",
             "phase": "evidence", "actionable": True},
        ],
    )

    frozen = state.freeze_requirements(["产值目标"])

    labels = [item["label"] for item in frozen["requirements"]]
    assert "产值目标" in labels
    assert "政策优势" not in labels
    assert state.planned_requirements == []


def test_uncovered_planner_perspective_remains_a_bounded_retrieval_gap():
    ref = SimpleNamespace(
        chunk_id="chunk-1", planid="policy-1", title="数字治理规划",
        area="某地", fyp="第十四个五年规划", source="kb_read",
        section_path="实施范围",
    )
    claim = SimpleNamespace(
        text="适用范围覆盖各级公共服务部门",
        chunk_ids=["chunk-1"], quotes=["适用范围覆盖各级公共服务部门"],
    )
    requirements = [
        {"id": "planner:scope", "kind": "dimension", "label": "适用范围",
         "phase": "evidence", "actionable": True},
        {"id": "planner:execution", "kind": "dimension", "label": "执行机制",
         "phase": "evidence", "actionable": True},
    ]
    result = SimpleNamespace(
        claims=[claim], evidence_refs=[ref], answer="",
        meta={"quote_verification": {"unverified": 0},
              "research_requirements": requirements},
    )
    registry = SimpleNamespace(ids=lambda: ["chunk-1"], get=lambda _chunk_id: ref)
    ctx = SimpleNamespace(evidence=registry, scope=None,
                          route=SimpleNamespace(kind="open", planids=()))

    coverage = build_coverage(result, "请综合研究数字治理政策的适用范围与执行机制", ctx)

    execution = next(item for item in coverage["requirement_coverage"]
                     if item["label"] == "执行机制")
    assert execution["status"] == "missing"
    assert execution["phase"] == "evidence"
    assert any(action["requirement_id"] == execution["id"]
               and action["query"] == "执行机制"
               for action in coverage["next_actions"])
