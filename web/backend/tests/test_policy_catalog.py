"""政策文献目录服务测试：固定数据 + 假冻结发布目录，不依赖真实 RAG。"""
import hashlib
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from guance_agent.config import Settings

from app import main as api
from app.policy_catalog import (
    CatalogSnapshot,
    CatalogUnavailable,
    PolicyCatalogService,
    PolicyRecord,
    SectionMeta,
    load_published_catalog,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

BODY_1 = "北京市数字经济发展规划。本章提出加快数字经济建设，数字经济规模目标。补贴5000元支持企业。"
BODY_2 = "上海市人才发展规划。提出数字经济人才引进措施，以及住房补贴政策。"
BODY_3 = "广东省数据管理条例。规范数据要素流通，未使用关键词。"
BODY_4 = "北京市养老服务方案。建设社区养老设施，完善服务标准。"


def _record(planid, title, area, fyp, plantype, body, sections=(), admin_level=None):
    return PolicyRecord(planid=planid, title=title, area=area, fyp=fyp,
                        plantype_label=plantype, document_id=f"doc-{planid}",
                        body=body, sections=sections, admin_level=admin_level)


def _snapshot():
    records = (
        _record("10001", "北京市数字经济促进条例", "北京市", "第十四个五年规划", "专题规划", BODY_1,
                (SectionMeta(0, 1, "第一章 总则", 0, 20),
                 SectionMeta(1, 2, "第二章 支持措施", 20, len(BODY_1)))),
        _record("10002", "上海市人才发展规划", "上海市", "第十四个五年规划", "发展规划纲要", BODY_2),
        _record("10003", "广东省数据管理条例", "广东省", "第十三个五年规划", "其他", BODY_3),
        _record("10004", "北京市养老服务方案", "北京市", None, "实施方案", BODY_4),
    )
    return CatalogSnapshot(generation_id="generation-test", revision=3, partial=True,
                           records=records)


@pytest.fixture
def service():
    return PolicyCatalogService(_snapshot())


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "warm_up", lambda _settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)
    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    settings = Settings(kb_backend="rag", rag_store_project="unused")
    app = api.create_app(settings=settings)
    app.state.policy_catalog = PolicyCatalogService(_snapshot())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def bare_client(monkeypatch):
    """未注入目录服务且未配置真实 RAG：必须 503，不能返回伪造文献。"""
    monkeypatch.setattr(api, "warm_up", lambda _settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)
    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    app = api.create_app(settings=Settings())
    with TestClient(app) as test_client:
        yield test_client


# ---- 任务一：目录 -------------------------------------------------------
def test_catalog_listing_without_query(client):
    response = client.get("/api/policies")
    assert response.status_code == 200
    body = response.json()
    assert body["dataset"] == {"generation_id": "generation-test", "revision": 3,
                               "total_documents": 4, "partial": True,
                               "source": "current_published_frozen_release"}
    assert body["query"] == {"q": None, "area": None, "fyp": None,
                             "plantype": None, "match_mode": None}
    assert body["matched_documents"] == 4
    assert [item["planid"] for item in body["items"]] == ["10001", "10002", "10003", "10004"]
    assert body["items"][0]["matches"] == []


def test_items_are_unique_and_complete_across_pages(client):
    first = client.get("/api/policies", params={"page": 1, "page_size": 3}).json()
    second = client.get("/api/policies", params={"page": 2, "page_size": 3}).json()
    planids = [item["planid"] for item in first["items"] + second["items"]]
    assert planids == ["10001", "10002", "10003", "10004"]
    assert len(planids) == len(set(planids))
    assert first["total_pages"] == 2 and second["total_pages"] == 2


