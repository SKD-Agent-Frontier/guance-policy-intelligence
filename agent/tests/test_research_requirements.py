"""Requirement-to-evidence coverage regressions using only verified local facts."""
from types import SimpleNamespace

from guance_agent.evidence_coverage import multi_document_required
from guance_agent.research_quality import evaluate, requirements
from guance_agent.research_requirements import (
    catalog_facet_matches_claim,
    claim_fingerprint,
    claim_matches_requirement_source,
    comparison_sides,
    expand_outline_subheadings,
    is_broad_outline_parent,
    normalize_requirements,
    parse_requirements,
    requirements_fingerprint,
    select_overview_facets,
    supports_requirement,
)
from guance_agent.schemas import Claim, EvidenceRef


def _ref(chunk_id: str) -> EvidenceRef:
    return EvidenceRef(
        planid="new-policy", chunk_id=chunk_id, title="示例规划", area="新河市",
        fyp="十五五", admin_level=None, plantype_label=None, section_idx=1,
        section_path="产业发展", char_start=0, char_end=60, source="kb_read",
    )


def _result(*facts: str):
    claims = [Claim(text=fact, chunk_ids=[f"c{index}"], quotes=[fact])
              for index, fact in enumerate(facts, start=1)]
    refs = [_ref(claim.chunk_ids[0]) for claim in claims]
    return SimpleNamespace(
        structured=None,
        evidence_refs=refs,
        claims=claims,
        meta={"quote_verification": {"unverified": 0}},
        evidence_status="verified",
        status="completed",
        error=None,
        task_type="qa",
    )


def _support(result, question: str, supports: dict):
    from guance_agent.research_quality import requirements

    units = requirements(question, result.meta.get("research_requirements"))["units"]
    if isinstance(result.meta.get("research_requirements"), list):
        result.meta["research_requirements"] = units
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(units),
        "supports": supports,
    }


def test_overview_question_words_do_not_become_research_subjects():
    units = parse_requirements("发展规划对目标安排主要涉及哪些方面？")

    assert all(unit.get("subject") != "哪些" for unit in units)
    assert all(unit.get("label") != "哪些" for unit in units)


def test_outline_expansion_uses_explicit_source_subheadings_not_claims():
    labels = expand_outline_subheadings(
        "本规划明确发展目标：——经济表现：稳步提升。——公共服务：持续完善。")

    assert labels == ["经济表现", "公共服务"]
    assert is_broad_outline_parent("三、经济和社会发展重点")
    assert not is_broad_outline_parent("推动重点领域率先突破")


def test_changes_and_characteristics_question_uses_source_outline_for_coverage():
    from guance_agent.qa_controlled import _overview_request

    question = "十五期间金融业发展呈现哪些变化？这些变化反映了哪些发展特点？"
    sections = [
        {"idx": 1, "path": "十五期间发展变化 / 金融改革与服务"},
        {"idx": 2, "path": "十五期间发展变化 / 现金收支"},
    ]

    assert _overview_request(question)
    assert [item["label"] for item in select_overview_facets(question, sections)] == [
        "金融改革与服务", "现金收支",
    ]


def test_sparse_parent_claim_cannot_cover_each_catalog_subheading():
    from guance_agent.research_quality import requirements

    question = "发展目标主要涉及哪些方面？"
    result = _result("本规划将统筹推进各项重点工作")
    result.meta["research_requirements"] = [
        {"kind": "subject_fact", "label": "目录方面：经济表现",
         "subject": "经济表现"},
        {"kind": "subject_fact", "label": "目录方面：公共服务",
         "subject": "公共服务"},
    ]
    units = requirements(question, result.meta["research_requirements"])["units"]
    result.meta["catalog_requirement_sources"] = {
        unit["id"]: {"planid": "new-policy", "section_path": "发展目标"}
        for unit in units if unit.get("label", "").startswith("目录方面：")
    }
    _support(result, question, {unit["id"]: [0] for unit in units
                                if unit.get("label", "").startswith("目录方面：")})

    quality = evaluate(result, question)

    assert quality["status"] == "partial"
    assert all(item["status"] == "missing" for item in quality["requirement_coverage"]
               if item["id"] in result.meta["catalog_requirement_sources"])


