"""Search cards have readable previews without changing literal evidence."""
from app.policy_catalog import CatalogSnapshot, PolicyCatalogService, PolicyRecord
from app.policy_preview import search_preview


def test_first_title_hit_does_not_duplicate_heading_in_preview():
    title = '海西州“十四五”数字经济发展规划'
    body = (title + "\n\n" +
            "海西州“十四五”数字经济发展规划\n\n" +
            "加强数字基础设施建设，培育数字产业集群，推动数字经济高质量发展。")
    preview = search_preview(body, title, "数字经济")
    assert preview == "加强数字基础设施建设，培育数字产业集群，推动数字经济高质量发展。"


def test_markdown_table_rows_are_readable_without_pipes_or_divider():
    body = ("专栏1 十三五规划主要指标完成情况\n"
            "| 指标 | 2015年 | 2020年 |\n"
            "| --- | --- | --- |\n"
            "| 地区生产总值GDP | 265.0 | 4.8% |")
    preview = search_preview(body, "吉林市规划", "GDP")
    assert preview == "表格：地区生产总值GDP · 265.0 · 4.8%"
    assert "|" not in preview and "---" not in preview


def test_image_markers_and_markdown_formatting_not_shown():
    body = ("![图3](media/WY1589821259615703040.PNG)\n\n"
            "## 重点任务\n\n"
            "发展数字经济，建立数字产业集群。")
    preview = search_preview(body, "规划标题", "数字经济")
    assert preview == "发展数字经济，建立数字产业集群。"
    assert "media/" not in preview and "##" not in preview


def test_ocr_hardwrap_is_joined_for_readable_context():
    body = ("发展数字产业，持续推进数字基础设\n"
            "施建设，推动数字经济与实体经济深度融合。")
    preview = search_preview(body, "规划标题", "数字经济")
    assert preview == "发展数字产业，持续推进数字基础设施建设，推动数字经济与实体经济深度融合。"


def test_title_only_query_uses_substantive_body_lead():
    title = "云南省“十四五”数字农业农村发展规划"
    body = title + "\n\n第一章 总则\n\n着力加强数字乡村基础设施建设和公共服务。"
    preview = search_preview(body, title, "数字农业")
    assert preview == "着力加强数字乡村基础设施建设和公共服务。"


def test_empty_or_media_only_returns_no_preview():
    assert search_preview("", "标题", "标题") is None
    assert search_preview("![图](media/image1.png)\n---", "标题", "标题") is None


def test_render_preview_does_not_mutate_matches_or_evidence_offsets():
    title = "十四五数字经济政策"
    body = title + "\n\n促进数字经济高质量发展。"
    record = PolicyRecord(planid="99999", title=title, area="测试市",
                          fyp="第十四个五年规划", plantype_label="专项规划",
                          document_id="d-99999", body=body, sections=())
    service = PolicyCatalogService(CatalogSnapshot(
        generation_id="test", revision=1, partial=False, records=(record,)))
    item = service.list_policies(q="数字经济")["items"][0]
    assert item["preview"] == "促进数字经济高质量发展。"
    assert [match["field"] for match in item["matches"]] == ["title", "body"]
    for match in item["matches"]:
        original = title if match["field"] == "title" else body
        assert original[match["offset"]:match["offset"] + len("数字经济")] == "数字经济"
        assert match["snippet"] in original
