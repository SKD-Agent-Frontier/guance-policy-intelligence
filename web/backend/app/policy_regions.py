"""Validated loaders for the immutable administrative tree and reviewed policy map."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .policy_catalog import CatalogSnapshot, CatalogUnavailable

RESOURCE_DIR = Path(__file__).parent / "resources" / "regions"
REGION_VERSION = "sha256:94e2bdbeb629a4e523a1c0581f8d234844a81a36066ade59f06e08eb143e9cdb"
REGION_ARTIFACT_SHA256 = "64d2152a679749298436d1806a9256e146ee0c94a85203ceeb70471ce634c446"
SOURCE_TYPE_TIERS = {
    "省": "province", "自治区": "province", "直辖市": "province",
    "地级市": "prefecture", "地区": "prefecture", "自治州": "prefecture", "盟": "prefecture",
    "市辖区": "county", "县": "county", "自治县": "county", "县级市": "county",
    "旗": "county", "自治旗": "county", "特区": "county", "林区": "county",
}
REGION_CODE = re.compile(r"\d{12}\Z")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


class RegionResourceUnavailable(CatalogUnavailable):
    """A required region artifact is absent or failed integrity validation."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _walk(nodes: list[dict], parent: str | None = None):
    for node in nodes:
        yield node, parent
        yield from _walk(node["children"], node["code"])