def test_one_verified_claim_may_support_multiple_facets_when_it_names_both():
    bindings = {"facet:economy": {}, "facet:services": {}}
    claim = "经济表现稳步提升，公共服务持续完善。"

    assert catalog_facet_matches_claim(
        {"id": "facet:economy", "subject": "经济表现"}, claim, bindings)
    assert catalog_facet_matches_claim(
        {"id": "facet:services", "subject": "公共服务"}, claim, bindings)


def test_comparison_side_anaphora_keeps_the_named_research_subject():
    sides = comparison_sides(
        "这些工业发展目标与农业领域的特色产业培育相比，支持方式有何不同？")

    assert sides == ("工业发展目标", "农业领域的特色产业培育")


def test_comparison_freezes_separate_retrievable_requirements_for_both_sides():
    units = normalize_requirements(
        "这些工业发展目标与农业领域的特色产业培育相比，支持方式有何不同？")
    relation = next(unit for unit in units if unit["kind"] == "comparison")
    by_id = {unit["id"]: unit for unit in units}

    assert by_id[relation["left_requirement_id"]]["subject"] == "工业发展目标"
    assert by_id[relation["right_requirement_id"]]["subject"] == "农业领域的特色产业培育"
    assert by_id[relation["left_requirement_id"]]["actionable"] is True
    assert by_id[relation["right_requirement_id"]]["actionable"] is True


def test_frozen_comparison_units_are_idempotent_and_keep_audited_side_support():
    question = "请做一份对照分析。"
    planned = {"requirements": [{
        "id": "untrusted-model-id",
        "kind": "comparison",
        "label": "两类政策实施方法比较",
        "left": "甲类产业发展目标",
        "right": "乙类农业支持目标",
    }]}
    frozen = requirements(question, planned)["units"]
    repeated = requirements(question, frozen)["units"]
    first_by_id = {unit["id"]: unit for unit in frozen}
    repeated_by_id = {unit["id"]: unit for unit in repeated}
    first_relation = next(unit for unit in frozen if unit["kind"] == "comparison")
    repeated_relation = next(unit for unit in repeated if unit["kind"] == "comparison")

    assert "untrusted-model-id" not in first_by_id
    assert first_relation["left_requirement_id"] == repeated_relation["left_requirement_id"]
    assert first_relation["right_requirement_id"] == repeated_relation["right_requirement_id"]
    for side, expected_subject in (("left", first_relation["left"]),
                                   ("right", first_relation["right"])):
        first_side = first_by_id[first_relation[f"{side}_requirement_id"]]
        repeated_side = repeated_by_id[repeated_relation[f"{side}_requirement_id"]]
        assert first_side["subject"] == repeated_side["subject"] == expected_subject
        assert first_side["kind"] == repeated_side["kind"] == "subject_fact"

    result = _result(
        "甲类产业发展目标为建设先进制造基地",
        "乙类农业支持目标为建设示范基地",
    )
    result.answer = (
        "甲类产业发展目标为建设先进制造基地。乙类农业支持目标为建设示范基地。"
        "甲类侧重产业载体，乙类侧重农业示范，实施路径不同。"
    )
    result.structured = {
        "schema_version": "qa-report/1",
        "dimensions": ["地区差异"],
        "blocks": [
            {"dimension": "甲类产业发展目标", "title_path": ["甲类政策"],
             "kind": "fact", "text": result.claims[0].text,
             "supporting_chunk_ids": ["c1"], "supporting_claim_indexes": [0],
             "verification": "model_reviewed_evidence_linked"},
            {"dimension": "乙类农业支持目标", "title_path": ["乙类政策"],
             "kind": "fact", "text": result.claims[1].text,
             "supporting_chunk_ids": ["c2"], "supporting_claim_indexes": [1],
             "verification": "model_reviewed_evidence_linked"},
            {"dimension": "地区差异", "title_path": ["比较结论"],
             "kind": "analysis",
             "text": "甲类侧重产业载体，乙类侧重农业示范，实施路径不同。",
             "supporting_chunk_ids": ["c1", "c2"],
             "supporting_claim_indexes": [0, 1],
             "verification": "model_reviewed_evidence_linked"},
        ],
        "coverage": {"status": "complete", "covered": ["地区差异"], "missing": []},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    result.meta["research_requirements"] = frozen
    supports = {}
    for unit in frozen:
        if unit["kind"] == "comparison":
            supports[unit["id"]] = [0, 1]
        elif unit["kind"] == "subject_fact":
            supports[unit["id"]] = [0 if unit["subject"] == first_relation["left"] else 1]
        else:
            supports[unit["id"]] = []
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "basis": "audited_report",
        "supports": supports,
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(frozen),
    }

    quality = evaluate(result, question)

    assert quality["status"] == "complete", (quality["missing_requirements"],
                                               quality["gaps"],
                                               quality["requirement_coverage"])
    comparison = next(item for item in quality["requirement_coverage"]
                      if item["kind"] == "comparison")
    assert comparison["status"] == "covered"
    assert set(comparison["evidence_claim_indexes"]) == {0, 1}


