"""政策目录 Facets 与地区树的契约测试，使用小型、明确的内存快照。"""
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from guance_agent.config import Settings

from app import main as api
from app import policy_app as standalone_api
from app.policy_catalog import CatalogSnapshot, PolicyCatalogService, PolicyRecord
from app.policy_regions import RegionResourceUnavailable, load_region_tree

REGION_VERSION = "sha256:" + "a" * 64
MAPPING_VERSION = "sha256:" + "b" * 64


def _record(planid, title, area, fyp, plantype, admin_level, body="正文数字经济"):
    return PolicyRecord(
        planid=planid,
        title=title,
        area=area,
        fyp=fyp,
        plantype_label=plantype,
        document_id=f"doc-{planid}",
        body=body,
        sections=(),
        admin_level=admin_level,
    )


def _regions():
    return {
        "region_version": REGION_VERSION,
        "artifact_sha256": "c" * 64,
        "source": "fixture",
        "source_url": "https://example.invalid/regions",
        "retrieved_at_utc": "2026-10-31T00:00:00Z",
        "snapshot_as_of": None,
        "official_version": None,
        "node_count": 9,
        "data": [
            {
                "code": "110000000000",
                "name": "北京市",
                "source_type": "直辖市",
                "tier": "province",
                "children": [
                    {"code": "110101000000", "name": "东城区", "source_type": "市辖区",
                     "tier": "county", "children": []},
                    {"code": "110102000000", "name": "西城区", "source_type": "市辖区",
                     "tier": "county", "children": []},
                ],
            },
            {
                "code": "130000000000",
                "name": "河北省",
                "source_type": "省",
                "tier": "province",
                "children": [
                    {
                        "code": "130100000000",
                        "name": "石家庄市",
                        "source_type": "地级市",
                        "tier": "prefecture",
                        "children": [
                            {"code": "130102000000", "name": "长安区", "source_type": "市辖区",
                             "tier": "county", "children": []},
                        ],
                    },
                ],
            },
            {
                "code": "460000000000",
                "name": "海南省",
                "source_type": "省",
                "tier": "province",
                "children": [
                    {"code": "469001000000", "name": "五指山市", "source_type": "县级市",
                     "tier": "county", "children": []},
                    {"code": "469002000000", "name": "琼海市", "source_type": "县级市",
                     "tier": "county", "children": []},
                ],
            },
        ],
    }


def _snapshot():
    records = (
        _record("p1", "数字经济促进政策2021年", "北京市", "第十四个五年规划", "专项规划", "区县级"),
        _record("p2", "数字经济养老政策2020年", "北京市", "政府工作报告", "总体规划纲要", "县级"),
        _record("p3", "数字经济县级政策", "河北省", None, None, "县级"),
        _record("p4", "数字经济未映射政策", "未知地区", None, "其他", "县级"),
        _record("p5", "数字经济国家政策2021年", "全国", "第十四个五年规划", "专项规划", "国家级"),
        _record("p6", "非关键词省级政策2020年", "河北省", "第十三个五年规划", "其他", "省级",
                body="正文不含检索词"),
        _record("p7", "数字经济五指山政策2019年", "五指山市", "政府工作报告", "其他", "区县级"),
        _record("p8", "非关键词十四五政策2020年", "北京市", "第十四个五年规划", "其他", "区县级",
                body="正文不含检索词"),
    )
    return CatalogSnapshot("generation-test", 3, True, records)