def test_catalog_sorts_title_match_then_year_level_report_and_planid():
    records = (
        _record("900", "2024年无标题命中", "x", None, "其他", "关键词", admin_level="国家级"),
        _record("800", "2020年关键词政策", "x", None, "其他", "正文", admin_level="县级"),
        _record("700", "2022年关键词政策", "x", None, "其他", "正文", admin_level="市级"),
        _record("600", "2022年关键词政策", "x", None, "其他", "正文", admin_level="省级"),
        _record("500", "2022年关键词政策", "x", "政府工作报告", "其他", "正文", admin_level="省级"),
        _record("400", "2022年关键词政策", "x", None, "其他", "正文", admin_level="省级"),
        _record("300", "未知标题", "x", None, "其他", "关键词", admin_level="国家级"),
    )
    service = PolicyCatalogService(CatalogSnapshot("g", 1, None, records))
    all_ids = [item["planid"] for item in service.list_policies(
        q="关键词", area=None, fyp=None, plantype=None, page=1, page_size=20)["items"]]
    assert all_ids == ["500", "400", "600", "700", "800", "900", "300"]
    pages = [item["planid"] for page in (1, 2, 3, 4) for item in service.list_policies(
        q="关键词", area=None, fyp=None, plantype=None, page=page, page_size=2)["items"]]
    assert pages == all_ids


def test_same_year_admin_levels_sort_national_to_county_then_unknown():
    records = (
        _record("a", "2022年关键词政策", "x", None, "其他", "正文"),
        _record("d", "2022年关键词政策", "x", None, "其他", "正文", admin_level="县级"),
        _record("c", "2022年关键词政策", "x", None, "其他", "正文", admin_level="市级"),
        _record("b", "2022年关键词政策", "x", None, "其他", "正文", admin_level="省级"),
        _record("e", "2022年关键词政策", "x", None, "其他", "正文", admin_level="国家级"),
    )
    service = PolicyCatalogService(CatalogSnapshot("g", 1, None, records))
    items = service.list_policies(q="关键词", area=None, fyp=None, plantype=None,
                                  page=1, page_size=20)["items"]
    assert [item["planid"] for item in items] == ["e", "b", "c", "d", "a"]


def test_year_inference_and_known_planning_period_rules():
    cases = (
        _record("1", "中华人民共和国2016年政府工作报告", "x", "政府工作报告", "其他", "正文"),
        _record("2", "河南省国民经济和社会发展第十四个五年规划和2035年远景目标纲要",
                "x", "第十四个五年规划", "其他", "正文"),
        _record("3", "多年份2016年与2020年文件", "x", "第十三个五年规划", "其他", "正文"),
        _record("4", "2035年远景目标纲要", "x", None, "其他", "正文"),
        _record("5", "短别名规划", "x", "十五五", "其他", "正文"),
        _record("6", "2035年远景展望", "x", None, "其他", "正文"),
        _record("7", "2021—2025年规划文本", "x", None, "其他", "正文"),
    )
    service = PolicyCatalogService(CatalogSnapshot("g", 1, None, cases))
    assert [record.year for record in cases] == [
        2016, 2021, 2016, None, 2026, None, None,
    ]
    ordered = service.list_policies(q=None, area=None, fyp=None, plantype=None,
                                    page=1, page_size=20)["items"]
    assert [item["planid"] for item in ordered] == ["5", "2", "1", "3", "4", "6", "7"]


def test_reference_year_rejects_ambiguous_ranges_and_vision_years():
    cases = (
        _record("1", "宿迁市气象灾害防御规划（2015―2020年）", "x", None, "其他", "正文"),
        _record("2", "某地2035年 远景目标纲要", "x", None, "其他", "正文"),
        _record("3", "某地规划（2015/2020年）", "x", None, "其他", "正文"),
        _record("4", "2021年某地规划和2035年远景目标", "x", None, "其他", "正文"),
        _record("5", "GB/T 2021 技术规范", "x", None, "其他", "正文"),
    )
    assert [record.year for record in cases] == [None, None, None, 2021, None]


