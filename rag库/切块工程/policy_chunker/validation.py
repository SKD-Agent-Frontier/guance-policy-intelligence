"""独立重读快照、来源和 JSONL，不信任生成阶段的“已通过”标记。"""
from collections import Counter, defaultdict
from pathlib import Path
import json
import re
from .source import Source, sha, read_jsonl, write_json, inventory
from .tokens import retrieval_text

TABLES = ("documents", "units", "structures", "parents", "chunks", "relations",
          "media_assets", "media_occurrences", "issues")


def covered(start, end, segments):
    cursor = start
    for seg in sorted(segments, key=lambda s: (s["start"], s["end"])):
        if seg["end"] <= cursor:
            continue
        if seg["start"] > cursor:
            return False
        cursor = max(cursor, seg["end"])
        if cursor >= end:
            return True
    return cursor >= end


def check_document(source, data, counter, config):
    errors = []
    def check(condition, message):
        if not condition:
            errors.append(message)
    units, parents, chunks = data["units"], data["parents"], data["chunks"]
    doc_id = data["documents"][0]["id"]
    check(doc_id == source.version, "source_version_mismatch")
    check(data["documents"][0]["planid"] == source.planid, "planid_mismatch")
    maps = {name: {x["id"]: x for x in data[name]} for name in ("units", "structures", "parents", "chunks", "relations")}
    for name, values in maps.items():
        check(len(values) == len(data[name]), f"duplicate_{name}_id")
        check(all(record["document_id"] == doc_id for record in data[name]), f"{name}_document_mismatch")
    def segment(seg):
        start, end = seg["start"], seg["end"]
        check(0 <= start <= end <= len(source.text), "range_bounds")
        check(seg["text"] == source.text[start:end], "source_text_mismatch")
    cursor = 0
    for unit in units:
        check(unit["start"] == cursor, "unit_gap_or_overlap")
        segment(unit); cursor = unit["end"]
        if unit["kind"] != "metadata":
            owner = maps["structures"].get(unit["structure_id"])
            check(owner is not None and owner["start"] <= unit["start"] and owner["end"] >= unit["end"], "unit_structure_containment")
    check(cursor == len(source.text), "unit_coverage_incomplete")
    for structure in data["structures"]:
        check(0 <= structure["start"] <= structure["end"] <= len(source.text), "structure_bounds")
        parent_id = structure["parent_id"]
        seen = {structure["id"]}
        current = structure
        while current.get("parent_id"):
            ancestor = maps["structures"].get(current["parent_id"])
            if ancestor is None:
                check(False, "missing_structure_parent"); break
            if ancestor["id"] in seen:
                check(False, "structure_cycle"); break
            check(ancestor["start"] <= current["start"] and ancestor["end"] >= current["end"], "structure_containment")
            seen.add(ancestor["id"]); current = ancestor
    for parent in parents:
        check(parent["document_id"] == doc_id, "parent_document_mismatch")
        check(parent["structure_id"] in maps["structures"], "missing_parent_structure")
        owner = maps["structures"].get(parent["structure_id"])
        if owner:
            check(owner["start"] <= parent["start"] and owner["end"] >= parent["end"], "parent_structure_containment")
            check(parent["section_path"] == owner["path"], "parent_section_path_mismatch")
        for seg in parent["source_segments"]:
            segment(seg)
        for uid in parent["unit_ids"]:
            unit = maps["units"].get(uid)
            check(unit is not None and parent["start"] <= unit["start"] and parent["end"] >= unit["end"], "parent_unit_containment")
    relation_map = maps["relations"]
    for relation in data["relations"]:
        check(relation["document_id"] == doc_id, "relation_document_mismatch")
        check(relation["from_parent_id"] in maps["parents"], "relation_source_missing")
        segment(relation["trigger"])
        if relation["status"] == "resolved":
            target_map = maps["units"] if relation["target_kind"] == "unit" else maps["parents"]
            check(relation["target_id"] in target_map, "relation_target_missing")
        else:
            check(relation["target_id"] is None, "unresolved_relation_has_target")
    all_segments = []
    for chunk in chunks:
        check(chunk["document_id"] == doc_id, "chunk_document_mismatch")
        parent = maps["parents"].get(chunk["parent_id"])
        check(parent is not None, "chunk_parent_missing")
        if parent:
            check(chunk["section_path"] == parent["section_path"], "chunk_section_path_mismatch")
        for seg in chunk["source_segments"]:
            segment(seg)
            if parent:
                check(parent["start"] <= seg["start"] and parent["end"] >= seg["end"], "child_parent_containment")
        for seg in chunk["context_segments"]:
            segment(seg)
        all_segments.extend(chunk["source_segments"] + chunk["context_segments"])
        expected = retrieval_text(source, chunk["section_path"], chunk["source_segments"], chunk["context_segments"])
        check(chunk["retrieval_text"] == expected, "retrieval_assembly_mismatch")
        actual_tokens = counter.count(expected)
        check(chunk["embedding_tokens"] == actual_tokens, "token_count_mismatch")
        check(chunk["embedding_input_hash"] == sha(counter.render(expected).encode("utf-8")), "embedding_input_hash_mismatch")
        check(chunk["tokenizer_profile"] == counter.profile_hash, "tokenizer_profile_mismatch")
        check(chunk["content_hash"] == sha(json.dumps([s["text"] for s in chunk["source_segments"]], ensure_ascii=False, separators=(",", ":")).encode("utf-8")), "content_hash_mismatch")
        if chunk["status"] == "ready":
            check(not chunk["reasons"], "ready_chunk_has_reasons")
            check(actual_tokens <= config["max_input_tokens"], "ready_chunk_over_limit")
        for rid in chunk["required_relation_ids"]:
            relation = relation_map.get(rid)
            check(relation is not None, "required_relation_missing")
            if not relation:
                continue
            if relation["status"] != "resolved":
                check(chunk["status"] != "ready", "ready_chunk_unresolved_dependency")
            else:
                target = maps["units" if relation["target_kind"] == "unit" else "parents"].get(relation["target_id"])
                if target:
                    check(covered(target["start"], target["end"], chunk["source_segments"] + chunk["context_segments"]), "required_context_not_provided")
        # 不只检查登记的关联：该父块的必要关联不允许从 chunk 清单中悄悄遗漏。
        direct = {r["id"] for r in data["relations"] if r["from_parent_id"] == chunk["parent_id"] and r["required"]}
        check(direct.issubset(set(chunk["required_relation_ids"])), "required_relation_omitted")
    for unit in units:
        if unit["disposition"] in ("excluded", "pending"):
            check(bool(unit.get("reason")), "unit_without_exclusion_reason")
            continue
        if unit["kind"] == "table":
            # HTML 包装标签没有政策语义，但每个原始行必须进入子块或其共享上下文。
            rows = list(re.finditer(r"<tr\b[^>]*>.*?</tr\s*>", unit["text"], re.I | re.S))
            if rows:
                check(all(covered(unit["start"] + r.start(), unit["start"] + r.end(), all_segments) for r in rows), "table_row_coverage")
            else:
                check(covered(unit["start"], unit["end"], all_segments), "table_coverage")
        else:
            check(covered(unit["start"], unit["end"], all_segments), "effective_unit_not_covered")
        check(any(p["start"] <= unit["start"] and p["end"] >= unit["end"] for p in parents), "effective_unit_no_parent")
    asset_ids = {x["id"] for x in data["media_assets"]}
    for item in data["media_occurrences"]:
        segment(item["source_segment"])
        check(item["document_id"] == doc_id, "media_document_mismatch")
        if item["asset_id"]:
            check(item["asset_id"] in asset_ids, "media_asset_missing")
        if item["parent_id"]:
            parent = maps["parents"].get(item["parent_id"])
            check(parent is not None and parent["start"] <= item["source_segment"]["start"] and parent["end"] >= item["source_segment"]["end"], "media_parent_mismatch")
    return sorted(set(errors))


