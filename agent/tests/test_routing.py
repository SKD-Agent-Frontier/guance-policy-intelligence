"""检索路由：对象识别必须是确定性的，且不能把年份当编号。"""

from guance_agent.routing import decide, parse_areas, parse_fyps, parse_planids, strip_annotations


def test_labeled_planid_is_recognised():
    d = decide("\u6bd4\u8f83\u5927\u6e21\u53e3\u533a\uff08planid 10032\uff09\u4e0e\u5357\u5e73\u5e02\uff08planid 10079\uff09\u7684\u5dee\u5f02\u3002")
    assert d.kind == "objects"
    assert d.planids == ("10032", "10079")
    assert d.multi is True
    assert d.blocked is None


def test_year_is_never_treated_as_planid():
    d = decide("\u5206\u6790\u5168\u56fd\u5c42\u9762\uff082016\uff09\u3001\u798f\u5efa\u5357\u5e73\u5e02\uff082010\uff09\u653f\u5e9c\u5de5\u4f5c\u62a5\u544a\u7684\u5dee\u5f02\u3002")
    assert d.planids == ()
    assert d.kind == "open"


def test_bare_numbers_are_not_planids():
    assert parse_planids("2021\u5e74\u6295\u5165 5000 \u4e07\u5143\uff0c\u8986\u76d6 120 \u4e2a\u793e\u533a") == ()
    assert parse_planids("planid: 4462 \u4e0e planid 4536") == ("4462", "4536")


def test_single_object_is_not_multi():
    d = decide("\u68b3\u7406\u8fbe\u5dde\u5e02\uff08planid 9341\uff09\u7684\u6c11\u751f\u6295\u5165\u8981\u6c42\u3002")
    assert d.planids == ("9341",) and d.multi is False


def test_title_object_is_recognised_and_book_marks_stripped_for_query():
    d = decide("\u300a\u82cf\u5dde\u5e02\u201c\u5341\u56db\u4e94\u201d\u6c11\u653f\u4e8b\u4e1a\u53d1\u5c55\u89c4\u5212\u300b\u63d0\u51fa\u54ea\u4e9b\u4eba\u624d\u57f9\u517b\u8981\u6c42\uff1f")
    assert d.kind == "objects"
    assert d.titles == ("\u82cf\u5dde\u5e02\u201c\u5341\u56db\u4e94\u201d\u6c11\u653f\u4e8b\u4e1a\u53d1\u5c55\u89c4\u5212",)
    assert "\u300a" not in d.sub_query and "\u300b" not in d.sub_query


def test_area_and_period_are_extracted_as_filters():
    d = decide("\u5341\u56db\u4e94\u671f\u95f4\u82cf\u5dde\u5e02\u517b\u8001\u670d\u52a1\u653f\u7b56\u6709\u54ea\u4e9b\u8981\u6c42\uff1f")
    assert d.areas == ("\u82cf\u5dde\u5e02",)
    assert d.fyps == ("\u7b2c\u5341\u56db\u4e2a\u4e94\u5e74\u89c4\u5212",)
    assert d.kind == "open"


def test_area_parser_does_not_swallow_leading_verbs():
    assert parse_areas("\u6bd4\u8f83\u5927\u6e21\u53e3\u533a\u4e0e\u5357\u5e73\u5e02") == ("\u5927\u6e21\u53e3\u533a", "\u5357\u5e73\u5e02")
    assert parse_areas("\u4e0e\u5357\u5e73\u5e02\u76f8\u6bd4") == ("\u5357\u5e73\u5e02",)
    assert parse_areas("\u8be5\u5730\u533a\u3001\u5404\u5730\u3001\u672c\u5e02") == ()


def test_open_topic_has_no_objects():
    d = decide("\u517b\u8001\u670d\u52a1\u4eba\u624d\u57f9\u517b\u6709\u54ea\u4e9b\u653f\u7b56\u8981\u6c42\uff1f")
    assert d.kind == "open" and d.planids == () and d.titles == ()


def test_over_long_query_is_blocked_not_truncated():
    d = decide("\u5206\u6790" * 300)
    assert d.blocked == "query_too_long"
    assert len(d.sub_query) == 600


def test_planid_annotation_removed_but_question_kept():
    d = decide("\u6bd4\u8f83\u7b2c\u5341\u4e8c\u4e2a\u4e94\u5e74\u89c4\u5212\u671f\u5185\uff0c\u603b\u4f53\u89c4\u5212\u7eb2\u8981\uff08planid 4462\uff09\u4e0e\u4e13\u9879\u89c4\u5212\uff08planid 4536\uff09\u7684\u5206\u5de5\u4e0e\u547c\u5e94\u3002")
    assert d.planids == ("4462", "4536")
    assert "planid" not in d.sub_query
    assert "\u5206\u5de5\u4e0e\u547c\u5e94" in d.sub_query
    assert d.fyps == ("\u7b2c\u5341\u4e8c\u4e2a\u4e94\u5e74\u89c4\u5212",)


def test_period_aliases_map_to_rag_canonical_names():
    assert parse_fyps("\u5341\u4e09\u4e94") == ("\u7b2c\u5341\u4e09\u4e2a\u4e94\u5e74\u89c4\u5212",)
    assert parse_fyps("\u5341\u56db\u4e94") == ("\u7b2c\u5341\u56db\u4e2a\u4e94\u5e74\u89c4\u5212",)
    assert parse_fyps("\u5341\u4e94\u4e94") == ("\u7b2c\u5341\u4e94\u4e2a\u4e94\u5e74\u89c4\u5212",)


def test_multi_object_keeps_each_object_separately():
    d = decide("planid 10032 \u4e0e planid 10079 \u5206\u522b\u5728\u4f53\u5236\u673a\u5236\u6539\u9769\u4e0a\u7684\u90e8\u7f72\u3002")
    assert d.planids == ("10032", "10079")
    assert d.multi is True


def test_empty_question_is_blocked():
    assert decide("   ").blocked == "empty_query"


def test_strip_annotations_keeps_content():
    out = strip_annotations("\u56f4\u7ed5\u300a\u67d0\u89c4\u5212\u300b\uff08planid 4462\uff09\u7684\u4f53\u5236\u673a\u5236\u6539\u9769")
    assert "planid" not in out and "4462" not in out
    assert "\u67d0\u89c4\u5212" in out and "\u4f53\u5236\u673a\u5236\u6539\u9769" in out


def test_cangzhou_historic_fifteenth_wording_is_tenth_five_year_plan():
    question = "我想了解沧州市‘十五’规划在产业结构调整方面的主要部署，有哪些重点方向？"
    route = decide(question)
    assert route.kind == "open"
    assert route.areas == ("沧州市",)
    assert route.fyps == ("第十个五年计划",)


def test_historic_tenth_not_confused_with_2026_fifteenth_or_party_congress():
    assert parse_fyps("沧州市“十五”计划") == ("第十个五年计划",)
    assert parse_fyps("十五计划期间") == ("第十个五年计划",)
    assert parse_fyps("第十五个五年规划") == ("第十五个五年规划",)
    assert parse_fyps("十五五产业规划") == ("第十五个五年规划",)
    assert parse_fyps("十五届五中全会") == ()


def test_generic_city_park_and_market_terms_are_not_area_filters():
    assert parse_areas("研究产业园区的升级政策") == ()
    assert parse_areas("优化城市更新的资金政策") == ()
    assert parse_areas("数字经济市场分析") == ()
