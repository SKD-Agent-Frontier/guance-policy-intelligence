"""自然语言模式规划离线回归：只选冻结目录中的真实对象，不调用模型。"""

from app.policy_catalog import CatalogSnapshot, PolicyCatalogService, PolicyRecord
from app.research_planning import resolve_research


def record(planid, area, period="第十三个五年规划", title=None, body="产业结构调整，优化三次产业比重。"):
    return PolicyRecord(
        planid=planid, title=title or f"{area}国民经济和社会发展第十三个五年规划纲要",
        area=area, fyp=period, plantype_label="总体规划纲要", document_id=planid,
        body=body, sections=(), admin_level="市",
    )


def catalog(*records, mapped=True):
    nodes = [{"code": "130000000000", "name": "河北省", "tier": "province",
              "children": [
                  {"code": code, "name": name, "tier": "prefecture", "children": []}
                  for code, name in [("131100000000", "衡水市"),
                                     ("130600000000", "保定市"),
                                     ("130200000000", "唐山市"),
                                     ("130400000000", "邯郸市")]
              ]}]
    codes = {"衡水市": "131100000000", "保定市": "130600000000",
             "唐山市": "130200000000", "邯郸市": "130400000000"}
    mapping = {r.planid: {"region_status": "mapped", "region_code": codes[r.area]}
               for r in records if r.area in codes} if mapped else None
    return PolicyCatalogService(
        CatalogSnapshot(generation_id="freeze", revision=1, partial=False,
                        records=tuple(records)),
        region_payload={"data": nodes} if mapped else None,
        region_mapping=mapping,
    )


QUESTION = "与河北省其他地市相比，衡水市在“十三五”规划中提出的产业结构优化目标有什么特点？"


def test_collective_prefecture_comparison_finds_anchor_and_true_peers():
    store = catalog(
        record("hengshui", "衡水市"),
        record("baoding", "保定市"),
        record("tangshan", "唐山市"),
        record("handan-old", "邯郸市", period="第十二个五年规划"),
    )
    answer = resolve_research("compare", QUESTION, store)
    assert answer["status"] == "ready"
    req = answer["request"]
    assert req["task_type"] == "compare"
    assert req["targets"] == ["hengshui", "baoding", "tangshan"]
    assert "产业结构" in req["dimensions"]
    assert "本轮入选城市" in answer["message"]
    assert "handan-old" not in req["targets"]


def test_collective_scope_does_not_invent_missing_comparison_documents():
    answer = resolve_research("compare", QUESTION, catalog(record("only", "衡水市")))
    assert answer["status"] == "needs_clarification"
    assert answer["request"] is None
    assert "没有足够" in answer["message"]
    assert "填写" not in answer["message"]


def test_extract_chooses_exact_named_document_not_user_filled_id():
    question = "请提取《衡水市国民经济和社会发展第十三个五年规划纲要》的产业结构目标"
    answer = resolve_research("extract", question, catalog(
        record("a", "衡水市"), record("b", "保定市")))
    assert answer["status"] == "ready"
    assert answer["request"]["targets"] == ["a"]


def test_multi_scheme_uses_explicit_policy_titles_and_refuses_one_document():
    store = catalog(record("a", "衡水市"), record("b", "保定市"))
    question = ("对比《衡水市国民经济和社会发展第十三个五年规划纲要》"
                "和《保定市国民经济和社会发展第十三个五年规划纲要》的方案")
    answer = resolve_research("compare_plans", question, store)
    assert answer["status"] == "ready"
    assert answer["request"]["targets"] == ["a", "b"]
    unclear = resolve_research("compare_plans", "比较这两个方案", store)
    assert unclear["status"] == "needs_clarification"
    assert unclear["request"] is None


def test_no_body_is_not_selected_as_comparable_evidence():
    store = catalog(record("a", "衡水市", body=""), record("b", "保定市"))
    answer = resolve_research("compare", QUESTION, store)
    assert answer["status"] == "needs_clarification"
    assert answer["request"] is None


def test_interprovince_compare_still_uses_existing_strict_title_matching():
    store = PolicyCatalogService(CatalogSnapshot(
        generation_id="freeze", revision=1, partial=False, records=(
            PolicyRecord(planid="z", area="浙江省", title="浙江省数字经济发展“十四五”规划",
                         fyp="第十四个五年规划", plantype_label="专项规划",
                         document_id="z", body="数字经济发展", sections=(), admin_level="省"),
            PolicyRecord(planid="j", area="江苏省", title="江苏省数字经济发展“十四五”规划",
                         fyp="第十四个五年规划", plantype_label="专项规划",
                         document_id="j", body="数字经济发展", sections=(), admin_level="省"),
        ),
    ))
    answer = resolve_research("compare", "比较浙江和江苏十四五数字经济发展目标的异同", store)
    assert answer["status"] == "ready"
    assert answer["request"]["targets"] == ["z", "j"]
