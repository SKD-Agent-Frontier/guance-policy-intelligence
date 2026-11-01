#!/usr/bin/env python3
"""Prepare local, reproducible mainland region and policy mapping review resources.

This command is offline and read-only with respect to its source files. It does
not fetch data, modify the RAG catalog, or approve candidate mappings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

EXPECTED_TREE_SHA256 = "64d2152a679749298436d1806a9256e146ee0c94a85203ceeb70471ce634c446"
EXPECTED_RAW_SHA256 = "2e50f7fb652571132487508e0dd9c8fae1f141e63adaab46fec7389447180739"
EXPECTED_REGION_VERSION = "sha256:94e2bdbeb629a4e523a1c0581f8d234844a81a36066ade59f06e08eb143e9cdb"
EXPECTED_CANONICAL_BYTES = 313818
GENERATION_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
CODE_RE = re.compile(r"\d{12}\Z")
PLANID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
ADMIN_LEVELS = {
    "国家": "national", "国家级": "national",
    "省": "province", "省级": "province",
    "市": "prefecture", "市级": "prefecture",
    "区县": "county", "区县级": "county", "县": "county", "县级": "county",
}
SOURCE_TYPE_TIERS = {
    "省": "province", "自治区": "province", "直辖市": "province",
    "地级市": "prefecture", "地区": "prefecture", "自治州": "prefecture", "盟": "prefecture",
    "市辖区": "county", "县": "county", "自治县": "county", "县级市": "county",
    "旗": "county", "自治旗": "county", "特区": "county", "林区": "county",
}
GENERIC_PARENTS = {"市辖区", "省直辖县级行政区划", "自治区直辖县级行政区划"}
EXCLUDED_ROOT_CODES = {"810000000000", "820000000000", "资料暂缺"}
SOURCE_URL = "https://dmfw.mca.gov.cn/9095/xzqh/getList?code=0&maxLevel=3"
RETRIEVED_AT_UTC = "2026-10-31T06:49:42.361Z"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> bytes:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(payload)
    return payload


def filter_raw_response(raw_path: Path) -> bytes:
    raw_bytes = raw_path.read_bytes()
    if sha256(raw_bytes) != EXPECTED_RAW_SHA256:
        raise ValueError("官方原始响应 SHA 不匹配")
    response = json.loads(raw_bytes.decode("utf-8"))
    root = response.get("data")
    if not isinstance(root, dict) or root.get("level") != 0 or not isinstance(root.get("children"), list):
        raise ValueError("官方原始响应根结构无效")
    mainland = [node for node in root["children"] if node.get("code") not in EXCLUDED_ROOT_CODES]
    if len(mainland) != 31 or any(node.get("level") != 1 for node in mainland):
        raise ValueError("按官方根代码排除港澳与‘资料暂缺’台湾节点后，省级节点数不是31")
    payload = {"source": "国家地名信息库", "source_url": SOURCE_URL,
               "source_response_retrieved_at": RETRIEVED_AT_UTC,
               "snapshot_as_of": None, "maxLevel": 3, "data": mainland}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2).replace("\n", "\r\n").encode("utf-8")
    if sha256(serialized) != EXPECTED_TREE_SHA256:
        raise ValueError("由原始响应重建的过滤树 SHA 与契约基线不符")
    return serialized


def read_tree(raw_bytes: bytes) -> tuple[list[dict], dict]:
    artifact_sha = sha256(raw_bytes)
    if artifact_sha != EXPECTED_TREE_SHA256:
        raise ValueError(f"地区树工件 SHA 不匹配: {artifact_sha}")
    source = json.loads(raw_bytes.decode("utf-8"))
    if source.get("source") != "国家地名信息库" or source.get("maxLevel") != 3:
        raise ValueError("地区树来源或请求层级与契约记录不符")
    raw_nodes = source.get("data")
    if not isinstance(raw_nodes, list):
        raise ValueError("地区树 data 必须是数组")
    transformed: list[dict] = []
    flat: list[dict] = []

    def convert(nodes: list[dict], ancestors: list[dict]) -> list[dict]:
        result = []
        for item in nodes:
            code = item.get("code")
            name = item.get("name")
            source_type = item.get("type")
            tier = SOURCE_TYPE_TIERS.get(source_type, "unknown")
            if not isinstance(code, str) or not CODE_RE.fullmatch(code) or not isinstance(name, str):
                raise ValueError("地区树节点缺少有效12位代码或名称")
            if tier == "unknown":
                raise ValueError(f"未登记的官方 source_type: {source_type!r}")
            node = {"code": code, "name": name, "source_type": source_type,
                    "tier": tier, "children": []}
            flat.append({**node, "ancestors": ancestors.copy()})
            parent_chain = [*ancestors, {k: node[k] for k in ("code", "name", "source_type", "tier")}]
            node["children"] = convert(item.get("children", []), parent_chain)
            result.append(node)
        return result

    transformed = convert(raw_nodes, [])
    if len(flat) != 3211 or len({n["code"] for n in flat}) != 3211:
        raise ValueError(f"地区树节点数或代码唯一性异常: {len(flat)}")
    tier_counts = Counter(n["tier"] for n in flat)
    if tier_counts != Counter({"province": 31, "prefecture": 333, "county": 2847}):
        raise ValueError(f"地区树层级计数与契约不符: {dict(tier_counts)}")
    canonical = canonical_bytes(transformed)
    version = "sha256:" + sha256(canonical)
    if version != EXPECTED_REGION_VERSION or len(canonical) != EXPECTED_CANONICAL_BYTES:
        raise ValueError(f"地区树 canonical 校验失败: version={version}, bytes={len(canonical)}")
    indexed = []

    def index(nodes: list[dict], parent: dict | None = None, ancestors: list[dict] | None = None):
        ancestors = ancestors or []
        for node in nodes:
            indexed.append({**node, "parent": parent,
                            "ancestors": ancestors.copy()})
            chain_item = {k: node[k] for k in ("code", "name", "source_type", "tier")}
            index(node["children"], node, [*ancestors, chain_item])

    index(transformed)
    metadata = {
        "source": source["source"],
        "source_url": source["source_url"],
        "retrieved_at_utc": source["source_response_retrieved_at"],
        "snapshot_as_of": source.get("snapshot_as_of"),
        "official_version": None,
        "request_method": "GET",
        "request_parameters": {"code": "0", "maxLevel": 3},
        "scope": "中国大陆31个省级行政区；保留源返回的省、地、县节点及实际父子关系；不含乡镇及港澳台",
        "source_artifact_sha256": artifact_sha,
        "region_version": version,
        "canonical_bytes": len(canonical),
        "node_counts": {"province": tier_counts["province"],
                         "prefecture": tier_counts["prefecture"],
                         "county": tier_counts["county"], "total": len(flat)},
    }
    return indexed, metadata


def open_catalog(db_path: Path, snapshot_manifest_path: Path) -> tuple[dict, list[dict]]:
    uri = "file:" + db_path.resolve().as_posix() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only = ON")
        db.execute("BEGIN")
        runtime = db.execute("SELECT generation, revision FROM runtime WHERE singleton=1").fetchone()
        if not runtime:
            raise ValueError("runtime 发布指针缺失")
        generation, revision = runtime
        if not isinstance(generation, str) or not GENERATION_RE.fullmatch(generation) or revision < 1:
            raise ValueError("runtime generation/revision 格式无效")
        release_row = db.execute(
            "SELECT status, manifest FROM releases WHERE id=?", (generation,)
        ).fetchone()
        if not release_row or release_row[0] != "ready":
            raise ValueError("runtime 指向的 release 未 ready")
        release = json.loads(release_row[1])
        if release.get("generation_id") != generation:
            raise ValueError("runtime 与 release manifest generation 不一致")
        rows = []
        seen = set()
        source_ids = set()
        for document_id, planid, data_text, data_hash in db.execute(
                "SELECT id, planid, data, data_hash FROM documents ORDER BY planid"):
            if sha256(data_text.encode("utf-8")) != data_hash:
                raise ValueError(f"源文档 data_hash 不匹配: {planid}")
            data = json.loads(data_text)
            if data.get("planid") != planid or not isinstance(data.get("metadata"), dict):
                raise ValueError(f"文档身份或 metadata 无效: {planid}")
            if planid in seen:
                raise ValueError(f"重复 planid: {planid}")
            seen.add(planid)
            if document_id in source_ids:
                raise ValueError(f"重复 source document id: {planid}")
            source_ids.add(document_id)
            rows.append({"planid": planid, "data_hash": data_hash,
                         "metadata": data["metadata"]})
        snapshot_manifest = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))
        expected_count = snapshot_manifest.get("counts", {}).get("documents")
        if (snapshot_manifest.get("generation_id") != generation
                or snapshot_manifest.get("revision") != revision
                or snapshot_manifest.get("partial") != bool(release.get("partial"))
                or len(rows) != expected_count):
            raise ValueError("runtime、发布清单及完整目录文档集合身份不一致")
        binding = {"generation_id": generation, "revision": revision,
                   "partial": bool(release.get("partial")),
                   "total_documents": len(rows),
                   "release_status": release_row[0],
                   "profile_id": release.get("profile_id"),
                   "collection": release.get("collection"),
                   "prepared_id": release.get("prepared_id"),
                   "source_document_ids_sha256": sha256(canonical_bytes(sorted(source_ids)))}
    return binding, rows


def validate_prepared_documents(path: Path, binding: dict) -> str:
    expected_ids = set()
    for line_number, line in enumerate(path.open("r", encoding="utf-8"), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        document_id = item.get("id")
        if not isinstance(document_id, str) or document_id in expected_ids:
            raise ValueError(f"prepared documents.jsonl id 无效或重复，第{line_number}行")
        expected_ids.add(document_id)
    ids_sha = sha256(canonical_bytes(sorted(expected_ids)))
    if len(expected_ids) != binding["total_documents"] or ids_sha != binding["source_document_ids_sha256"]:
        raise ValueError("prepared documents.jsonl 与活动目录 source document id 集合不一致")
    file_hash = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            file_hash.update(chunk)
    return file_hash.hexdigest()


def candidate_evidence(records: list[dict], nodes: list[dict]) -> tuple[dict, dict]:
    by_tier_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for node in nodes:
        by_tier_name[(node["tier"], node["name"])].append(node)
    entries = []
    counts = Counter()
    unmapped_reasons: dict[str, list[str]] = defaultdict(list)
    for record in records:
        metadata = record["metadata"]
        level_raw = metadata.get("admin_level")
        admin = ADMIN_LEVELS.get(level_raw)
        area = metadata.get("area_name")
        parent_name = metadata.get("parent_area_name")
        basis = {key: metadata.get(key) for key in ("admin_level", "area_name", "parent_area_name")}
        basis_sha = sha256(canonical_bytes(basis))
        base = {"planid": record["planid"], "source_data_hash": record["data_hash"],
                "metadata_basis": basis, "metadata_basis_sha256": basis_sha,
                "admin_level": admin, "candidate_nodes": []}
        if admin == "national":
            counts["national"] += 1
            entries.append({**base, "review_state": "not_applicable",
                            "region_status_if_approved": "not_applicable",
                            "candidate_region_code": None,
                            "evidence_rule": "admin_level 明确为国家级；不属于地区树，parent_area_name 不参与地区匹配。"})
            continue
        if admin not in {"province", "prefecture", "county"}:
            reason = "admin_level_unknown"
            counts[reason] += 1
            unmapped_reasons[reason].append(record["planid"])
            entries.append({**base, "review_state": "unclassified", "reason": reason,
                            "region_status_if_approved": "unclassified",
                            "candidate_region_code": None})
            continue
        if admin == "province":
            tiers = ["province"]
        elif admin == "prefecture":
            tiers = ["prefecture", "province"]
        else:
            tiers = ["county"]
        name_nodes = [n for tier in tiers for n in by_tier_name.get((tier, area), [])
                      if not (admin == "prefecture" and n["tier"] == "province"
                              and n["source_type"] != "直辖市")]
        for node in name_nodes:
            parent = node["parent"]
            direct_parent_name = parent["name"] if parent else "中华人民共和国"
            direct_parent_code = parent["code"] if parent else None
            expected_tier = {"province": "province", "prefecture": "prefecture",
                             "county": "county"}[admin]
            base["candidate_nodes"].append({
                "code": node["code"], "name": node["name"], "tier": node["tier"],
                "expected_tier": expected_tier,
                "tier_exact": node["tier"] == expected_tier,
                "municipality_exception": admin == "prefecture"
                    and node["tier"] == "province" and node["source_type"] == "直辖市",
                "source_type": node["source_type"], "direct_parent_code": direct_parent_code,
                "direct_parent_name": direct_parent_name,
                "parent_name_exact": parent_name == direct_parent_name,
                "ancestors": node["ancestors"],
                "hierarchy_rule": "直接父节点名称必须与 metadata.parent_area_name 完全相等；省级根节点直接父级以‘中华人民共和国’哨兵表示。",
            })
        matched = [n for n in base["candidate_nodes"] if n["parent_name_exact"]]
        if len(matched) == 1:
            selected = matched[0]
            counts["unique_candidate"] += 1
            counts[f"unique_candidate_{admin}"] += 1
            entries.append({**base, "review_state": "candidate_pending_independent_review",
                            "region_status_if_approved": "mapped",
                            "candidate_region_code": selected["code"],
                            "evidence_rule": "admin_level层级允许、area_name与官方节点名精确相等、parent_area_name与该节点直接父节点名精确相等；这是唯一候选，不自动构成批准映射。"})
            continue
        reason = classify_unmapped(admin, area, parent_name, name_nodes, matched)
        counts["unclassified_candidate"] += 1
        counts[reason] += 1
        counts[f"unclassified_{admin}"] += 1
        unmapped_reasons[reason].append(record["planid"])
        entries.append({**base, "review_state": "unclassified",
                        "reason": reason, "region_status_if_approved": "unclassified",
                        "candidate_region_code": None})
    result = {"algorithm_version": "policy-region-candidate-audit-v1",
              "binding": None,
              "entries": sorted(entries, key=lambda item: item["planid"])}
    # The binding is filled by the caller, before the evidence file is hashed.
    summary = {"counts": dict(sorted(counts.items())),
               "unclassified_planids_by_reason": {
                   key: sorted(value) for key, value in sorted(unmapped_reasons.items())}}
    return result, summary


def classify_unmapped(admin: str, area: Any, parent: Any, candidates: list[dict], matched: list[dict]) -> str:
    if not isinstance(parent, str) or not parent.strip():
        return "parent_missing"
    if parent in GENERIC_PARENTS:
        return "generic_parent_category"
    if not candidates:
        return "no_official_node_name_at_expected_tier"
    if len(matched) > 1:
        return "multiple_exact_direct_parent_candidates"
    if len(candidates) > 1 and len(matched) == 0:
        return "duplicate_name_without_parent_disambiguation"
    if len(matched) == 0:
        return "direct_parent_name_mismatch"
    return "no_unique_candidate"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-db", type=Path, required=True,
                        help="已发布 metadata.sqlite3 的本地只读路径")
    parser.add_argument("--release-manifest", type=Path, required=True,
                        help="与运行库 generation/revision 对应的发布数据清单")
    parser.add_argument("--prepared-documents", type=Path, required=True,
                        help="该 release prepared 目录中的 documents.jsonl，用于核验完整 id 集合")
    parser.add_argument("--raw-response", type=Path, required=True,
                        help="SHA已核验的原始官方响应；离线重建过滤树")
    parser.add_argument("--output-dir", type=Path,
                        default=Path.home() / "Documents" / "CC-Projects" / "guance-facets-runtime" / "regions",
                        help="本地地区资源目录；默认 Documents/CC-Projects/guance-facets-runtime/regions")
    parser.add_argument("--review-decisions", type=Path,
                        help="主审逐篇复核后填写的决议JSON；提供后才生成approved映射")
    args = parser.parse_args()
    try:
        if (not args.metadata_db.is_file() or not args.raw_response.is_file()
                or not args.release_manifest.is_file() or not args.prepared_documents.is_file()):
            raise ValueError("输入文件不存在")
        output = args.output_dir.resolve()
        mapping_path = output / "policy-region-map.json"
        if mapping_path.exists() and not args.review_decisions:
            raise ValueError("输出目录已有映射但未提供本次审查决议；拒绝覆盖或留下状态不一致的工件")
        source_tree_bytes = filter_raw_response(args.raw_response)
        nodes, region_meta = read_tree(source_tree_bytes)
        binding, records = open_catalog(args.metadata_db, args.release_manifest)
        prepared_sha = validate_prepared_documents(args.prepared_documents, binding)
        binding["prepared_documents_sha256"] = prepared_sha
        review, review_summary = candidate_evidence(records, nodes)
        review["binding"] = {k: binding[k] for k in
                             ("generation_id", "revision", "partial", "total_documents", "release_status")}
        review_payload = (json.dumps(review, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        review_sha = sha256(review_payload)
        manifest = {
            "source": region_meta["source"], "source_url": region_meta["source_url"],
            "request_method": region_meta["request_method"],
            "request_parameters": region_meta["request_parameters"],
            "scope": region_meta["scope"],
            "retrieved_at_utc": region_meta["retrieved_at_utc"],
            "snapshot_as_of": region_meta["snapshot_as_of"],
            "official_version": region_meta["official_version"],
            "versioning": "按抓取内容SHA-256冻结；不宣称历史发布版本",
            "encoding": "UTF-8",
            "source_artifact_sha256": region_meta["source_artifact_sha256"],
            "raw_response_sha256": EXPECTED_RAW_SHA256,
            "filtered_tree_file": "mainland-province-prefecture-county.json",
            "filtered_tree_sha256": region_meta["source_artifact_sha256"],
            "artifact_sha256": region_meta["source_artifact_sha256"],
            "region_version": region_meta["region_version"],
            "canonical_bytes": region_meta["canonical_bytes"],
            "node_counts": region_meta["node_counts"],
            "license_note": "官方可查询公开；再分发许可未确认。该工件仅输出至本地CC-Projects资源目录，不应提交公开仓库。",
            "generation_id": binding["generation_id"], "revision": binding["revision"],
            "total_documents": binding["total_documents"], "partial": binding["partial"],
            "source_document_ids_sha256": binding["source_document_ids_sha256"],
            "prepared_documents_sha256": binding["prepared_documents_sha256"],
            "mapping_status": "unapproved", "region_mapping_version": None,
            "candidate_audit_file": "policy-region-review.json",
            "candidate_audit_sha256": review_sha,
            "candidate_counts": review_summary["counts"],
            "unclassified_planids_by_reason": review_summary["unclassified_planids_by_reason"],
        }
        map_payload = None
        if args.review_decisions:
            decisions = json.loads(args.review_decisions.read_text(encoding="utf-8"))
            approved_by = decisions.get("approved_by")
            reviewed_sha = decisions.get("reviewed_audit_sha256")
            if not isinstance(approved_by, str) or not approved_by.strip():
                raise ValueError("审查决议必须填写 approved_by")
            reviewed_at = decisions.get("reviewed_at_utc")
            if not isinstance(reviewed_at, str) or not reviewed_at.strip():
                raise ValueError("审查决议必须填写 reviewed_at_utc")
            if reviewed_sha != review_sha:
                raise ValueError("审查决议绑定的 candidate audit SHA 不匹配")
            decision_entries = decisions.get("entries")
            if not isinstance(decision_entries, list) or any(not isinstance(item, dict)
                                                              for item in decision_entries):
                raise ValueError("审查决议 entries 必须是对象数组")
            decisions_by_id = {}
            for item in decision_entries:
                planid = item.get("planid")
                if not isinstance(planid, str) or not PLANID_RE.fullmatch(planid):
                    raise ValueError("审查决议 planid 格式无效")
                if planid in decisions_by_id:
                    raise ValueError(f"审查决议 planid 重复: {planid}")
                decisions_by_id[planid] = item
            if (len(decisions_by_id) != len(records)
                    or set(decisions_by_id) != {r["planid"] for r in records}):
                raise ValueError("审查决议 planid 集合必须与完整发布目录完全一致")
            candidates_by_id = {item["planid"]: item for item in review["entries"]}
            map_entries = []
            for planid in sorted(decisions_by_id):
                decision = decisions_by_id[planid]
                status = decision.get("region_status")
                code = decision.get("region_code")
                candidate = candidates_by_id[planid]
                if status == "mapped":
                    allowed = {node["code"] for node in candidate["candidate_nodes"]
                               if node["parent_name_exact"]}
                    if not isinstance(code, str) or code not in allowed or len(allowed) != 1:
                        raise ValueError(f"{planid} 映射未通过唯一直接父级候选校验")
                elif status == "not_applicable":
                    if candidate["admin_level"] != "national" or code is not None:
                        raise ValueError(f"{planid} 仅 national 可以 not_applicable")
                elif status == "unclassified":
                    if candidate["admin_level"] == "national" or code is not None:
                        raise ValueError(f"{planid} 非全国记录才能 unclassified，且 region_code 必须 null")
                else:
                    raise ValueError(f"{planid} region_status 非法")
                map_entries.append({"planid": planid, "region_code": code,
                                    "region_status": status})
            map_payload = {"algorithm_version": "policy-region-map-v1", "entries": map_entries}
            map_sha = "sha256:" + sha256(canonical_bytes(map_payload))
            manifest.update({"mapping_status": "approved", "region_mapping_version": map_sha,
                             "mapping_approved_by": approved_by,
                             "mapping_reviewed_at_utc": reviewed_at,
                             "mapping_review_method": decisions.get("review_method"),
                             "mapping_decisions_sha256": sha256(canonical_bytes(decisions))})
        output.mkdir(parents=True, exist_ok=True)
        source_tree_copy = output / "mainland-province-prefecture-county.json"
        source_tree_copy.write_bytes(source_tree_bytes)
        write_json(output / "policy-region-review.json", review)
        if map_payload is not None:
            write_json(mapping_path, map_payload)
        write_json(output / "manifest.json", manifest)
        # Verify the local artifact and keep source inputs read-only.
        if sha256(source_tree_copy.read_bytes()) != region_meta["source_artifact_sha256"]:
            raise ValueError("本地树工件复制后SHA不一致")
        print(json.dumps({"status": "approved_local_mapping" if map_payload is not None
                          else "review_resources_prepared_unapproved",
                          "output_dir": str(output), "generation_id": binding["generation_id"],
                          "revision": binding["revision"], "partial": binding["partial"],
                          "total_documents": binding["total_documents"],
                          "tree_artifact_sha256": region_meta["source_artifact_sha256"],
                          "region_version": region_meta["region_version"],
                          "candidate_audit_sha256": review_sha,
                          "region_mapping_version": manifest["region_mapping_version"],
                          "candidate_counts": review_summary["counts"],
                          "unclassified_planids_by_reason": review_summary["unclassified_planids_by_reason"]},
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"资源准备失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