def test_comparison_side_content_period_comes_from_that_side_not_question_order():
    units = normalize_requirements(
        "请比较金融业务发展情况。",
        {"requirements": [{
            "kind": "comparison", "label": "十五期间与十一五的金融业务比较",
            "left": "十五期间金融业务发展表现",
            "right": "十一五规划金融业务发展目标",
        }]},
    )
    relation = next(unit for unit in units if unit["kind"] == "comparison")
    by_id = {unit["id"]: unit for unit in units}

    assert by_id[relation["left_requirement_id"]]["content_period"] == "第十个五年计划"
    assert by_id[relation["right_requirement_id"]]["content_period"] == "第十一个五年规划"


def test_identical_comparison_subjects_remain_distinct_across_requested_periods():
    question = "请比较“十五”与“十一五”期间的农业发展。"
    planned = {"requirements": [{
        "kind": "comparison", "label": "农业发展跨期比较",
        "left": "农业发展", "right": "农业发展",
    }]}
    frozen = normalize_requirements(question, planned)
    repeated = normalize_requirements(question, frozen)
    first_relation = next(unit for unit in frozen if unit["kind"] == "comparison")
    repeated_relation = next(unit for unit in repeated if unit["kind"] == "comparison")
    first_by_id = {unit["id"]: unit for unit in frozen}
    repeated_by_id = {unit["id"]: unit for unit in repeated}

    left_id = first_relation["left_requirement_id"]
    right_id = first_relation["right_requirement_id"]
    assert left_id != right_id
    assert left_id == repeated_relation["left_requirement_id"]
    assert right_id == repeated_relation["right_requirement_id"]
    assert first_by_id[left_id]["subject"] == first_by_id[right_id]["subject"] == "农业发展"
    assert first_by_id[left_id]["content_period"] != first_by_id[right_id]["content_period"]
    assert first_by_id[left_id]["content_period"] == repeated_by_id[left_id]["content_period"]
    assert first_by_id[right_id]["content_period"] == repeated_by_id[right_id]["content_period"]


def test_identical_comparison_subjects_without_distinct_period_pairing_stay_unresolved():
    units = normalize_requirements("请比较两种农业发展方式。", {
        "requirements": [{
            "kind": "comparison", "label": "农业发展方式比较",
            "left": "农业发展", "right": "农业发展",
        }],
    })

    assert any(unit["kind"] == "unresolved_requirement" for unit in units)


def test_carrier_document_period_does_not_cover_a_different_content_period():
    question = "请比较“十五”与“十一五”期间的农业发展。"
    planned = {"requirements": [{
        "kind": "comparison", "label": "农业发展跨期比较",
        "left": "农业发展", "right": "农业发展",
    }]}
    frozen = normalize_requirements(question, planned)
    relation = next(unit for unit in frozen
                    if unit.get("kind") == "comparison"
                    and unit["label"] == "农业发展跨期比较")
    left_id = relation["left_requirement_id"]
    right_id = relation["right_requirement_id"]
    result = _result("十五期间农业发展以保护生态为主。")
    result.evidence_refs[0].fyp = "第十一个五年规划"
    result.meta["research_question"] = question
    result.meta["research_requirements"] = frozen
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "supports": {unit["id"]: ([0] if unit.get("kind") == "subject_fact" else [])
                     for unit in frozen},
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(frozen),
    }

    quality = evaluate(result, question)
    side_coverage = {unit["id"]: unit for unit in quality["requirement_coverage"]
                     if unit.get("kind") == "subject_fact"}

    assert side_coverage[left_id]["status"] == "covered"
    assert side_coverage[right_id]["status"] == "missing"


