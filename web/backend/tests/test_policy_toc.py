"""Printed TOC detection uses original source offsets and never alters text."""
from app.policy_toc import printed_toc_range


def test_fujian_32011_pdf_toc_detects_real_source_end_without_swallowing_preface():
    # Representative unchanged excerpt from edge_cases/32011/32011.md.
    body = (
        "— 2 —\n福建省“十四五”全民医疗保障专项规划\n— 3 —\n"
        "目 录\n"
        "前 言..................................................................5\n"
        "第一章 发展基础......................................................6\n"
        "第一节 发展现状................................................6\n"
        "第二节 发展形势...............................................10\n"
        "第二章 总体要求.....................................................13\n"
        "第一节 指导思想...............................................13\n"
        "— 4 —\n"
        "第三节 健全医保管理服务体系.................................33\n"
        "第四节 积极应对人口老龄化...................................38\n"
        "第六章 保障措施...................................................46\n"
        "第四节 营造良好环境.........................................48\n"
        "— 5 —\n"
        "前 言\n人民健康是社会主义现代化的重要标志。\n"
        "第一章 发展基础\n第一节 发展现状\n正文真实内容。\n"
    )
    span = printed_toc_range(body)
    assert span is not None
    start, end = span
    assert body[start:].startswith("目 录\n")
    assert body[end:].startswith("前 言\n人民健康")
    assert "第四节 营造良好环境" in body[start:end]
    assert "— 4 —" in body[start:end]
    assert body[span[0]:span[1]] + body[span[1]:] == body[span[0]:]


def test_does_not_hide_normal_chapters_or_dates_without_numbered_toc():
    body = ("第一章 发展基础\n2025年各项目标如期完成。\n"
            "第二章 指导思想\n发展质量进一步提高。\n"
            "第三章 主要任务\n到2035年实现远景目标。\n")
    assert printed_toc_range(body) is None
    assert printed_toc_range("目 录\n第一章 发展基础\n实际正文。") is None
    assert printed_toc_range("") is None


def test_no_heading_requires_four_nearby_numbered_entries():
    body = ("第一章 发展基础..................................6\n"
            "第一节 发展现状..................................7\n"
            "第二章 总体要求..................................10\n"
            "第二节 指导思想..................................11\n"
            "正文从这里开始。\n")
    span = printed_toc_range(body)
    assert span == (0, body.index("正文从这里开始。"))
    assert printed_toc_range(
        "第一章 发展基础..........6\n第一节 现状..........7\n第三章 任务..........10\n"
    ) is None


def test_numbered_source_prose_does_not_trigger_toc_by_itself():
    body = "目录\n" + "第五章 工作要求。\n" * 5 + "2025年第三季度推进实施。\n"
    assert printed_toc_range(body) is None



def test_jian_22455_toc_without_printed_page_numbers_is_hidden_only_in_reader():
    # Some source PDFs retain leaders but drop the page-number column entirely.
    body = (
        "2\n目 录\n"
        "第一章 发展现状.....................................\n"
        "第一节 “十三五”时期工作成就························\n"
        "第二节 “十四五”时期发展形势.......................\n"
        "第二章 总体要求.....................................\n"
        "第一节 指导思想.....................................\n"
        "\n第一章 发展现状\n真实政策正文：全市市场监管体系建设取得进展。\n"
    )
    span = printed_toc_range(body)
    assert span is not None
    assert body[span[0]:].startswith("2\n目 录")
    assert body[span[1]:].startswith("第一章 发展现状\n真实政策正文")
    # Ordinary policy prose and the real first chapter are not suppressed.
    assert body[span[1]:].count("真实政策正文") == 1


def test_unmarked_sections_are_not_mistaken_for_a_printed_index():
    text = "目 录\n第一章 发展目标\n第一节 总体要求\n\n第一章 发展目标\n实际正文。"
    assert printed_toc_range(text) is None


def test_short_dotted_leaders_with_page_numbers_still_match():
    body = (
        "目 录\n"
        "第一章 总体要求··32\n"
        "第一节 发展目标..34\n"
        "\n第一章 总体要求\n实际正文。\n"
    )
    span = printed_toc_range(body)
    assert span is not None
    assert body[span[1]:].startswith("第一章 总体要求\n实际正文。")
