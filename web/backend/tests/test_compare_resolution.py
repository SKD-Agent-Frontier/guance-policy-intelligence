"""自然语言政策比较定位的离线回归：不需要学校模型或真实发布库。"""
from app.compare_resolution import resolve_compare
from app.policy_catalog import CatalogSnapshot, PolicyCatalogService, PolicyRecord


def record(planid, title, area, fyp="第十四个五年规划", body=""):
    return PolicyRecord(
        planid=planid, title=title, area=area, fyp=fyp,
        plantype_label="专项规划", document_id=planid,
        body=body, sections=(), admin_level="省",
    )


def catalog(*records):
    return PolicyCatalogService(CatalogSnapshot(
        generation_id="test-release", revision=1, partial=False, records=records,
    ))


QUESTION = "比较浙江和江苏十四五数字经济发展目标的异同"


def test_clear_comparison_resolves_both_real_planids_and_dimension():
    service = catalog(
        record("z", "浙江省数字经济发展“十四五”规划", "浙江省"),
        record("j", "江苏省数字经济发展“十四五”规划", "江苏省"),
        record("other", "浙江省数字化转型“十四五”规划", "浙江省"),
        record("old", "江苏省数字经济发展“十三五”规划", "江苏省", fyp="第十三个五年规划"),
    )
    found = resolve_compare(QUESTION, service)
    assert found["status"] == "ready"
    assert found["targets"] == ["z", "j"]
    assert found["dimensions"] == ["发展目标"]
    assert [item["area"] for item in found["policies"]] == ["浙江", "江苏"]


def test_does_not_split_literal_topic_or_use_unrelated_region_or_period():
    service = catalog(
        record("z", "浙江省数字化转型“十四五”规划", "浙江省", body="数字化技术"),
        record("j", "江苏省数字经济发展“十三五”规划", "江苏省", fyp="第十三个五年规划"),
        record("s", "上海市数字经济发展“十四五”规划", "上海市"),
    )
    result = resolve_compare(QUESTION, service)
    assert result["status"] == "needs_clarification"
    assert result["targets"] == []
    assert "浙江" in result["message"]


def test_equal_strength_ambiguous_documents_do_not_choose_by_planid():
    service = catalog(
        record("z1", "浙江省数字经济发展“十四五”规划", "浙江省"),
        record("z2", "浙江省数字经济发展“十四五”方案", "浙江省"),
        record("j", "江苏省数字经济发展“十四五”规划", "江苏省"),
    )
    result = resolve_compare(QUESTION, service)
    # 不同文种会被排序而非混淆，去掉文种加分后才属于真正歧义。
    assert result["status"] == "ready"
    service = catalog(
        record("z1", "浙江省数字经济发展“十四五”规划", "浙江省"),
        record("z2", "浙江省数字经济发展“十四五”规划（修订稿）", "浙江省"),
        record("j", "江苏省数字经济发展“十四五”规划", "江苏省"),
    )
    result = resolve_compare(QUESTION, service)
    assert result["status"] == "needs_clarification"
    assert result["targets"] == []
    assert "同等匹配" in result["message"]


def test_vague_comparison_requests_clarification_without_manual_id_requirement():
    service = catalog(record("z", "浙江数字经济十四五规划", "浙江省"))
    result = resolve_compare("比较浙江和江苏的政策", service)
    assert result["status"] == "needs_clarification"
    assert result["targets"] == []
    assert "五年规划" in result["message"]

def test_zhejiang_beijing_digital_economy_goals_never_select_fire_or_high_tech_plans():
    question = "比较浙江和北京十四五数字经济发展目标的异同"
    service = catalog(
        record("24037", "浙江省消防事业发展“十四五”规划", "浙江省",
               body="推进数字经济相关技术应用。总体目标为降低火灾死亡人数。"),
        record("26147", "北京市“十四五”时期高精尖产业发展规划", "北京市",
               body="培育数字经济产业。推动先进制造业。"),
    )
    result = resolve_compare(question, service)
    assert result["status"] == "needs_clarification"
    assert result["targets"] == []
    assert "浙江" in result["message"]
    assert "正文" in result["message"]


def test_true_topic_policy_outscores_unrelated_mention_despite_decoy_frequency():
    question = "比较浙江和北京十四五数字经济发展目标的异同"
    service = catalog(
        record("fire", "浙江省消防事业发展“十四五”规划", "浙江省",
               body="数字经济" * 40),
        record("z", "浙江省数字经济发展“十四五”规划", "浙江省",
               body="到2025年数字经济规模达到一定目标。"),
        record("tech", "北京市“十四五”时期高精尖产业发展规划", "北京市",
               body="数字经济" * 40),
        record("b", "北京市数字经济发展“十四五”规划", "北京市",
               body="数字经济产业增加值目标。"),
    )
    result = resolve_compare(question, service)
    assert result["status"] == "ready"
    assert result["targets"] == ["z", "b"]
    assert all(item["match"] == "title" for item in result["policies"])


def test_missing_one_direct_topic_policy_never_substitutes_body_only_alternative():
    service = catalog(
        record("z", "浙江数字经济十四五规划", "浙江省"),
        record("tech", "北京高精尖产业十四五规划", "北京市",
               body="数字经济发展目标。"),
    )
    result = resolve_compare("比较浙江和北京十四五数字经济发展目标的异同", service)
    assert result["status"] == "needs_clarification"
    assert result["targets"] == []
    assert "北京" in result["message"]
    assert "正文" in result["message"]


def test_unmatched_topic_remains_clarification_not_nonexistence_claim():
    service = catalog(record("z", "浙江省生态环境十四五规划", "浙江省"))
    result = resolve_compare("比较浙江和北京十四五数字经济发展目标的异同", service)
    assert result["status"] == "needs_clarification"
    assert result["targets"] == []
    assert "当前发布库" in result["message"]
    assert "不存在" not in result["message"]