def test_short_tenth_plan_alias_matches_metadata_plan_name():
    record = _record("1", "第十个规划", "x", "第十个五年计划", "其他", "正文")
    service = PolicyCatalogService(CatalogSnapshot("g", 1, None, (record,)))
    alias = service.list_policies(q=None, area=None, fyp="十五", plantype=None,
                                  page=1, page_size=20)
    full_name = service.list_policies(q=None, area=None, fyp="第十个五年计划",
                                      plantype=None, page=1, page_size=20)
    assert alias["matched_documents"] == full_name["matched_documents"] == 1
    assert alias["items"][0]["year"] == full_name["items"][0]["year"] == 2001


def test_reference_year_does_not_search_arbitrary_fyp_text():
    record = _record("1", "普通政策", "x", "某某第十四个五年规划相关材料", "其他", "正文")
    assert record.year is None


def test_title_hit_beats_newer_body_only_hit_and_list_detail_year_match(client):
    response = client.get("/api/policies", params={"q": "数字经济"}).json()
    assert response["items"][0]["planid"] == "10001"
    assert response["items"][0]["year"] == 2021
    detail = client.get("/api/policies/10001").json()["policy"]
    assert detail["year"] == response["items"][0]["year"]


def test_page_beyond_last_is_empty(client):
    body = client.get("/api/policies", params={"page": 9, "page_size": 2}).json()
    assert body["items"] == [] and body["matched_documents"] == 4


# ---- 任务二：字面检索 ---------------------------------------------------
def test_keyword_hits_title(client):
    body = client.get("/api/policies", params={"q": "数字经济促进"}).json()
    assert body["matched_documents"] == 1
    item = body["items"][0]
    assert item["planid"] == "10001"
    assert item["matches"][0] == {"field": "title", "offset": 3,
                                  "snippet": "北京市数字经济促进条例"}


def test_keyword_hits_body(client):
    body = client.get("/api/policies", params={"q": "住房补贴"}).json()
    assert body["matched_documents"] == 1
    item = body["items"][0]
    body_match = next(match for match in item["matches"] if match["field"] == "body")
    assert BODY_2[body_match["offset"]:body_match["offset"] + len("住房补贴")] == "住房补贴"
    assert "住房补贴" in body_match["snippet"]


def test_non_matching_keyword_returns_nothing(client):
    body = client.get("/api/policies", params={"q": "量子计算"}).json()
    assert body["matched_documents"] == 0 and body["items"] == []


def test_multiple_hits_in_one_policy_count_once(client, service):
    record = service.snapshot.records[0]
    assert record.body.count("数字经济") >= 2
    body = client.get("/api/policies", params={"q": "数字经济"}).json()
    matched = [item["planid"] for item in body["items"]]
    assert matched.count("10001") == 1
    assert body["matched_documents"] == 2  # 10001 标题+正文、10002 正文


def test_match_total_independent_of_page_size(service):
    small = service.list_policies(q="数字经济", area=None, fyp=None, plantype=None,
                                  page=1, page_size=1)
    large = service.list_policies(q="数字经济", area=None, fyp=None, plantype=None,
                                  page=1, page_size=100)
    assert small["matched_documents"] == large["matched_documents"] == 2
    assert len(small["items"]) == 1 and len(large["items"]) == 2


def test_snippet_comes_from_frozen_body(service):
    result = service.list_policies(q="数字经济", area=None, fyp=None, plantype=None,
                                   page=1, page_size=100)
    record = next(item for item in service.snapshot.records if item.planid == "10001")
    match = next(match for match in result["items"][0]["matches"] if match["field"] == "body")
    assert record.body[match["offset"]:match["offset"] + 4] == "数字经济"
    assert match["snippet"] in record.body


# ---- 任务三：筛选与组合 -------------------------------------------------
def test_area_filter(client):
    body = client.get("/api/policies", params={"area": "北京市"}).json()
    assert body["matched_documents"] == 2
    assert {item["planid"] for item in body["items"]} == {"10001", "10004"}


