"""用真实 Qwen tokenizer 验证边界；篡改测试验证校验器确实能抓住错误。"""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from policy_chunker.source import Source, stable_id
from policy_chunker.structure import parse, heading
from policy_chunker.chunking import table_groups, sentence_ranges
from policy_chunker.tokens import TokenCounter
from policy_chunker.pipeline import process
from policy_chunker.validation import check_document, covered
from types import SimpleNamespace
from policy_chunker.pipeline import build

PROJECT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((PROJECT / "config.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def counter():
    return TokenCounter(PROJECT, CONFIG)


def source(tmp_path, body, newline="\n", planid="1"):
    path = tmp_path / planid / f"{planid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f'---\nplanid: "{planid}"\ntitle: "测试政策"\narea_name: "测试地区"\nfyp_name: "十四五"\n---\n\n' + body
    text = text.replace("\n", newline)
    path.write_bytes(text.encode("utf-8"))
    return Source.load(path)


def run(tmp_path, body, counter, newline="\n", **overrides):
    s = source(tmp_path, body, newline)
    config = {**CONFIG, **overrides}
    return s, process(s, tmp_path, counter, config, stable_id("test", config)), config


def test_crlf_codepoints_duplicate_sentences(tmp_path, counter):
    s, data, config = run(tmp_path, "# 第一章 总则\n\n政策😀保持 原样。\n\n政策😀保持 原样。\n", counter, newline="\r\n")
    assert "\r\n" in s.text
    assert "".join(u["text"] for u in data["units"]) == s.text
    repeated = [u for u in data["units"] if "政策😀" in u["text"]]
    assert len(repeated) == 2 and repeated[0]["start"] != repeated[1]["start"]
    assert not check_document(s, data, counter, config)
    assert s.raw == s.path.read_bytes()


def test_markdown_markers_do_not_make_body_heading(tmp_path):
    assert heading("## " + "这是完整的政策正文，" * 20 + "应当执行。") is None
    assert heading("1.2万元") is None
    assert heading("2025年增长5%") is None
    assert heading("按照第八条执行") is None
    assert heading("## 1.2.1 编制依据")["kind"] == "section"
    s = source(tmp_path, "## " + "正文内容，" * 20 + "。\n")
    units, structures, _ = parse(s, "test")
    assert len(structures) == 1
    assert any(u["kind"] == "paragraph" for u in units)


def test_toc_is_excluded(tmp_path, counter):
    s, data, config = run(tmp_path, "目 录\n第一章 总则 ........ 1\n第二章 目标 ........ 3\n\n第一章 总则\n\n这里才是政策正文。\n", counter)
    assert any(u["kind"] == "toc" and "第二章" in u["text"] for u in data["units"])
    assert all("........" not in c["retrieval_text"] for c in data["chunks"])
    assert not check_document(s, data, counter, config)


def test_list_shared_condition_retained(tmp_path, counter):
    body = "申请人应满足以下条件：\n\n1. " + "支持创新企业发展，" * 30 + "。\n\n2. " + "符合相关规定，" * 30 + "。\n"
    s, data, config = run(tmp_path, body, counter, child_target_tokens=100, parent_target_tokens=150)
    assert len(data["chunks"]) >= 2
    assert all("申请人应满足以下条件" in c["retrieval_text"] for c in data["chunks"])
    assert any(r["kind"] == "condition" for r in data["relations"])
    assert not check_document(s, data, counter, config)


def test_rowspan_is_not_split(tmp_path, counter):
    body = '<table>\n<tr><th>类别</th><th>目标</th></tr>\n<tr><td rowspan="2">共同条件</td><td>' + "重点项目" * 30 + '</td></tr>\n<tr><td>' + "发展目标" * 30 + '</td></tr>\n<tr><td>其他</td><td>' + "其他目标" * 30 + '</td></tr>\n</table>\n'
    s, data, config = run(tmp_path, body, counter, child_target_tokens=100, max_input_tokens=400)
    table = next(u for u in data["units"] if u["kind"] == "table")
    safe, headers = table_groups(s, table)
    assert len(safe) == 2
    first = s.text[safe[0][0]:safe[0][1]]
    assert 'rowspan="2"' in first and first.count("<tr>") == 2
    assert headers[0]["text"].startswith("<tr>")
    assert not check_document(s, data, counter, config)


def test_unsafe_table_stays_pending(tmp_path, counter):
    body = '<table>\n<tr><th>类别</th><th>内容</th></tr>\n<tr><td rowspan="999">未闭合行组</td><td>' + "政策内容" * 500 + '</td></tr>\n</table>\n'
    s, data, config = run(tmp_path, body, counter, max_input_tokens=300)
    assert all(c["status"] == "pending" for c in data["chunks"])
    assert "unsafe_oversize_table" in data["chunks"][0]["reasons"]
    assert not check_document(s, data, counter, config)


def test_indivisible_oversize_is_not_truncated(tmp_path, counter):
    s, data, config = run(tmp_path, "完整内容没有安全句终" * 700 + "\n", counter, max_input_tokens=300)
    assert data["chunks"][0]["embedding_tokens"] > 300
    assert data["chunks"][0]["status"] == "pending"
    assert data["chunks"][0]["source_segments"][0]["text"] == s.text[s.body_start:].lstrip("\n")
    assert not check_document(s, data, counter, config)


def test_explicit_reference_context_and_validator(tmp_path, counter):
    s, data, config = run(tmp_path, "第一条 适用范围\n\n本规定适用于本地企业。\n\n第二条 申请要求\n\n按照第一条执行。\n", counter)
    relation = next(r for r in data["relations"] if r["kind"] == "reference")
    assert relation["status"] == "resolved"
    dependent = next(c for c in data["chunks"] if relation["id"] in c["required_relation_ids"])
    assert "本规定适用于本地企业" in dependent["retrieval_text"]
    assert not check_document(s, data, counter, config)
    damaged = deepcopy(data)
    target = next(c for c in damaged["chunks"] if c["id"] == dependent["id"])
    target["context_segments"] = []
    assert "required_context_not_provided" in check_document(s, damaged, counter, config)


def test_ambiguous_and_external_references_pending(tmp_path, counter):
    s, data, config = run(tmp_path, "第一条 一般要求\n\n按照第九条执行。依照《其他办法》第一条执行。\n", counter)
    assert len([r for r in data["relations"] if r["status"] == "unresolved"]) == 2
    assert all(c["status"] == "pending" for c in data["chunks"])
    assert not check_document(s, data, counter, config)


def test_empty_images_and_missing_media(tmp_path, counter):
    s, data, config = run(tmp_path, "\n\n", counter)
    assert data["documents"][0]["status"] == "no_body" and not data["chunks"]
    s, data, config = run(tmp_path, "![图1](media/missing.png)\n", counter)
    assert data["documents"][0]["status"] == "media_only" and not data["chunks"]
    assert data["media_occurrences"][0]["status"] == "missing"
    assert not check_document(s, data, counter, config)


def test_sentence_splitting_keeps_quotes_and_decimals(tmp_path):
    s = source(tmp_path, "补助金额1.25万元；“符合条件的，可以申请。”其余保持原样。\n")
    ranges = sentence_ranges(s, s.body_start, len(s.text))
    assert len(ranges) == 3
    assert all("1.25" in s.text[a:b] for a, b in ranges[:1])
    assert "".join(s.text[a:b] for a, b in ranges) == s.text[s.body_start:]


@pytest.mark.parametrize("damage,expected", [("unit", "source_text_mismatch"), ("parent", "child_parent_containment"), ("tokens", "token_count_mismatch")])
def test_validator_rejects_tampering(tmp_path, counter, damage, expected):
    s, data, config = run(tmp_path, "# 总则\n\n真实政策正文不能改字。\n", counter)
    if damage == "unit":
        data["units"][-1]["text"] += "伪造"
    elif damage == "parent":
        data["parents"][0]["end"] -= 1
    else:
        data["chunks"][0]["embedding_tokens"] -= 1
    assert expected in check_document(s, data, counter, config)


def test_ids_repeat_but_sources_remain_independent(tmp_path, counter):
    s, one, config = run(tmp_path, "这是相同政策内容。\n", counter)
    two = process(s, tmp_path, counter, config, stable_id("test", config))
    assert one == two
    another = source(tmp_path, "这是相同政策内容。\n", planid="2")
    three = process(another, tmp_path, counter, config, stable_id("test", config))
    assert one["chunks"][0]["id"] != three["chunks"][0]["id"]


def test_previous_paragraph_and_dependency_cycle(tmp_path, counter):
    s, data, config = run(tmp_path, "第一条 申请条件\n\n申请人应为本地企业。\n\n前款所述企业应依法登记。\n", counter)
    reference = next(r for r in data["relations"] if r["kind"] == "reference")
    assert reference["status"] == "resolved" and reference["target_kind"] == "unit"
    assert not check_document(s, data, counter, config)
    s, data, config = run(tmp_path, "第一条 条件\n\n按照第二条执行。\n\n第二条 条件\n\n按照第一条执行。\n", counter)
    assert all(c["status"] == "pending" and "cyclic_required_dependency" in c["reasons"] for c in data["chunks"])
    assert not check_document(s, data, counter, config)


def test_literal_block_is_not_split_as_prose(tmp_path, counter):
    s, data, config = run(tmp_path, "```text\n" + "原样内容。" * 1000 + "\n```\n", counter, max_input_tokens=300)
    assert len(data["chunks"]) == 1 and data["chunks"][0]["kind"] == "literal"
    assert data["chunks"][0]["status"] == "pending"
    assert not check_document(s, data, counter, config)


def test_pipe_table_long_rows_keep_header_and_caption(tmp_path, counter):
    body = "## 表1 发展目标\n\n单位：万元\n\n| 项目 | 目标 |\n| --- | --- |\n" + "".join("| 项目%d | %s |\n" % (i, "完整目标" * 60) for i in range(5)) + "\n注：均为规划期目标。\n"
    s, data, config = run(tmp_path, body, counter, child_target_tokens=100, max_input_tokens=600)
    assert len(data["parents"]) == 1
    assert len(data["chunks"]) > 1
    assert all("表1 发展目标" in c["retrieval_text"] and "单位：万元" in c["retrieval_text"] and "规划期目标" in c["retrieval_text"] for c in data["chunks"])
    assert not check_document(s, data, counter, config)


def test_batch_resume_corruption_incremental_and_assets(tmp_path):
    from PIL import Image
    from policy_chunker.source import read_jsonl, sha
    root = tmp_path / "library"
    first = source(root / "corpus", "政策正文第一部分。\n", planid="901")
    source(root / "corpus", "政策正文第二部分。\n", planid="902")
    for planid in ("901", "902"):
        media = root / "corpus" / planid / "media"
        media.mkdir()
        Image.new("RGB", (2, 2), "white").save(media / "image.png")
    run_dir = tmp_path / "run"
    args = SimpleNamespace(source=str(root), run=str(run_dir), sample=False, resume=False)
    assert build(PROJECT, args) == 0
    assert len(list(read_jsonl(run_dir / "media_assets.jsonl"))) == 1
    documents = list(read_jsonl(run_dir / "documents.jsonl"))
    original = {p.name: sha(p.read_bytes()) for p in run_dir.glob("*.jsonl")}
    args.resume = True
    snapshot = run_dir / documents[0]["snapshot"]
    snapshot.write_bytes(b"interrupted snapshot")
    assert build(PROJECT, args) == 0
    assert snapshot.read_bytes() == first.raw
    stats = json.loads((run_dir / "build_stats.json").read_text())
    assert stats["processed"] == 0 and stats["reused"] == 2
    assert original == {p.name: sha(p.read_bytes()) for p in run_dir.glob("*.jsonl")}
    # 损坏一份缓存，恢复必须重新处理这一份，不能仅相信“任务完成”标记。
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
    config_hash = json.loads((run_dir / "run_config.json").read_text())["config_hash"]
    key = next(k for k, value in checkpoint.items() if value["planid"] == "901")
    shard = PROJECT / ".cache" / "documents" / config_hash / f"{key}.json"
    shard.write_bytes(shard.read_bytes() + b" ")
    assert build(PROJECT, args) == 0
    stats = json.loads((run_dir / "build_stats.json").read_text())
    assert stats["processed"] == 1 and stats["reused"] == 1
    # 新来源版本另建批次；未变化的文档从内容寻址缓存复用，旧快照保留。
    first.path.write_bytes(first.raw + "新增政策要求。\n".encode("utf-8"))
    args.run = str(tmp_path / "incremental")
    assert build(PROJECT, args) == 0
    stats = json.loads((Path(args.run) / "build_stats.json").read_text())
    assert stats["processed"] == 1 and stats["reused"] == 1
    updated = list(read_jsonl(Path(args.run) / "documents.jsonl"))
    assert documents[0]["id"] != updated[0]["id"]
    assert documents[1]["id"] == updated[1]["id"]
    assert (run_dir / documents[0]["snapshot"]).read_bytes() == first.raw


def test_production_line_ordinal_is_not_legal_reference(tmp_path, counter):
    body = "新建第二条玻璃生产线，完成第三条道路建设。\n\n按照第八条规定办理。\n"
    s, data, config = run(tmp_path, body, counter)
    refs = [r for r in data["relations"] if r["kind"] == "reference"]
    assert len(refs) == 1 and refs[0]["trigger"]["text"] == "第八条"
    assert not check_document(s, data, counter, config)


def test_markdown_list_continuation_is_same_item(tmp_path, counter):
    body = "申请条件如下：\n\n1. 首个项目\n\n   本项目的连续说明不能与项目拆开。\n\n2. 第二个项目\n"
    s, data, config = run(tmp_path, body, counter)
    first = next(u for u in data["units"] if u["kind"] == "list_item")
    assert "连续说明" in first["text"]
    assert not check_document(s, data, counter, config)


def test_plain_title_only_is_not_evidence(tmp_path, counter):
    s, data, config = run(tmp_path, "测试政策\n", counter)
    assert data["documents"][0]["status"] == "title_only"
    assert not data["chunks"]
    assert not check_document(s, data, counter, config)


def test_failed_document_is_retried_on_resume(tmp_path, monkeypatch):
    import policy_chunker.pipeline as pipeline
    root = tmp_path / "library"
    source(root / "corpus", "暂时失败后应重新处理的政策正文。\n", planid="903")
    original = pipeline.process
    def temporary_failure(*args, **kwargs):
        raise OSError("temporary input failure")
    monkeypatch.setattr(pipeline, "process", temporary_failure)
    args = SimpleNamespace(source=str(root), run=str(tmp_path / "run"), sample=False, resume=False)
    assert build(PROJECT, args) == 1
    assert (Path(args.run) / "usable_chunks.jsonl").read_bytes() == b""
    monkeypatch.setattr(pipeline, "process", original)
    args.resume = True
    assert build(PROJECT, args) == 0
    stats = json.loads((Path(args.run) / "build_stats.json").read_text())
    assert stats["processed"] == 1 and stats["reused"] == 0
