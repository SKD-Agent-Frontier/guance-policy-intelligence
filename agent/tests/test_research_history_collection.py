from guance_agent.research_history import (
    collect_scoped_history,
    collect_scoped_requirements,
    history_summary_matches_scope,
    requested_history_turn_limit,
    scoped_turn_window,
)


def _turn(planid, text, status="continued"):
    return {"question": text, "resolution_status": status,
            "documents": [{"planid": planid}],
            "verified_facts": [{"text": text, "quotes": [text], "chunk_ids": [text]}],
            "evidence_refs": [{"chunk_id": text, "planid": planid}],
            "quote_verification": {"approved": 1, "unverified": 0}}


def test_collects_verified_facts_across_a_failed_continuation():
    turns = [_turn("p", "基础"), _turn("p", "指标"),
             {"resolution_status": "continued", "context_documents": [{"planid": "p"}]}]
    combined = collect_scoped_history(turns, {"p"})
    assert {fact["text"] for fact in combined["verified_facts"]} == {"基础", "指标"}


def test_collection_never_crosses_a_scope_replacement():
    turns = [_turn("p", "更早旧文献"), _turn("q", "新范围", "scope_changed")]
    assert collect_scoped_history(turns, {"p"}) is None


def test_collection_excludes_unrelated_documents():
    combined = collect_scoped_history([_turn("q", "无关"), _turn("p", "目标")], {"p"})
    assert [item["text"] for item in combined["verified_facts"]] == ["目标"]


def test_multi_turn_summary_collects_frozen_requirements_within_requested_window():
    turns = []
    for index, requirement in enumerate(("industrial", "agricultural", "services")):
        turn = _turn("p", str(index))
        turn["user_scope"] = {
            "areas": ["临朐县"], "periods": ["第十一个五年规划"],
            "requirements": [{"id": requirement, "label": requirement}],
        }
        turns.append(turn)
    combined = collect_scoped_history(turns, {"p"}, max_turns=3)
    assert {item["id"] for item in combined["requirements"]} == {
        "industrial", "agricultural", "services"
    }
    assert len(combined["verified_facts"]) == 3


def test_requirement_collection_stops_at_scope_replacement_and_incompatible_scope():
    old = _turn("p", "旧问题")
    old["user_scope"] = {"areas": ["临朐县"], "requirements": [{"id": "old"}]}
    new = _turn("q", "新问题", "scope_changed")
    new["user_scope"] = {"areas": ["衡水市"], "requirements": [{"id": "new"}]}
    assert [item["id"] for item in collect_scoped_requirements(
        [old, new], max_turns=3, areas={"衡水市"})] == ["new"]
    assert [item["id"] for item in collect_scoped_requirements(
        [old, new], max_turns=3)] == ["new"]


def test_requirement_collection_does_not_merge_different_documents_in_same_area_period():
    old = _turn("doc-a", "旧规划")
    old["documents"][0]["title"] = "衡水市十三五规划"
    old["user_scope"] = {
        "areas": ["衡水市"], "periods": ["第十三个五年规划"],
        "requirements": [{"id": "requirement-a"}],
    }
    current = _turn("doc-b", "新规划")
    current["documents"][0]["title"] = "衡水市另一专项规划"
    current["user_scope"] = {
        "areas": ["衡水市"], "periods": ["第十三个五年规划"],
        "requirements": [{"id": "requirement-b"}],
    }
    assert [item["id"] for item in collect_scoped_requirements(
        [old, current], max_turns=3)] == ["requirement-b"]


def test_history_window_parses_numeric_and_vague_multi_turn_requests():
    assert requested_history_turn_limit("请将前三轮的内容整理成摘要") == 3
    assert requested_history_turn_limit("请将前几轮的内容整理成摘要") == 3
    assert requested_history_turn_limit("请结合前面讨论整理成报告") == 3
    assert requested_history_turn_limit("请把上述内容归纳成摘要") == 3
    assert requested_history_turn_limit("请汇总前12轮") == 8
    assert requested_history_turn_limit("请总结这份规划") is None


def test_summary_of_original_scope_survives_a_later_comparison_expansion():
    prior = {
        "user_scope": {
            "areas": ["衡水市", "河北省"],
            "periods": ["第十三个五年规划"],
        },
        "documents": [
            {"planid": "1116", "area": "衡水市", "fyp": "第十三个五年规划"},
            {"planid": "2210", "area": "河北省", "fyp": "第十三个五年规划"},
        ],
    }

    assert history_summary_matches_scope(
        "请将前三轮讨论内容整理成摘要，突出衡水市十三五发展目标", prior)
    assert not history_summary_matches_scope(
        "改为只看石家庄市十三五规划，整理前三轮内容", prior)
    assert not history_summary_matches_scope(
        "请将前三轮整理成承德市十三五摘要", prior)


def test_bounded_history_summary_keeps_target_requirements_across_comparison_expansion():
    turns = []
    for index, (areas, planids, resolution) in enumerate((
            (["衡水市"], ["1116"], "no_prior"),
            (["衡水市"], ["1116"], "continued"),
            (["衡水市", "河北省"], ["1116", "2210"], "extended"))):
        turn = _turn(planids[0], f"turn-{index}", resolution)
        turn["documents"] = [{"planid": planid, "area": area,
                              "fyp": "第十三个五年规划"}
                             for planid, area in zip(planids, areas)]
        turn["evidence_refs"] = [
            {"chunk_id": f"turn-{index}-{planid}", "planid": planid}
            for planid in planids]
        turn["verified_facts"] = [
            {"text": f"fact-{index}-{planid}", "quotes": [f"fact-{index}-{planid}"],
             "chunk_ids": [f"turn-{index}-{planid}"]}
            for planid in planids]
        turn["user_scope"] = {
            "areas": areas, "periods": ["第十三个五年规划"],
            "requirements": [{"id": f"requirement-{index}"}],
        }
        turns.append(turn)

    window = scoped_turn_window(
        turns, max_turns=3, areas={"衡水市"},
        periods={"第十三个五年规划"})
    requirements = collect_scoped_requirements(
        window, max_turns=3, areas={"衡水市"},
        periods={"第十三个五年规划"})
    history = collect_scoped_history(window, {"1116", "2210"}, max_turns=3)

    assert [turn["question"] for turn in window] == ["turn-0", "turn-1", "turn-2"]
    assert {unit["id"] for unit in requirements} == {
        "requirement-0", "requirement-1", "requirement-2",
    }
    assert {ref["planid"] for ref in history["evidence_refs"]} == {"1116", "2210"}
