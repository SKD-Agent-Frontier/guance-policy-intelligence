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