def test_fyp_filter_accepts_display_alias(client):
    exact = client.get("/api/policies", params={"fyp": "第十四个五年规划"}).json()
    alias = client.get("/api/policies", params={"fyp": "十四五"}).json()
    assert exact["matched_documents"] == alias["matched_documents"] == 2
    assert {item["planid"] for item in alias["items"]} == {"10001", "10002"}


def test_fyp_filter_does_not_infer_dates(client):
    body = client.get("/api/policies", params={"fyp": "近十年"}).json()
    assert body["matched_documents"] == 0


def test_plantype_filter(client):
    body = client.get("/api/policies", params={"plantype": "其他"}).json()
    assert body["matched_documents"] == 1
    assert body["items"][0]["planid"] == "10003"


def test_combined_filters(client):
    body = client.get("/api/policies",
                      params={"q": "数字经济", "area": "北京市",
                              "fyp": "十四五", "plantype": "专题规划"}).json()
    assert body["matched_documents"] == 1
    assert body["items"][0]["planid"] == "10001"
    empty = client.get("/api/policies",
                       params={"q": "数字经济", "area": "广东省"}).json()
    assert empty["matched_documents"] == 0


# ---- 非法参数 -----------------------------------------------------------
@pytest.mark.parametrize("params", [
    {"q": ""},
    {"q": "   "},
    {"q": "x" * 101},
    {"area": "x" * 51},
    {"page": 0},
    {"page_size": 0},
    {"page_size": 101},
    {"page": "abc"},
])
def test_invalid_parameters_are_rejected(client, params):
    response = client.get("/api/policies", params=params)
    assert response.status_code in (400, 422)
    assert "items" not in response.text


@pytest.mark.parametrize("path,params", [
    ("/api/policies", {"year": "2020"}),
    ("/api/policies", {"q": "数字经济", "date": "2024"}),
    ("/api/policies/10001", {"path": "/tmp/secret"}),
    ("/api/policies/10001/content", {"file": "../outside.md"}),
])
def test_unknown_query_parameters_are_rejected(client, path, params):
    response = client.get(path, params=params)
    assert response.status_code == 400
    assert "不支持的查询参数" in response.json()["detail"]


def test_catalog_unavailable_without_real_rag(bare_client):
    response = bare_client.get("/api/policies")
    assert response.status_code == 503
    assert "items" not in response.text


# ---- 任务四：详情与正文 ------------------------------------------------
def test_policy_detail(client):
    response = client.get("/api/policies/10001")
    assert response.status_code == 200
    body = response.json()
    assert body["dataset"]["generation_id"] == "generation-test"
    policy = body["policy"]
    assert policy["planid"] == "10001"
    assert policy["title"] == "北京市数字经济促进条例"
    assert policy["body_chars"] == len(BODY_1)
    assert [section["path"] for section in policy["sections"]] == ["第一章 总则", "第二章 支持措施"]
    assert policy["sections"][0]["start"] == 0


@pytest.mark.parametrize("planid", ["99999", "bad id", "x" * 65])
def test_policy_detail_errors(client, planid):
    response = client.get(f"/api/policies/{planid}")
    assert response.status_code in (400, 404)


def test_policy_content_pagination(client):
    first = client.get("/api/policies/10001/content", params={"offset": 0, "limit": 10}).json()
    assert first["text"] == BODY_1[:10]
    assert first["total_chars"] == len(BODY_1) and first["has_more"] is True
    tail = client.get("/api/policies/10001/content",
                      params={"offset": len(BODY_1) - 5, "limit": 10}).json()
    assert tail["text"] == BODY_1[-5:] and tail["has_more"] is False
    empty = client.get("/api/policies/10001/content",
                       params={"offset": len(BODY_1), "limit": 10}).json()
    assert empty["text"] == "" and empty["has_more"] is False


@pytest.mark.parametrize("params", [
    {"offset": -1}, {"limit": 0}, {"limit": 20001}, {"offset": 100000},
])
def test_policy_content_limits(client, params):
    response = client.get("/api/policies/10001/content", params=params)
    assert response.status_code in (400, 422)