def test_quote_content_periods_support_both_same_subject_comparison_sides():
    question = "请比较“十五”与“十一五”期间的农业发展。"
    planned = {"requirements": [{
        "kind": "comparison", "label": "农业发展跨期比较",
        "left": "农业发展", "right": "农业发展",
    }]}
    frozen = normalize_requirements(question, planned)
    relation = next(unit for unit in frozen
                    if unit.get("kind") == "comparison"
                    and unit["label"] == "农业发展跨期比较")
    left_id = relation["left_requirement_id"]
    right_id = relation["right_requirement_id"]
    result = _result(
        "十五期间农业发展以保护生态为主。",
        "十一五期间农业发展转向标准化生产。",
    )
    for ref in result.evidence_refs:
        ref.fyp = "第十一个五年规划"
    result.meta["research_question"] = question
    result.meta["research_requirements"] = frozen
    result.meta["requirement_support"] = {
        "verification": "semantic_reviewed_against_quotes",
        "supports": {unit["id"]: (
            [0] if unit["id"] == left_id else
            [1] if unit["id"] == right_id else [])
                     for unit in frozen},
        "claim_fingerprint": claim_fingerprint(result.claims),
        "requirements_fingerprint": requirements_fingerprint(frozen),
    }

    quality = evaluate(result, question)
    side_coverage = {unit["id"]: unit for unit in quality["requirement_coverage"]
                     if unit.get("kind") == "subject_fact"}

    assert side_coverage[left_id]["status"] == "covered"
    assert side_coverage[right_id]["status"] == "covered"


def test_any_verified_fact_does_not_complete_an_unrelated_single_facet_question():
    result = _result("新能源装备产业有32家企业")

    quality = evaluate(result, "这些政策的主要措施有哪些？")

    assert quality["status"] == "partial"
    assert "重点措施" in quality["missing_requirements"]


def test_audited_semantic_mapping_can_cover_a_measure_without_the_word_measure():
    result = _result("建设产业基地并开展技术培训")
    _support(result, "这些政策的主要措施有哪些？", {"dimension:重点措施": [0]})

    quality = evaluate(result, "这些政策的主要措施有哪些？")

    assert quality["status"] == "complete"
    assert "重点措施" in quality["covered_requirements"]


def test_semantic_mapping_cannot_use_a_count_to_cover_requested_revenue():
    result = _result("新能源装备产业有32家企业")
    _support(
        result,
        "关于新能源装备产业的企业数量和年销售收入目标分别是多少？",
        {"metric:2:annual_revenue:新能源装备产业": [0]},
    )

    quality = evaluate(
        result,
        "关于新能源装备产业的企业数量和年销售收入目标分别是多少？",
    )

    assert quality["status"] == "partial"
    assert "年销售收入" in quality["missing_requirements"]


def test_one_metric_does_not_cover_a_second_metric_for_the_same_subject():
    result = _result("新能源装备产业有32家企业")

    quality = evaluate(
        result,
        "关于新能源装备产业的企业数量和年销售收入目标分别是多少？",
    )

    assert quality["status"] == "partial"
    assert "企业数量" in quality["covered_requirements"]
    assert "年销售收入" in quality["missing_requirements"]


def test_year_and_entity_count_do_not_cover_requested_output_value():
    result = _result("规划于2025年实施，数字农业经营主体达到100家")

    quality = evaluate(
        result,
        "关于数字农业的目标产值和规划年份分别是什么？请给出量化指标。",
    )

    assert quality["status"] == "partial"
    assert "目标产值" in quality["missing_requirements"]


def test_equivalent_revenue_term_supports_requested_sales_revenue():
    result = _result(
        "新能源装备产业有32家企业",
        "新能源装备产业年营业收入达到20亿元",
    )

    quality = evaluate(
        result,
        "关于新能源装备产业的企业数量和年销售收入目标分别是多少？",
    )

    assert quality["status"] == "complete"
    assert set(quality["covered_requirements"]) >= {"企业数量", "年销售收入"}