def load_region_tree(resource_dir: str | Path | None = None) -> dict:
    """Load and validate the complete tree; never fetch or synthesize data."""
    directory = Path(resource_dir) if resource_dir is not None else RESOURCE_DIR
    tree_path = directory / "mainland-province-prefecture-county.json"
    manifest_path = directory / "manifest.json"
    try:
        payload = json.loads(tree_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegionResourceUnavailable("地区树资源不可用") from exc
    if (not isinstance(payload, dict) or not isinstance(payload.get("data"), list)
            or not isinstance(manifest, dict)):
        raise RegionResourceUnavailable("地区树资源格式无效")
    raw_bytes = tree_path.read_bytes()
    artifact_sha = hashlib.sha256(raw_bytes).hexdigest()
    expected_artifact = manifest.get("filtered_tree_sha256", manifest.get("artifact_sha256"))
    if expected_artifact != REGION_ARTIFACT_SHA256 or artifact_sha != expected_artifact:
        raise RegionResourceUnavailable("地区树资源校验失败")
    source_nodes = payload["data"]
    def normalize_node(node: dict) -> dict:
        if not isinstance(node, dict) or not isinstance(node.get("children"), list):
            raise RegionResourceUnavailable("地区树结构无效")
        source_type = node.get("source_type", node.get("type"))
        if not isinstance(source_type, str):
            raise RegionResourceUnavailable("地区树来源类型无效")
        return {"code": node.get("code"), "name": node.get("name"),
                "source_type": source_type,
                "tier": SOURCE_TYPE_TIERS.get(source_type, "unknown"),
                "children": [normalize_node(child) for child in node.get("children", [])]}
    try:
        nodes = [normalize_node(node) for node in source_nodes]
    except (AttributeError, TypeError) as exc:
        raise RegionResourceUnavailable("地区树结构无效") from exc
    flattened = list(_walk(nodes))
    codes: set[str] = set()
    for node, _parent in flattened:
        if (not isinstance(node, dict) or not REGION_CODE.fullmatch(str(node.get("code", "")))
                or node["code"] in codes or not isinstance(node.get("name"), str)
                or not isinstance(node.get("children"), list)):
            raise RegionResourceUnavailable("地区树节点无效")
        codes.add(node["code"])
        tier = SOURCE_TYPE_TIERS.get(node.get("source_type"), "unknown")
        if node.get("tier") != tier:
            raise RegionResourceUnavailable("地区树层级映射无效")
    version = "sha256:" + hashlib.sha256(_canonical(nodes)).hexdigest()
    if version != REGION_VERSION or manifest.get("region_version") != version:
        raise RegionResourceUnavailable("地区树版本校验失败")
    counts = manifest.get("node_counts", {})
    expected_count = counts.get("total", manifest.get("node_count"))
    tier_counts = {tier: sum(node["tier"] == tier for node, _ in flattened)
                   for tier in ("province", "prefecture", "county")}
    if (expected_count != 3211 or len(flattened) != 3211
            or tier_counts != {"province": 31, "prefecture": 333, "county": 2847}
            or manifest.get("canonical_bytes") != len(_canonical(nodes))):
        raise RegionResourceUnavailable("地区树节点数量校验失败")
    retrieved = manifest.get("retrieved_at_utc", payload.get("source_response_retrieved_at"))
    return {
        "region_version": version,
        "artifact_sha256": artifact_sha,
        "source": manifest.get("source", "国家地名信息库"),
        "source_url": manifest.get("source_url", "https://dmfw.mca.gov.cn/9095/xzqh/getList?code=0&maxLevel=3"),
        "retrieved_at_utc": retrieved,
        "snapshot_as_of": manifest.get("snapshot_as_of"),
        "official_version": manifest.get("official_version"),
        "node_count": len(flattened),
        "data": nodes,
    }


def load_region_mapping(
    resource_dir: str | Path | None, snapshot: CatalogSnapshot, region_payload: dict | None = None,
) -> tuple[dict[str, dict[str, str | None]], str] | None:
    """Load only a reviewed map bound to this exact catalog generation/revision."""
    directory = Path(resource_dir) if resource_dir is not None else RESOURCE_DIR
    mapping_path = directory / "policy-region-map.json"
    manifest_path = directory / "manifest.json"
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(mapping, dict) or not isinstance(manifest, dict):
        return None
    if manifest.get("mapping_status") != "approved":
        return None
    if (manifest.get("generation_id") != snapshot.generation_id
            or manifest.get("revision") != snapshot.revision):
        return None
    if region_payload is None or manifest.get("region_version") != region_payload.get("region_version"):
        return None
    if not isinstance(mapping.get("entries"), list):
        return None
    if not isinstance(mapping.get("algorithm_version"), str) or not mapping["algorithm_version"]:
        return None
    canonical = {"algorithm_version": mapping.get("algorithm_version"),
                 "entries": mapping["entries"]}
    version = "sha256:" + hashlib.sha256(_canonical(canonical)).hexdigest()
    if not SHA256.fullmatch(version) or manifest.get("region_mapping_version") != version:
        return None
    expected_planids = {record.planid for record in snapshot.records}
    records_by_planid = {record.planid: record for record in snapshot.records}
    nodes = ({node["code"]: node for node, _ in _walk(region_payload["data"])}
             if region_payload is not None else {})
    entries: dict[str, dict[str, str | None]] = {}
    previous = ""
    for item in mapping["entries"]:
        if not isinstance(item, dict):
            return None
        planid, code, status = item.get("planid"), item.get("region_code"), item.get("region_status")
        record = records_by_planid.get(planid) if isinstance(planid, str) else None
        if (not isinstance(planid, str) or planid <= previous or record is None
                or status not in ("mapped", "unclassified", "not_applicable")
                or (status == "mapped") != (isinstance(code, str) and REGION_CODE.fullmatch(code) is not None)
                or (status != "mapped" and code is not None)):
            return None
        if record is not None:
            admin = {"国家": "national", "国家级": "national", "省": "province", "省级": "province",
                     "市": "prefecture", "市级": "prefecture", "区县": "county", "区县级": "county",
                     "县": "county", "县级": "county"}.get(
                         record.admin_level if isinstance(record.admin_level, str) else "",
                         "unclassified")
            if (admin == "national") != (status == "not_applicable"):
                return None
            if status == "mapped":
                expected_tier = admin
                if code not in nodes or nodes[code]["tier"] != expected_tier:
                    return None
        previous = planid
        entries[planid] = {"region_code": code, "region_status": status}
    if set(entries) != expected_planids:
        return None
    return entries, version