def test_policy_content_unknown_planid(client):
    assert client.get("/api/policies/99999/content").status_code == 404


# ---- 冻结发布加载：版本隔离与安全校验 ------------------------------------
SCHEMA = """
CREATE TABLE runtime (generation TEXT, revision INTEGER);
CREATE TABLE releases (id TEXT, manifest TEXT, status TEXT);
CREATE TABLE documents (id TEXT, data TEXT, data_hash TEXT);
CREATE TABLE objects (document_id TEXT, kind TEXT, data TEXT, data_hash TEXT);
"""


def _write_snapshot(path, text):
    path.write_bytes(text.encode("utf-8"))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_project(tmp_path, *, extra_old_doc=False, tamper=False, traversal=False):
    project = tmp_path / "store"
    store = project / ".runtime" / "local"
    store.mkdir(parents=True)
    prepared = project / "prepared"
    prepared.mkdir()
    config = {"storage": {"metadata": "sqlite", "directory": ".runtime/local"}}
    (project / "config.json").write_text(json.dumps(config), encoding="utf-8")
    conn = sqlite3.connect(store / "metadata.sqlite3")
    try:
        conn.executescript(SCHEMA)
        docs = [("doc-1", "11111", "甲市数字经济规划", "甲市", "第十四个五年规划", "专题规划",
                 "甲市数字经济规划\n\n正文包含数字经济。"),
                ("doc-2", "22222", "乙市人才规划", "乙市", "第十四个五年规划", "发展规划纲要",
                 "乙市人才规划\n\n正文包含人才引进。")]
        old_docs = []
        if extra_old_doc:
            old_docs.append(("doc-old", "33333", "旧发布政策", "旧市", None, "其他",
                             "旧发布政策\n\n旧发布正文含数字经济。"))
        ids = []
        for index, (did, planid, title, area, fyp, plantype, text) in enumerate(docs):
            snapshot_rel = f"snapshots/{planid}.md"
            snapshot_path = prepared / snapshot_rel
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            source_hash = _write_snapshot(snapshot_path, text)
            row_data = {
                "id": did, "planid": planid, "body_start": len(title) + 2,
                "snapshot": ("../outside.md" if (tamper and index == 0) else snapshot_rel),
                "source_hash": source_hash,
                "metadata": {"title": title, "area_name": area, "fyp_name": fyp,
                             "plantype_label": plantype, "admin_level": "市级"},
            }
            if tamper and index == 0:
                row_data["source_hash"] = "0" * 64
            raw = json.dumps(row_data, ensure_ascii=False)
            conn.execute("INSERT INTO documents VALUES (?,?,?)",
                         (did, raw, hashlib.sha256(raw.encode()).hexdigest()))
            sections = [
                {"document_id": did, "id": f"root-{did}", "kind": "root", "level": 0,
                 "path": [], "title": None,
                 "start": row_data["body_start"], "end": len(text)},
                {"document_id": did, "id": f"sec-{did}", "kind": "section",
                 "level": 1, "path": ["第一章"], "title": "第一章",
                 "start": row_data["body_start"], "end": len(text)},
            ]
            for section in sections:
                sec_raw = json.dumps(section, ensure_ascii=False)
                conn.execute("INSERT INTO objects VALUES (?,?,?,?)",
                             (did, "structure", sec_raw,
                              hashlib.sha256(sec_raw.encode()).hexdigest()))
            ids.append(did)
        if extra_old_doc:
            for did, planid, title, area, fyp, plantype, text in old_docs:
                snapshot_rel = f"snapshots/{planid}.md"
                snapshot_path = prepared / snapshot_rel
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                source_hash = _write_snapshot(snapshot_path, text)
                row_data = {"id": did, "planid": planid, "body_start": len(title) + 2,
                            "snapshot": snapshot_rel, "source_hash": source_hash,
                            "metadata": {"title": title, "area_name": area, "fyp_name": fyp,
                                         "plantype_label": plantype}}
                raw = json.dumps(row_data, ensure_ascii=False)
                conn.execute("INSERT INTO documents VALUES (?,?,?)",
                             (did, raw, hashlib.sha256(raw.encode()).hexdigest()))
        (prepared / "documents.jsonl").write_text(
            "".join(json.dumps({"id": did}, ensure_ascii=False) + "\n" for did in ids),
            encoding="utf-8")
        manifest = {"prepared_path": str(prepared), "partial": False}
        conn.execute("INSERT INTO releases VALUES (?,?,?)",
                     ("generation-current", json.dumps(manifest), "ready"))
        if extra_old_doc:
            old_prepared = project / "prepared-old"
            old_prepared.mkdir()
            manifest_old = {"prepared_path": str(old_prepared), "partial": False}
            conn.execute("INSERT INTO releases VALUES (?,?,?)",
                         ("generation-old", json.dumps(manifest_old), "ready"))
        conn.execute("INSERT INTO runtime VALUES (?,?)", ("generation-current", 7))
        conn.commit()
    finally:
        conn.close()
    return project