def test_equivalent_currency_units_match_an_explicit_revenue_target():
    result = _result("新能源装备产业年营业收入达到20000万元")
    question = "关于新能源装备产业，年销售收入目标为2亿元，请核实。"
    result.structured = {
        "schema_version": "qa-report/1", "dimensions": [],
        "blocks": [{"dimension": "数值核对", "title_path": ["年销售收入"],
                    "kind": "analysis", "text": "新能源装备产业年销售收入达到20000万元，折合2亿元。",
                    "verification": "model_reviewed_evidence_linked",
                    "supporting_chunk_ids": ["c1"],
                    "supporting_claim_indexes": [0]}],
        "coverage": {"status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    result.answer = "新能源装备产业年销售收入达到20000万元，折合2亿元。"
    units = requirements(question)["units"]
    result.meta["research_requirements"] = units
    _support(result, question, {unit["id"]: [0] for unit in units})
    result.meta["requirement_support"]["basis"] = "audited_report"

    quality = evaluate(result, question)

    assert quality["status"] == "complete"
    assert "年销售收入" in quality["covered_requirements"]


def test_a_different_revenue_value_requires_comparison_not_exact_quote_match():
    result = _result("新能源装备产业年营业收入达到150亿元")

    quality = evaluate(
        result,
        "关于新能源装备产业，年销售收入目标为160亿元，请核实。",
    )

    assert quality["status"] == "partial"
    assert "年销售收入" not in quality["missing_requirements"]
    check = next(unit for unit in quality["requirement_coverage"]
                 if unit["kind"] == "value_check")
    assert check["phase"] == "synthesis"
    assert check["status"] == "missing"


def test_distinct_subjects_are_paired_with_their_own_requested_metrics():
    result = _result(
        "新材料规划产能达到50万吨",
        "生态农业种植面积达到2万亩",
    )

    quality = evaluate(
        result,
        "重点关注新材料和生态农业，分别说明产能与种植面积目标。",
    )

    assert quality["status"] == "complete"
    pairs = {(item["subject"], item["metric"])
             for item in quality["requirement_coverage"] if item["kind"] == "metric"}
    assert pairs == {("新材料", "production_capacity"), ("生态农业", "land_area")}


def test_planning_year_alone_does_not_cover_a_generic_quantified_target():
    result = _result("数字农业规划于2025年实施")

    quality = evaluate(result, "特别关注数字农业的量化指标。")

    assert quality["status"] == "partial"
    assert "数字农业量化指标" in quality["missing_requirements"]


def test_current_question_drops_inherited_targets_and_keeps_requested_section():
    from types import SimpleNamespace

    from guance_agent.evidence_coverage import build_coverage
    from guance_agent.research_requirements import parse_requirements

    units = parse_requirements(
        "研究对象：旧产业发展的整体部署；本轮问题：请把服务业补充进总结。"
    )

    assert {unit["label"] for unit in units} == {"服务业"}

    result = _result("旧产业发展部署已经完成")
    coverage = build_coverage(
        result,
        "研究对象：旧产业发展的整体部署；本轮问题：请把服务业补充进总结。",
        SimpleNamespace(evidence=None, scope=None,
                        route=SimpleNamespace(planids=())),
    )
    assert "服务业" in coverage["missing_requirements"]
    assert all("旧产业" not in action["focus"] for action in coverage["next_actions"])


def test_same_policy_comparison_requires_both_sides_and_reviewed_analysis():
    result = _result(
        "工业发展目标为先进制造业",
        "特色农业支持方式包括建设示范基地",
    )
    result.structured = {
        "schema_version": "qa-report/1",
        "blocks": [{
            "dimension": "产业发展方向", "title_path": ["支持方式对比"],
            "kind": "analysis",
            "text": "工业发展目标为先进制造业；特色农业支持方式包括建设示范基地，因此采用不同支持方式。",
            "supporting_chunk_ids": ["c1", "c2"],
            "supporting_claim_indexes": [0, 1],
            "verification": "model_reviewed_evidence_linked",
        }],
        "coverage": {"status": "complete"},
        "verification": {"facts": "semantic_reviewed_against_quotes"},
    }
    result.answer = result.structured["blocks"][0]["text"]
    units = requirements(
        "某县规划中工业发展目标与特色农业比较，政策支持方式有何不同？"
    )["units"]
    result.meta["research_requirements"] = units
    support = {}
    for unit in units:
        if unit["kind"] == "subject_fact" and unit.get("subject") == "工业发展目标":
            support[unit["id"]] = [0]
        elif unit["kind"] == "subject_fact" and unit.get("subject") == "特色农业":
            support[unit["id"]] = [1]
        elif unit["kind"] == "comparison":
            support[unit["id"]] = [0, 1]
        else:
            support[unit["id"]] = []
    _support(result, "某县规划中工业发展目标与特色农业比较，政策支持方式有何不同？", support)
    result.meta["requirement_support"]["basis"] = "audited_report"

    quality = evaluate(
        result,
        "某县规划中工业发展目标与特色农业比较，政策支持方式有何不同？",
    )

    assert quality["status"] == "complete"
    comparison = next(item for item in quality["requirement_coverage"]
                      if item["kind"] == "comparison")
    assert comparison["status"] == "covered"


def test_coverage_exposes_every_independent_metric_gap_as_an_action():
    from types import SimpleNamespace

    from guance_agent.evidence_coverage import build_coverage

    result = _result()
    result.claims = []
    result.evidence_refs = []
    coverage = build_coverage(
        result,
        "关于新能源装备产业的企业数量和年销售收入目标分别是多少？",
        SimpleNamespace(evidence=None, scope=None,
                        route=SimpleNamespace(planids=())),
    )

    assert coverage["status"] == "insufficient"
    assert {item["gap_label"] for item in coverage["next_actions"]} == {
        "企业数量", "年销售收入",
    }
    assert all(item["requirement_id"] and item["focus"] and item["query"]
               for item in coverage["next_actions"])


def test_planned_requirements_add_unknown_metrics_and_preserve_rule_requirements():
    from guance_agent.research_requirements import normalize_requirements

    question = "比较甲市与乙市的新材料产业规划，补充服务业一节"
    planned = {"requirements": [
        {"kind": "metric", "label": "投资强度", "subject": "新材料产业",
         "metric": "investment_intensity"},
        {"kind": "metric", "label": "人均床位", "subject": "服务业",
         "metric": "beds_per_capita"},
        {"kind": "metric", "label": "服务覆盖率", "subject": "服务业",
         "metric": "coverage_rate"},
        {"kind": "comparison", "label": "甲市与乙市比较",
         "left": "甲市", "right": "乙市", "phase": "synthesis",
         "actionable": False},
        {"kind": "output_section", "label": "服务业",
         "phase": "synthesis", "actionable": False},
    ]}

    units = normalize_requirements(question, planned)
    assert {unit["metric"] for unit in units if unit["kind"] == "metric"} >= {
        "investment_intensity", "beds_per_capita", "coverage_rate",
    }
    assert any(unit["kind"] == "comparison" for unit in units)
    assert any(unit["kind"] == "output_section" for unit in units)
    assert {unit["id"] for unit in units}
    assert len({unit["id"] for unit in units}) == len(units)


def test_planner_id_collisions_do_not_merge_distinct_requirements_or_rule_units():
    from guance_agent.research_requirements import normalize_requirements

    units = normalize_requirements(
        "关于新能源装备产业的年销售收入目标是多少？",
        {"requirements": [
            {"id": "same", "kind": "metric", "label": "投资强度",
             "subject": "新能源装备产业", "metric": "investment_intensity"},
            {"id": "same", "kind": "metric", "label": "覆盖率",
             "subject": "新能源装备产业", "metric": "coverage_rate"},
            {"id": "metric:1:annual_revenue:新能源装备产业",
             "kind": "metric", "label": "企业数量", "metric": "enterprise_count",
             "subject": "新能源装备产业"},
        ]},
    )

    planned = [unit for unit in units if unit["metric"] in {
        "investment_intensity", "coverage_rate",
    }]
    assert len(planned) == 2
    assert len({unit["id"] for unit in planned}) == 2
    assert any(unit["metric"] == "annual_revenue" for unit in units)
    assert any(unit["metric"] == "enterprise_count" for unit in units)
    assert "same" not in {unit["id"] for unit in units}


def test_document_locator_is_not_a_factual_subject_or_duplicate_unresolved_gap():
    question = "密云县十一五规划的经济发展方面有哪些具体表现和量化指标？"
    units = normalize_requirements(question, {"requirements": [
        {"kind": "output_section", "label": "经济发展方面的主要内容",
         "subject": "密云县'十一五'规划"},
        {"kind": "metric", "label": "经济发展方面的量化指标",
         "subject": "密云县'十一五'规划中发展基础中的经济发展方面",
         "metric": "annual_revenue"},
        {"kind": "unresolved_requirement", "label": question},
    ]})

    assert not any(unit["kind"] == "unresolved_requirement" for unit in units)
    assert all(unit.get("subject") is None for unit in units
               if unit["label"] in {"经济发展方面的主要内容",
                                    "经济发展方面的量化指标"})
    assert any(unit["kind"] == "output_section" for unit in units)


def test_source_locator_planner_unit_is_non_actionable_citation_metadata():
    units = normalize_requirements("你上面提到的第①项具体是什么？", {
        "requirements": [{
            "kind": "subject_fact", "label": "定位依据：规划第①项中的产业布局内容",
            "subject": "临朐县十一五规划",
        }],
    })

    locator = next(unit for unit in units if "定位依据" in unit["label"])
    assert locator["kind"] == "source_citation"
    assert locator["actionable"] is False


def test_referential_first_item_label_keeps_its_factual_requirement():
    units = normalize_requirements("你上面提到的第①项中，金融业务的具体目标是什么？", {
        "requirements": [{
            "kind": "subject_fact", "label": "第①项中的金融业务目标",
            "subject": "金融业务", "metric": "目标",
        }],
    })

    item = next(unit for unit in units if unit["label"] == "第①项中的金融业务目标")
    assert item["kind"] == "subject_fact"
    assert item["subject"] == "金融业务"
    assert item["actionable"] is True


def test_same_policy_industry_comparison_is_not_cross_document_comparison():
    same_policy_questions = (
        "某县规划中工业发展目标与特色农业比较，政策支持方式有何不同？",
        "请归纳这篇规划的工业和农业发展目标。",
        "工业、农业的目标分别是什么？",
        "工业和农业各自有什么目标？",
    )
    cross_region = "比较甲市与乙市规划的扶持措施有何差异？"

    assert all(not multi_document_required(question) for question in same_policy_questions)
    assert multi_document_required(cross_region)


def test_overview_facets_come_from_relevant_catalog_outline_only():
    sections = [
        {"idx": 1, "path": "一、发展目标 / （一）经济发展"},
        {"idx": 2, "path": "一、发展目标 / （二）创新驱动"},
        {"idx": 3, "path": "二、实施保障 / （一）组织领导"},
    ]
    facets = select_overview_facets("发展目标涉及哪些方面？", sections)
    assert [item["label"] for item in facets] == ["经济发展", "创新驱动"]
    assert select_overview_facets("年销售收入目标是多少？", sections) == []


def test_overview_facet_limit_reports_uncovered_outline_remainder():
    sections = [
        {"idx": index, "path": f"一、主要任务 / （{index}）方面{index}"}
        for index in range(1, 27)
    ]
    facets = select_overview_facets("主要任务有哪些方面？", sections)
    assert len(facets) == 24
    assert facets[-1]["overflow"] is True
    assert facets[-1]["remaining_count"] == 3


def test_catalog_facet_support_must_come_from_its_bound_section_and_document():
    claim = Claim(text="完善城乡建设", chunk_ids=["c1"], quotes=["完善城乡建设"])
    local_ref = _ref("c1").model_copy(update={
        "planid": "new-policy", "section_path": "一、发展目标 / （一）城乡建设",
    })
    requirement = {"id": "planned:facet", "kind": "subject_fact"}
    binding = {"planned:facet": {
        "planid": "new-policy", "section_path": "一、发展目标 / （一）城乡建设",
    }}
    assert claim_matches_requirement_source(requirement, claim, {"c1": local_ref}, binding)
    foreign_ref = local_ref.model_copy(update={"planid": "other-policy"})
    assert not claim_matches_requirement_source(requirement, claim, {"c1": foreign_ref}, binding)
    wrong_section = local_ref.model_copy(update={"section_path": "二、实施保障"})
    assert not claim_matches_requirement_source(requirement, claim, {"c1": wrong_section}, binding)


def test_passage_content_period_is_tied_to_the_supported_fact_not_metadata():
    unit = {"kind": "subject_fact", "subject": "金融业",
            "content_period": "第十个五年计划"}
    assert supports_requirement(unit, "十五期间金融业保险业务快速发展。")
    assert not supports_requirement(unit, "十一五期间金融业收入年均增长12%。")