def _mapping():
    return {
        "p1": {"region_code": "110101000000", "region_status": "mapped"},
        "p2": {"region_code": "110102000000", "region_status": "mapped"},
        "p3": {"region_code": "130102000000", "region_status": "mapped"},
        "p4": {"region_code": None, "region_status": "unclassified"},
        "p5": {"region_code": None, "region_status": "not_applicable"},
        "p6": {"region_code": "130000000000", "region_status": "mapped"},
        "p7": {"region_code": "469001000000", "region_status": "mapped"},
        "p8": {"region_code": "110101000000", "region_status": "mapped"},
    }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "warm_up", lambda _settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)
    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    app = api.create_app(settings=Settings(kb_backend="rag", rag_store_project="unused"))
    app.state.policy_catalog = PolicyCatalogService(
        _snapshot(), region_payload=_regions(), region_mapping=_mapping(),
        region_mapping_version=MAPPING_VERSION,
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def no_mapping_client(monkeypatch):
    monkeypatch.setattr(api, "warm_up", lambda _settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)
    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    app = api.create_app(settings=Settings(kb_backend="rag", rag_store_project="unused"))
    app.state.policy_catalog = PolicyCatalogService(_snapshot(), region_payload=_regions())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def no_rag_regions_client(monkeypatch):
    monkeypatch.setattr(api, "warm_up", lambda _settings: None)
    monkeypatch.setattr(api, "close_kb", lambda: None)
    monkeypatch.setattr(api, "aclose_client", AsyncMock())
    app = api.create_app(settings=Settings())
    app.state.policy_regions = _regions()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def standalone_client(monkeypatch):
    monkeypatch.delenv("RAG_STORE_PROJECT", raising=False)
    with TestClient(standalone_api.app) as test_client:
        yield test_client


def _counts(options, key="value"):
    return {option[key]: option["count"] for option in options}


def test_regions_keeps_direct_children_and_zero_document_nodes(client):
    response = client.get("/api/policies/regions")
    assert response.status_code == 200
    body = response.json()
    assert body["region_version"] == REGION_VERSION
    assert body["node_count"] == 9
    by_code = {province["code"]: province for province in body["data"]}
    assert [node["name"] for node in by_code["110000000000"]["children"]] == ["东城区", "西城区"]
    assert by_code["460000000000"]["children"][0]["code"] == "469001000000"
    assert len({node["code"] for province in body["data"]
                for node in [province, *province["children"],
                               *(child for parent in province["children"]
                               for child in parent["children"]) ]}) == 9

    facets = client.get("/api/policies/facets").json()["facets"]["admin_level"]["regions"]
    province_counts = _counts(facets["province_options"], "code")
    assert province_counts["460000000000"] == 1
    assert province_counts["110000000000"] == 3
    assert province_counts["130000000000"] == 2
    hainan = client.get("/api/policies/facets", params={
        "province_code": "460000000000"}).json()["facets"]["admin_level"]["regions"]
    county_counts = _counts(hainan["county_options"], "code")
    assert county_counts["469001000000"] == 1
    assert county_counts["469002000000"] == 0


def test_region_tree_does_not_require_a_policy_catalog(no_rag_regions_client):
    regions = no_rag_regions_client.get("/api/policies/regions")
    assert regions.status_code == 200
    assert regions.json()["node_count"] == 9
    assert no_rag_regions_client.get("/api/policies").status_code == 503


def test_standalone_health_reports_catalog_unavailable_without_rag(standalone_client):
    response = standalone_client.get("/api/health")
    assert response.status_code == 503
    assert "status" not in response.json()


def test_standalone_health_and_regions_work_from_injected_read_only_catalog(standalone_client):
    standalone_api.app.state.policy_catalog = PolicyCatalogService(
        _snapshot(), region_payload=_regions(), region_mapping=_mapping(),
        region_mapping_version=MAPPING_VERSION,
    )
    health = standalone_client.get("/api/health")
    regions = standalone_client.get("/api/policies/regions")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok", "service": "policy-catalog-api",
        "generation_id": "generation-test", "revision": 3,
        "total_documents": 8, "region_mapping_ready": True,
    }
    assert regions.status_code == 200
    assert regions.json()["node_count"] == 9