def test_loader_reads_only_current_release(tmp_path):
    project = _make_project(tmp_path, extra_old_doc=True)
    snapshot = load_published_catalog(project)
    assert snapshot.generation_id == "generation-current"
    assert snapshot.revision == 7
    assert [record.planid for record in snapshot.records] == ["11111", "22222"]
    assert all(record.admin_level == "市级" for record in snapshot.records)
    assert [record.year for record in snapshot.records] == [2021, 2021]
    service = PolicyCatalogService(snapshot)
    listed = service.list_policies(q=None, area=None, fyp=None, plantype=None,
                                   page=1, page_size=20)["items"]
    assert listed[0]["year"] == service.get_policy("11111")["policy"]["year"] == 2021
    result = service.list_policies(q="数字经济", area=None, fyp=None, plantype=None,
                                   page=1, page_size=20)
    assert result["matched_documents"] == 1
    assert result["items"][0]["planid"] == "11111"


def test_loader_verifies_snapshot_hash(tmp_path):
    project = _make_project(tmp_path, tamper=True)
    with pytest.raises(CatalogUnavailable):
        load_published_catalog(project)


def test_loader_rejects_missing_config(tmp_path):
    with pytest.raises(CatalogUnavailable):
        load_published_catalog(tmp_path / "absent")


def test_loader_rejects_tampered_structure_hash(tmp_path):
    project = _make_project(tmp_path)
    database = project / ".runtime" / "local" / "metadata.sqlite3"
    conn = sqlite3.connect(database)
    try:
        conn.execute("UPDATE objects SET data=? WHERE kind='structure'",
                     ('{"document_id":"doc-1","kind":"section","start":0,"end":1}',))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CatalogUnavailable):
        load_published_catalog(project)


def test_loader_keeps_section_offsets_relative_to_body(tmp_path):
    project = _make_project(tmp_path)
    service = PolicyCatalogService(load_published_catalog(project))
    detail = service.get_policy("11111")
    sections = detail["policy"]["sections"]
    # 全文根节点（kind=root，空 path）不进入章节目录
    assert len(sections) == 1 and sections[0]["path"] == "第一章"
    assert sections[0]["start"] == 0
    assert sections[0]["end"] == len(service.snapshot.records[0].body)


def test_loader_does_not_expose_filesystem_paths(tmp_path):
    project = _make_project(tmp_path)
    service = PolicyCatalogService(load_published_catalog(project))
    payload = json.dumps({"list": service.list_policies(q=None, area=None, fyp=None,
                                                        plantype=None, page=1, page_size=20),
                          "detail": service.get_policy("11111")},
                         ensure_ascii=False)
    assert str(project) not in payload
    assert "snapshots" not in payload and "prepared" not in payload
