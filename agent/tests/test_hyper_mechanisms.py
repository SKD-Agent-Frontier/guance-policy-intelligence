"""Offline mechanism tests; no real Qwen or RAG assertions."""
import json
from dataclasses import dataclass
from types import SimpleNamespace

from guance_agent.config import Settings
from guance_agent.harness_inspired import HyperMechanisms


@dataclass
class Requirement:
    id: str


@dataclass
class Block:
    id: str
    kind: str
    requirement_ids: list[str]
    evidence_ids: list[str]


def sample_state():
    return SimpleNamespace(
        requirements=[Requirement("q1"), Requirement("q2")],
        claim_requirement_ids=[["q1"]],
        claim_fact_ids=[["f1"]],
        claims=[object()],
        approved_blocks=[],
        draft_blocks=[],
        io_calls=0,
        model_calls=1,
        needs_analysis=False,
        remaining=lambda: {"search_read": 4},
    )


def test_config_hyper_opt_in_only():
    assert Settings().qa_engine == "controlled"
    assert Settings(qa_engine="hyper").qa_engine == "hyper"


def test_atomic_constraints_keep_all_qualifier(tmp_path):
    run = HyperMechanisms("run_a", "列出所有舆论相关政策；按地区整理", tmp_path)
    assert run.exhaustive_requested
    assert any("列出所有舆论相关政策" in x for x in run.atomic_items)
    assert "Top-K" in run.guidance()


def test_corpus_critic_one_retry(tmp_path):
    run = HyperMechanisms("run_b", "比较浙江和江苏的政策", tmp_path)
    state = sample_state()
    assert run.pre_draft_critic(state, finish_if_partial=False) == {"gaps": ["q2"]}
    assert run.pre_draft_critic(state, finish_if_partial=False) is None


def test_corpus_critic_respects_budget(tmp_path):
    run = HyperMechanisms("run_c", "分析政策", tmp_path)
    state = sample_state()
    state.remaining = lambda: {"search_read": 1}
    assert run.pre_draft_critic(state, finish_if_partial=False) is None


def test_critic_does_not_claim_unverified_exhaustive_coverage(tmp_path):
    run = HyperMechanisms("run_d", "列出所有舆论政策", tmp_path)
    state = sample_state()
    state.draft_blocks = [Block("a", "analysis", ["q1", "q2"], ["f1"])]
    verdict = run.adversarial_critic(state, {
        "passed": True, "gaps": [],
        "covered_requirement_ids": ["q1", "q2"],
        "approved_block_ids": ["a"],
    })
    assert verdict["passed"] is False
    assert "exhaustive_catalog_coverage_unverified" in verdict["gaps"]
    assert verdict["approved_block_ids"] == ["a"]


def test_patch_retains_bound_blocks_for_re_review(tmp_path):
    run = HyperMechanisms("run_e", "分析政策", tmp_path)
    state = sample_state()
    state.approved_blocks = [
        Block("old", "fact", ["q1"], ["f1"]),
        Block("invalid", "fact", ["q2"], ["ghost"]),
    ]
    def lookup(_state, block, rid=None):
        return [0] if block.id == "old" and rid in (None, "q1") else []
    support = {"q1": [0]}
    blocks = run.patch_with_approved(
        state, [Block("new", "fact", ["q1"], ["f1"])], support, lookup
    )
    assert [block.id for block in blocks] == ["new", "old"]
    assert support["q1"] == [0, 0]


def test_redacted_workspace(tmp_path):
    question = "所有私有政策问题：身份证号1234"
    run = HyperMechanisms("run_f", question, tmp_path)
    run.snapshot("initialized", sample_state())
    raw = (tmp_path / "run_f.json").read_text(encoding="utf-8")
    assert question not in raw
    assert "身份证号" not in raw
    assert json.loads(raw)["events"][0]["stage"] == "initialized"