def test_region_tree_version_pin_and_policy_versions(client):
    first = client.get("/api/policies/facets")
    assert first.status_code == 200
    dataset = first.json()["dataset"]
    pins = {name: dataset[name] for name in
            ("generation_id", "revision", "region_version", "region_mapping_version")}
    assert client.get("/api/policies", params=pins).status_code == 200
    assert client.get("/api/policies/regions", params={"region_version": REGION_VERSION}).status_code == 200

    for name, changed in (("generation_id", "other-generation"),
                          ("revision", 4),
                          ("region_version", "sha256:" + "d" * 64),
                          ("region_mapping_version", "sha256:" + "e" * 64)):
        stale = {**pins, name: changed}
        response = client.get("/api/policies/facets", params=stale)
        assert response.status_code == 409
        assert response.json()["current"] == pins

    mismatch = client.get("/api/policies/regions", params={"region_version": "sha256:" + "f" * 64})
    assert mismatch.status_code == 409
    assert mismatch.json()["current"]["region_version"] == REGION_VERSION


def test_policy_filters_keep_area_exact_and_complete_region_chain(client):
    exact_area = client.get("/api/policies", params={"area": "河北省"}).json()
    assert exact_area["matched_documents"] == 2
    assert {item["planid"] for item in exact_area["items"]} == {"p3", "p6"}

    by_county = client.get("/api/policies", params={"county_code": "130102000000"})
    assert by_county.status_code == 200
    body = by_county.json()
    assert body["matched_documents"] == 1
    assert body["items"][0]["planid"] == "p3"
    assert body["query"]["province_code"] == "130000000000"
    assert body["query"]["prefecture_code"] == "130100000000"
    assert body["query"]["county_code"] == "130102000000"

    exact_and_tree = client.get("/api/policies", params={
        "area": "河北省", "county_code": "130102000000"})
    assert exact_and_tree.json()["matched_documents"] == 1
    no_subtree_alias = client.get("/api/policies", params={
        "area": "石家庄市", "province_code": "130000000000"})
    assert no_subtree_alias.json()["matched_documents"] == 0

    direct_county = client.get("/api/policies", params={"county_code": "469001000000"}).json()
    assert direct_county["matched_documents"] == 1
    assert direct_county["query"]["prefecture_code"] is None


def test_facets_self_exclusion_keeps_year_and_fyp_independent(client):
    response = client.get("/api/policies/facets", params={
        "year": "2021", "fyp": "第十四个五年规划"})
    assert response.status_code == 200
    facets = response.json()["facets"]
    year_counts = _counts(facets["year"]["options"])
    assert year_counts[2021] == 2
    assert year_counts[2020] == 1

    fyp_counts = _counts(facets["fyp"]["options"])
    assert fyp_counts["第十四个五年规划"] == 2
    assert facets["fyp"]["unclassified"]["count"] == 0


def test_keyword_unique_counts_and_missing_values_are_faceted_separately(client):
    response = client.get("/api/policies/facets", params={"q": "数字经济", "year": "unknown"})
    assert response.status_code == 200
    body = response.json()
    assert body["facets"]["year"]["options"][-1] == {
        "value": "unknown", "label": "年份未知", "count": 2}
    assert body["facets"]["fyp"]["unclassified"]["count"] == 2
    assert body["facets"]["plantype"]["unclassified"]["count"] == 1

    filtered = client.get("/api/policies", params={
        "q": "数字经济", "fyp_status": "unclassified"}).json()
    assert filtered["matched_documents"] == 2
    assert {item["planid"] for item in filtered["items"]} == {"p3", "p4"}
    assert filtered["query"]["fyp"] is None
    assert filtered["query"]["fyp_status"] == "unclassified"

    missing_type = client.get("/api/policies", params={"plantype_status": "unclassified"}).json()
    assert missing_type["matched_documents"] == 1
    assert missing_type["items"][0]["planid"] == "p3"

    region_unclassified = client.get("/api/policies", params={
        "q": "数字经济", "admin_level": "county", "region_status": "unclassified"}).json()
    assert region_unclassified["matched_documents"] == 1
    assert region_unclassified["items"][0]["planid"] == "p4"
    national = client.get("/api/policies", params={
        "admin_level": "national", "region_status": "not_applicable"}).json()
    assert national["matched_documents"] == 1
    assert national["items"][0]["planid"] == "p5"