def validate_run(run, root, counter, config):
    """从汇总 JSONL 流式按文档读取，避免将数百 MB 原文副本全部装入内存。"""
    documents = list(read_jsonl(run / "documents.jsonl"))
    iterators = {name: iter(read_jsonl(run / f"{name}.jsonl")) for name in TABLES if name not in ("documents", "media_assets")}
    heads = {name: next(iterator, None) for name, iterator in iterators.items()}
    assets = list(read_jsonl(run / "media_assets.jsonl"))
    by_doc_assets = defaultdict(list)
    for asset in assets:
        for doc_id in asset["document_ids"]:
            by_doc_assets[doc_id].append(asset)
    errors, counts, ids, ready = [], Counter(), set(), []
    asset_ids = set()
    for asset in assets:
        if asset["id"] in asset_ids:
            errors.append({"error": "duplicate_asset_id"})
        asset_ids.add(asset["id"])
        for relative in asset["paths"]:
            path = root / relative
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or sha(path.read_bytes()) != asset["sha256"]:
                errors.append({"error": "asset_file_hash_mismatch", "path": relative})
    for document in documents:
        data = {"documents": [document], "media_assets": by_doc_assets[document["id"]]}
        for name, iterator in iterators.items():
            records = []
            while heads[name] is not None and heads[name]["document_id"] == document["id"]:
                records.append(heads[name]); heads[name] = next(iterator, None)
            data[name] = records
        for name, records in data.items():
            counts[name] += len(records) if name != "media_assets" else 0
            if name == "media_assets":
                continue
            for record in records:
                if record["id"] in ids:
                    errors.append({"planid": document["planid"], "error": "duplicate_global_id"})
                ids.add(record["id"])
        snapshot = run / document["snapshot"]
        if sha(snapshot.read_bytes()) != document["source_hash"]:
            errors.append({"planid": document["planid"], "error": "snapshot_hash_mismatch"})
        if sha((root / document["source_path"]).read_bytes()) != document["source_hash"]:
            errors.append({"planid": document["planid"], "error": "source_changed"})
        if document["status"] == "error":
            errors.append({"planid": document["planid"], "error": "document_processing_failed"}); continue
        source = Source.load(snapshot)
        errors.extend({"planid": document["planid"], "error": error} for error in check_document(source, data, counter, config))
        counts[f"document_status:{document['status']}"] += 1
        for unit in data["units"]:
            counts[f"unit_disposition:{unit['disposition']}"] += 1
        for chunk in data["chunks"]:
            counts[f"chunk_status:{chunk['status']}"] += 1
            for reason in set(chunk["reasons"]):
                counts[f"chunk_reason:{reason}"] += 1
            if chunk["status"] == "ready":
                ready.append({"id": chunk["id"], "document_id": document["id"], "parent_id": chunk["parent_id"],
                              "embedding_tokens": chunk["embedding_tokens"], "embedding_input_hash": chunk["embedding_input_hash"]})
        for issue in data["issues"]:
            counts[f"issue:{issue['reason']}"] += 1
        if counts["documents"] % 50 == 0:
            print(f"validated {counts['documents']}/{len(documents)}", flush=True)
    if any(value is not None for value in heads.values()):
        errors.append({"error": "orphan_or_out_of_order_records"})
    baseline = json.loads((run / "input_manifest.json").read_text(encoding="utf-8"))
    unchanged = inventory(root) == baseline
    if not unchanged:
        errors.append({"error": "input_inventory_changed"})
    run_profile = json.loads((run / "run_config.json").read_text(encoding="utf-8"))
    expected_paths = run_profile["selected_sources"]
    if [d["source_path"] for d in documents] != expected_paths:
        errors.append({"error": "document_accounting_mismatch"})
    available = sorted(x["path"] for x in baseline if x["path"].endswith(".md"))
    if run_profile["scope"] == "sample":
        available = [p for p in available if Path(p).stem in config["sample_planids"]]
    if expected_paths != available:
        errors.append({"error": "selected_source_accounting_mismatch"})
    counts["media_assets"] = len(assets)
    counts["physical_media_files"] = sum(len(a["paths"]) for a in assets)
    result = {"passed": not errors, "input_unchanged": unchanged, "counts": dict(counts), "errors": errors,
              "source_count": len(documents), "tokenizer_revision": counter.profile["revision"],
              "semantic_accuracy_evaluated": False}
    write_json(run / "validation.json", result)
    # 只有独立校验通过后才发布可用清单；失败时写空清单，避免旧结果被继续使用。
    from .source import write_jsonl
    write_jsonl(run / "usable_chunks.jsonl", ready if not errors else [])
    return result
