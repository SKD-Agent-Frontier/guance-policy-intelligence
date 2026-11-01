"""Read-only original HTML heading hints and per-planid media tests."""

from app.policy_format import read_original_html_headings


def test_original_html_headings_are_read_only_and_structured(tmp_path):
    root = tmp_path / "clean"
    folder = root / "corpus" / "20699"
    folder.mkdir(parents=True)
    original = (
        "<html><head><style>h1{color:red}</style></head><body>"
        "<h1>政策总标题</h1><h2>第一章 发展基础</h2>"
        "<p class='MsoHeading2'><span>一、主要目标</span></p>"
        "<p>普通正文应当保持普通格式。</p>"
        "<script>alert('not a heading')</script></body></html>"
    ).encode("utf-8")
    source = folder / "20699.html"
    source.write_bytes(original)
    result = read_original_html_headings(root, "20699")
    assert result["source"] == "original_html"
    assert result["headings"] == [
        {"text": "政策总标题", "level": 2},
        {"text": "第一章 发展基础", "level": 3},
        {"text": "一、主要目标", "level": 3},
    ]
    assert source.read_bytes() == original  # reference library was never modified


def test_missing_and_oversized_html_fail_closed_without_changes(tmp_path):
    assert read_original_html_headings(tmp_path, "123") == {
        "source": "unavailable", "headings": [],
    }
    root = tmp_path / "clean"
    folder = root / "edge_cases" / "123"
    folder.mkdir(parents=True)
    html = folder / "123.html"
    html.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    assert read_original_html_headings(root, "123")["source"] == "unavailable"
    assert html.stat().st_size == 8 * 1024 * 1024 + 1