def test_facet_counts_use_keyword_and_ignore_pagination(client):
    response = client.get("/api/policies/facets", params={"q": "数字经济"})
    assert response.status_code == 200
    facets = response.json()["facets"]
    assert sum(item["count"] for item in facets["admin_level"]["options"]) == 6
    assert sum(item["count"] for item in facets["year"]["options"]) == 6

    first = client.get("/api/policies", params={"q": "数字经济", "page_size": 1}).json()
    all_items = client.get("/api/policies", params={"q": "数字经济", "page_size": 100}).json()
    assert first["matched_documents"] == all_items["matched_documents"] == 6
    assert len(first["items"]) == 1 and len(all_items["items"]) == 6


def test_invalid_region_combinations_and_repeated_parameters_are_400(client):
    conflicts = (
        {"province_code": "110000000000", "prefecture_code": "130100000000"},
        {"admin_level": "national", "province_code": "110000000000"},
        {"county_code": "999999999999"},
        {"region_status": "unclassified", "county_code": "110101000000"},
        {"year": "20xx"},
        {"year": "2100"},
        {"fyp": "十四五", "fyp_status": "unclassified"},
    )
    for params in conflicts:
        response = client.get("/api/policies", params=params)
        assert response.status_code == 400, params

    duplicate = client.get("/api/policies/facets", params=[("year", "2020"), ("year", "2021")])
    assert duplicate.status_code == 400
    unknown = client.get("/api/policies/facets", params={"page": 1})
    assert unknown.status_code == 400
    invalid_numeric = client.get("/api/policies", params={"revision": "not-a-number"})
    assert invalid_numeric.status_code == 422


def test_version_pins_require_complete_group_and_return_contract_errors(client):
    partial = client.get("/api/policies/facets", params={"generation_id": "generation-test"})
    assert partial.status_code == 400
    malformed = client.get("/api/policies", params={
        "generation_id": "generation-test", "revision": 3,
        "region_version": "bad", "region_mapping_version": MAPPING_VERSION})
    assert malformed.status_code == 400
    invalid_revision = client.get("/api/policies/facets", params={
        "generation_id": "generation-test", "revision": 0,
        "region_version": REGION_VERSION, "region_mapping_version": MAPPING_VERSION})
    assert invalid_revision.status_code == 422

    wrong_generation = client.get("/api/policies", params={
        "generation_id": "other-generation", "revision": 3,
        "region_version": REGION_VERSION, "region_mapping_version": MAPPING_VERSION})
    assert wrong_generation.status_code == 409
    wrong_generation_bad_revision = client.get("/api/policies/facets", params={
        "generation_id": "other-generation", "revision": "1.0",
        "region_version": REGION_VERSION, "region_mapping_version": MAPPING_VERSION})
    assert wrong_generation_bad_revision.status_code in {400, 422}


def test_region_loader_rejects_malformed_node_instead_of_leaking_key_error(tmp_path):
    resource_dir = tmp_path / "regions"
    resource_dir.mkdir()
    (resource_dir / "mainland-province-prefecture-county.json").write_text(
        json.dumps({"data": [{"code": "110000000000", "name": "北京市",
                              "source_type": "直辖市", "tier": "province"}]}),
        encoding="utf-8",
    )
    (resource_dir / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RegionResourceUnavailable):
        load_region_tree(resource_dir)


def test_mapping_unavailable_does_not_break_old_list_but_blocks_facets(no_mapping_client):
    regions = no_mapping_client.get("/api/policies/regions")
    assert regions.status_code == 200
    listing = no_mapping_client.get("/api/policies")
    assert listing.status_code == 200
    assert listing.json()["dataset"]["region_mapping_version"] is None
    assert all(item["region_code"] is None and item["region_status"] is None
               for item in listing.json()["items"])
    facets = no_mapping_client.get("/api/policies/facets")
    assert facets.status_code == 503
    region_filter = no_mapping_client.get("/api/policies", params={
        "province_code": "110000000000"})
    assert region_filter.status_code == 503
