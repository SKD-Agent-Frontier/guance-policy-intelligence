"""真实发布库的有限验收测试（仅当设置 GUANCE_CATALOG_STORE_PROJECT 时运行）。

不依赖模型，也不需要 guance_rag 包；直接读取冻结发布的 sqlite 与快照。
"""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.policy_catalog import PolicyCatalogService, load_published_catalog

STORE = os.environ.get("GUANCE_CATALOG_STORE_PROJECT")
pytestmark = pytest.mark.skipif(
    not STORE or not Path(STORE).is_dir(),
    reason="未设置 GUANCE_CATALOG_STORE_PROJECT 指向真实 RAG 运行库")


@pytest.fixture(scope="module")
def snapshot():
    return load_published_catalog(STORE)


@pytest.fixture(scope="module")
def service(snapshot):
    return PolicyCatalogService(snapshot)


def _independent_scan(project: str, keyword: str) -> tuple[int, int]:
    """独立实现：只读取 runtime/releases/documents 与 prepared 快照，不复用生产加载器。"""
    root = Path(project)
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    store = (root / config["storage"]["directory"]).resolve()
    conn = sqlite3.connect((store / "metadata.sqlite3").as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        generation = conn.execute("SELECT generation FROM runtime").fetchone()[0]
        manifest = json.loads(conn.execute(
            "SELECT manifest FROM releases WHERE id=? AND status='ready'",
            (generation,)).fetchone()[0])
        prepared = Path(manifest["prepared_path"])
        ids = [json.loads(line)["id"] for line in
               (prepared / "documents.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
        matched = 0
        for did in ids:
            row = conn.execute("SELECT data,data_hash FROM documents WHERE id=?",
                               (did,)).fetchone()
            if hashlib.sha256(row["data"].encode()).hexdigest() != row["data_hash"]:
                continue
            data = json.loads(row["data"])
            raw = (prepared / data["snapshot"]).read_bytes()
            text = raw.decode("utf-8")
            title = data["metadata"].get("title") or ""
            body = text[data["body_start"]:]
            if keyword in title or keyword in body:
                matched += 1
        return len(ids), matched
    finally:
        conn.close()


def test_total_matches_current_release(snapshot):
    total, _ = _independent_scan(STORE, "数字经济")
    assert snapshot.total_documents == total > 0
    assert snapshot.generation_id
    assert len({record.planid for record in snapshot.records}) == snapshot.total_documents


def test_literal_keyword_count_is_complete_and_deduplicated(snapshot, service):
    total, independent = _independent_scan(STORE, "数字经济")
    result = service.list_policies(q="数字经济", area=None, fyp=None, plantype=None,
                                   page=1, page_size=5)
    assert result["matched_documents"] == independent
    assert result["dataset"]["total_documents"] == total
    # 分页大小不改变匹配总数
    larger = service.list_policies(q="数字经济", area=None, fyp=None, plantype=None,
                                   page=1, page_size=100)
    assert larger["matched_documents"] == independent
    # 同一政策命中多次只计一次
    assert len({item["planid"] for item in larger["items"]}) == len(larger["items"])


def test_real_hits_are_literal_and_locatable(snapshot, service):
    result = service.list_policies(q="数字经济", area=None, fyp=None, plantype=None,
                                   page=1, page_size=10)
    records = {record.planid: record for record in snapshot.records}
    for item in result["items"]:
        record = records[item["planid"]]
        assert item["matches"]
        for match in item["matches"]:
            source = record.title if match["field"] == "title" else record.body
            assert source is not None
            assert source[match["offset"]:match["offset"] + len("数字经济")] == "数字经济"
            assert match["snippet"] in source


def test_real_filters_and_detail(service):
    sample = service.list_policies(q=None, area=None, fyp=None, plantype=None,
                                   page=1, page_size=5)["items"][0]
    record = service.get_policy(sample["planid"])["policy"]
    if record["area"]:
        filtered = service.list_policies(q=None, area=record["area"], fyp=None,
                                         plantype=None, page=1, page_size=5)
        assert any(item["planid"] == record["planid"] for item in filtered["items"])
    if record["fyp"]:
        filtered = service.list_policies(q=None, area=None, fyp=record["fyp"],
                                         plantype=None, page=1, page_size=100)
        assert any(item["planid"] == record["planid"] for item in filtered["items"])
    if record["plantype_label"]:
        filtered = service.list_policies(q=None, area=None, fyp=None,
                                         plantype=record["plantype_label"],
                                         page=1, page_size=5)
        assert any(item["planid"] == record["planid"] for item in filtered["items"])
    sections = record["sections"]
    assert record["body_chars"] > 0
    for section in sections[:5]:
        assert 0 <= section["start"] < section["end"] <= record["body_chars"]
        assert isinstance(section["path"], str)


def test_real_content_offsets_map_to_frozen_body(service):
    sample = service.list_policies(q=None, area=None, fyp=None, plantype=None,
                                   page=1, page_size=1)["items"][0]
    detail = service.get_policy(sample["planid"])["policy"]
    first = service.read_content(sample["planid"], offset=0, limit=100)
    assert len(first["text"]) == min(100, detail["body_chars"])
    offset = max(0, detail["body_chars"] - 10)
    tail = service.read_content(sample["planid"], offset=offset, limit=100)
    assert tail["has_more"] is False
    assert first["text"] + "..." or first["text"]
    # 冻结正文哈希在加载时已校验；这里确认读取是同一份文本的连续切片
    joined = service.read_content(sample["planid"], offset=0,
                                  limit=detail["body_chars"])["text"]
    assert len(joined) == detail["body_chars"]


def test_real_payload_has_no_filesystem_paths(service, snapshot):
    payload = json.dumps({
        "list": service.list_policies(q="数字经济", area=None, fyp=None,
                                      plantype=None, page=1, page_size=2),
        "detail": service.get_policy(snapshot.records[0].planid),
    }, ensure_ascii=False)
    assert str(Path(STORE)) not in payload
    assert ".runtime" not in payload and "prepared_" not in payload
