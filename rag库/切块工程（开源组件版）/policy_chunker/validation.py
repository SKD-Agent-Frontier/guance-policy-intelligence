"""独立重读快照、来源和 JSONL，不信任生成阶段的“已通过”标记。"""
# 校验分两层：check_document 检查一篇内的范围/结构/输入；validate_run 检查完整批次、
# 真实源文件和媒体，并在通过后生成 usable 清单。这里不调用生成模型复核语义。
# pending 是已登记的使用限制，不等于结构校验失败；但 pending 不能进入可用清单。
from collections import Counter, defaultdict
from pathlib import Path
import json
import re
from .source import Source, sha, read_jsonl, write_json, inventory
from .tokens import retrieval_text

TABLES = ("documents", "units", "structures", "parents", "chunks", "relations",
          "media_assets", "media_occurrences", "issues", "component_nodes", "component_calls")


def covered(start, end, segments):
    # 判断多个范围的并集能否无缺口覆盖 [start,end)，不要求这些范围彼此排他。
    # 例如 [10,20)、[18,30) 可覆盖 [10,30)，但 [10,20)、[21,30) 有一个字符缺口。
    cursor = start
    # cursor 始终表示目前已连续覆盖到哪里；遇到下一段起点大于 cursor 就立即失败。
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
    # data 是一篇文档的各类记录，source 从原始快照重建，counter 使用固定真实 tokenizer。
    # 返回去重错误码列表；空列表表示这里明确列出的确定性检查均通过，不表示语义全正确。
    errors = []
    def check(condition, message):
        # 收集可继续检查的错误，而不是遇到第一处问题就放弃整篇诊断。
        if not condition:
            errors.append(message)
    units, parents, chunks = data["units"], data["parents"], data["chunks"]
    doc_id = data["documents"][0]["id"]
    check(doc_id == source.version, "source_version_mismatch")
    check(data["documents"][0]["planid"] == source.planid, "planid_mismatch")
    maps = {name: {x["id"]: x for x in data[name]} for name in ("units", "structures", "parents", "chunks", "relations")}
    # ID 索引帮助检查引用目标；同时核对列表长度，防止字典覆盖重复 ID 后把重复隐藏掉。
    for name, values in maps.items():
        check(len(values) == len(data[name]), f"duplicate_{name}_id")
        check(all(record["document_id"] == doc_id for record in data[name]), f"{name}_document_mismatch")
    def segment(seg):
        # 范围合法只是第一步；必须同时证明保存的原文与快照切片逐字相同。
        start, end = seg["start"], seg["end"]
        check(0 <= start <= end <= len(source.text), "range_bounds")
        check(seg["text"] == source.text[start:end], "source_text_mismatch")
    cursor = 0
    # 排他覆盖检查从完整文件0开始，包含 YAML、空白、目录；不是仅对“有效正文”检查。
    for unit in units:
        check(unit["start"] == cursor, "unit_gap_or_overlap")
        segment(unit); cursor = unit["end"]
        if unit["kind"] != "metadata":
            owner = maps["structures"].get(unit["structure_id"])
            check(owner is not None and owner["start"] <= unit["start"] and owner["end"] >= unit["end"], "unit_structure_containment")
    check(cursor == len(source.text), "unit_coverage_incomplete")
    for structure in data["structures"]:
        # 章节允许嵌套，但上级必须存在、范围包含下级、父链不能回到自己。
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
        # 不仅检查父 ID 存在，还核对文档、章节范围、路径和所有成员单元的包含关系。
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
        # resolved 关系必须指向本篇实际对象；unresolved 则不能私自塞入一个猜测目标。
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
        # 正文段必须在父块内；跨条上下文可以在父块外，但仍必须指向同版本真实原文。
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
        # 重新装配、重新分词、重新求输入哈希，避免仅相信生成阶段写下的 token 数。
        check(chunk["retrieval_text"] == expected, "retrieval_assembly_mismatch")
        actual_tokens = counter.count(expected)
        check(chunk["embedding_tokens"] == actual_tokens, "token_count_mismatch")
        check(chunk["embedding_input_hash"] == sha(counter.render(expected).encode("utf-8")), "embedding_input_hash_mismatch")
        check(chunk["tokenizer_profile"] == counter.profile_hash, "tokenizer_profile_mismatch")
        check(chunk["content_hash"] == sha(json.dumps([s["text"] for s in chunk["source_segments"]], ensure_ascii=False, separators=(",", ":")).encode("utf-8")), "content_hash_mismatch")
        if chunk["status"] == "ready":
            # 可用块不能同时带阻断原因，也不能超过硬上限。pending 块保留超限实情。
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
                    # 关系存在还不够：目标的全部必要范围必须实际出现在模型输入的原文段中。
                    check(covered(target["start"], target["end"], chunk["source_segments"] + chunk["context_segments"]), "required_context_not_provided")
        # 不只检查登记的关联：该父块的必要关联不允许从 chunk 清单中悄悄遗漏。
        direct = {r["id"] for r in data["relations"] if r["from_parent_id"] == chunk["parent_id"] and r["required"]}
        check(direct.issubset(set(chunk["required_relation_ids"])), "required_relation_omitted")
    for unit in units:
        # 被排除或待处理的范围必须说明原因；其他有效单元必须有父块和子块/上下文覆盖。
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
    # 图片引用本身也是原文位置对象；有资源/父块关联时，目标必须真实存在且包含其位置。
    for item in data["media_occurrences"]:
        segment(item["source_segment"])
        check(item["document_id"] == doc_id, "media_document_mismatch")
        if item["asset_id"]:
            check(item["asset_id"] in asset_ids, "media_asset_missing")
        if item["parent_id"]:
            parent = maps["parents"].get(item["parent_id"])
            check(parent is not None and parent["start"] <= item["source_segment"]["start"] and parent["end"] >= item["source_segment"]["end"], "media_parent_mismatch")
    # 新版额外检查真实组件证据，避免“只写一个组件名称”冒充开源执行。
    nodes = {n["id"]: n for n in data.get("component_nodes", [])}
    calls = {c["id"]: c for c in data.get("component_calls", [])}
    for call in calls.values():
        check(call["component"] == "HierarchicalNodeParser+SentenceSplitter", "wrong_component")
        check(all(nid in nodes for nid in call["node_ids"]), "component_call_node_missing")
    for node in nodes.values():
        check(node["call_id"] in calls, "component_node_call_missing")
        if node["mapping"] == "failed":
            continue
        check(source.text[node["raw_start"]:node["raw_end"]] == node["text"], "component_raw_text_mismatch")
        check(sha(node["text"].encode()) == node["text_hash"], "component_text_hash_mismatch")
        if node["parent_node_id"]:
            parent_node = nodes.get(node["parent_node_id"])
            check(parent_node is not None, "component_parent_missing")
            if parent_node:
                check(node["id"] in parent_node["child_node_ids"], "component_relation_not_mutual")
                check(parent_node["start"] <= node["start"] <= node["end"] <= parent_node["end"], "component_node_containment")
    for parent in parents:
        check(all(nid in nodes for nid in parent.get("component_node_ids", [])), "parent_provenance_missing")
    for chunk in chunks:
        check(all(nid in nodes for nid in chunk.get("component_node_ids", [])), "chunk_provenance_missing")
        if chunk["status"] == "ready":
            check(bool(chunk.get("component_node_ids")), "ready_chunk_without_component_evidence")
        if chunk.get("generation") == "component":
            check(any(n["start"] == chunk["source_segments"][0]["start"] and n["end"] == chunk["source_segments"][-1]["end"]
                      for nid in chunk.get("component_node_ids", []) if (n := nodes[nid]).get("mapping") != "failed"), "component_boundary_changed")
        parent = maps["parents"].get(chunk["parent_id"])
        if parent and parent["kind"] == "list":
            intro = [maps["units"][uid] for uid in parent["unit_ids"] if maps["units"][uid]["kind"] == "list_intro"]
            notes = [maps["units"][uid] for uid in parent["unit_ids"] if maps["units"][uid]["kind"] == "note"]
            items = [maps["units"][uid] for uid in parent["unit_ids"] if maps["units"][uid]["kind"] == "list_item"]
            if chunk["status"] == "ready":
                check(all(covered(u["start"],u["end"],chunk["source_segments"]+chunk["context_segments"]) for u in notes), "list_note_context_missing")
            if intro and chunk["status"] == "ready":
                check(all(covered(u["start"],u["end"],chunk["source_segments"]+chunk["context_segments"]) for u in intro), "list_intro_context_missing")
                check(any(s["start"] < u["end"] and u["start"] < s["end"] for u in items for s in chunk["source_segments"]), "intro_only_chunk_ready")
    return sorted(set(errors))


def validate_run(run, root, counter, config):
    """从汇总 JSONL 流式按文档读取，避免将数百 MB 原文副本全部装入内存。"""
    documents = list(read_jsonl(run / "documents.jsonl"))
    # 汇总 JSONL 按 documents 的文档顺序分组。每个表保留一个 heads 前瞻记录，
    # 当前文档消费完就停在下一篇，不必同时加载全库的单位、父块和子块。
    iterators = {name: iter(read_jsonl(run / f"{name}.jsonl")) for name in TABLES if name not in ("documents", "media_assets")}
    heads = {name: next(iterator, None) for name, iterator in iterators.items()}
    assets = list(read_jsonl(run / "media_assets.jsonl"))
    # 资源是跨文档共享对象，单独建立 doc -> assets 索引，避免把共享资源误报为重复来源。
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
            # 校验资源记录与实际文件字节一致，不仅检查“这个路径似乎存在”。
            path = root / relative
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or sha(path.read_bytes()) != asset["sha256"]:
                errors.append({"error": "asset_file_hash_mismatch", "path": relative})
    for document in documents:
        # 流式消费每类表中当前 document_id 的连续记录；剩余记录会在批次末尾检查。
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
            # 处理失败不是可发布的“空结果”；留下错误并继续检查其他已完成文档。
            errors.append({"planid": document["planid"], "error": "document_processing_failed"}); continue
        source = Source.load(snapshot)
        errors.extend({"planid": document["planid"], "error": error} for error in check_document(source, data, counter, config))
        counts[f"document_status:{document['status']}"] += 1
        for unit in data["units"]:
            counts[f"unit_disposition:{unit['disposition']}"] += 1
        for chunk in data["chunks"]:
            counts[f"chunk_status:{chunk['status']}"] += 1
            counts[f"chunk_generation:{chunk.get('generation', 'unknown')}"] += 1
            for reason in set(chunk["reasons"]):
                counts[f"chunk_reason:{reason}"] += 1
            if chunk["status"] == "ready":
                ready.append({"id": chunk["id"], "document_id": document["id"], "parent_id": chunk["parent_id"],
                              "embedding_tokens": chunk["embedding_tokens"], "embedding_input_hash": chunk["embedding_input_hash"]})
        for issue in data["issues"]:
            counts[f"issue:{issue['reason']}"] += 1
        for call in data.get("component_calls", []):
            counts[f"component_call:{call['reason']}"] += 1
        if counts["documents"] % 50 == 0:
            print(f"validated {counts['documents']}/{len(documents)}", flush=True)
    if any(value is not None for value in heads.values()):
        # 发现不属于文档清单的记录或表顺序不一致，不能默默忽略尾部数据。
        errors.append({"error": "orphan_or_out_of_order_records"})
    baseline = json.loads((run / "input_manifest.json").read_text(encoding="utf-8"))
    # 重扫全部批准输入与构建前清单比较，检测内容变化、文件新增或文件删除。
    unchanged = inventory(root) == baseline
    if not unchanged:
        errors.append({"error": "input_inventory_changed"})
    run_profile = json.loads((run / "run_config.json").read_text(encoding="utf-8"))
    from .engine import verify_installation
    try:
        verify_installation(counter.project, run_profile["component_profile"])
    except ValueError as error:
        errors.append({"error": "component_installation_mismatch", "detail": str(error)})
    expected_paths = run_profile["selected_sources"]
    if [d["source_path"] for d in documents] != expected_paths:
        errors.append({"error": "document_accounting_mismatch"})
    available = sorted(x["path"] for x in baseline if x["path"].endswith(".md"))
    # 全量批次应包含全部 Markdown；样例批次按固定 sample_planids 选取，不能漏篇不报。
    if run_profile["scope"] == "sample":
        available = [p for p in available if Path(p).stem in config["sample_planids"]]
    if expected_paths != available:
        errors.append({"error": "selected_source_accounting_mismatch"})
    counts["media_assets"] = len(assets)
    counts["physical_media_files"] = sum(len(a["paths"]) for a in assets)
    result = {"passed": not errors, "input_unchanged": unchanged, "counts": dict(counts), "errors": errors,
              "source_count": len(documents), "tokenizer_revision": counter.profile["revision"],
              "semantic_accuracy_evaluated": False}
    # 报告显式区分确定性检查与语义效果评测；数量相符或位置正确不能冒充问答准确率。
    write_json(run / "validation.json", result)
    # 只有独立校验通过后才发布可用清单；失败时写空清单，避免旧结果被继续使用。
    from .source import write_jsonl
    write_jsonl(run / "usable_chunks.jsonl", ready if not errors else [])
    return result
