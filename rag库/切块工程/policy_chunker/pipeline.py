"""单 worker 批次运行、内容寻址检查点、全量输出和中文报告。"""
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
import importlib.metadata
import json
import re
import subprocess
import time
from .source import Source, inventory, sha, stable_id, write_json, write_jsonl, read_jsonl
from .structure import parse
from .chunking import EXCLUDED, source_quality, make_parents, make_chunks
from .relations import build_relations, assemble
from .media import register
from .tokens import TokenCounter
from .validation import TABLES, validate_run, covered


def code_fingerprint(project):
    files = sorted((project / "policy_chunker").glob("*.py")) + [project / "chunk.py"]
    return sha(b"".join(p.name.encode() + p.read_bytes() for p in files))


def process(source, root, counter, config, config_hash):
    quality, metrics = source_quality(source)
    units, structures, hints_used = parse(source, config_hash)
    parents, chunks, relations = [], [], []
    if quality == "normal":
        parents = make_parents(source, units, structures, counter, config, config_hash)
        chunks = make_chunks(source, parents, units, counter, config, config_hash)
        relations = build_relations(source, units, structures, parents, config_hash)
        chunks = assemble(source, parents, units, chunks, relations, counter, config, config_hash)
    assets, occurrences, problems = register(source, root, units, parents)
    issues = []
    def issue(reason, start=None, end=None, **extra):
        issues.append({"id": stable_id("issue", source.version, reason, start, end, extra, config_hash),
                       "document_id": source.version, "planid": source.planid, "reason": reason,
                       **({"range": {"start": start, "end": end}} if start is not None else {}), **extra})
    if quality != "normal":
        issue(quality, source.body_start, len(source.text), metrics=metrics)
    if not hints_used:
        issue("markdown_hint_skipped_fragmented_source", source.body_start, len(source.text))
    for problem in problems:
        problem = dict(problem)
        span = problem.pop("range", {})
        reason = problem.pop("reason")
        issue(reason, span.get("start"), span.get("end"), **problem)
    for relation in relations:
        if relation["status"] == "unresolved":
            issue("unresolved_required_dependency", relation["trigger"]["start"], relation["trigger"]["end"], relation_id=relation["id"], rule=relation["rule"])
    for chunk in chunks:
        if chunk["status"] == "pending":
            issue("pending_chunk", chunk["source_segments"][0]["start"], chunk["source_segments"][-1]["end"], chunk_id=chunk["id"], causes=chunk["reasons"])
    represented = [s for c in chunks for s in c["source_segments"] + c["context_segments"]]
    for unit in units:
        if unit["kind"] in EXCLUDED:
            unit["disposition"], unit["reason"] = "excluded", unit["kind"]
        elif quality != "normal":
            unit["disposition"], unit["reason"] = "pending", quality
        elif unit["kind"] == "table" or covered(unit["start"], unit["end"], represented):
            unit["disposition"], unit["reason"] = "represented", None
        else:
            unit["disposition"], unit["reason"] = "pending", "unreliable_structure"
            issue("unreliable_structure", unit["start"], unit["end"])
    if quality == "normal" and not chunks:
        quality = "title_only"
        issue(quality, source.body_start, len(source.text))
    partial = any(c["status"] != "ready" for c in chunks) or any(u["disposition"] == "pending" for u in units)
    status = quality if quality != "normal" else ("partial" if partial else "ready")
    snapshot = f"snapshots/{source.source_hash}/{source.planid}.md"
    document = {"id": source.version, "planid": source.planid, "source_path": source.path.relative_to(root).as_posix(),
                "source_hash": source.source_hash, "source_bytes": len(source.raw), "source_codepoints": len(source.text),
                "snapshot": snapshot, "encoding": "utf-8", "body_start": source.body_start,
                "metadata": source.metadata, "status": status, "quality_metrics": metrics,
                "config_hash": config_hash, "structure_hints": "markdown-it+rules" if hints_used else "rules_only_fragmented_source"}
    return {"documents": [document], "units": units, "structures": structures, "parents": parents,
            "chunks": chunks, "relations": relations, "media_assets": assets, "media_occurrences": occurrences, "issues": issues}


def merge_shards(run, shard_paths):
    for name in TABLES:
        if name == "media_assets":
            merged = {}
            for path in shard_paths:
                for asset in json.loads(path.read_text(encoding="utf-8"))[name]:
                    if asset["id"] not in merged:
                        merged[asset["id"]] = asset
                    else:
                        target = merged[asset["id"]]
                        target["paths"] = sorted(set(target["paths"] + asset["paths"]))
                        target["document_ids"] = sorted(set(target["document_ids"] + asset["document_ids"]))
            write_jsonl(run / f"{name}.jsonl", sorted(merged.values(), key=lambda a: a["id"]))
        else:
            def records():
                for path in shard_paths:
                    yield from json.loads(path.read_text(encoding="utf-8"))[name]
            write_jsonl(run / f"{name}.jsonl", records())


def report(run, result, build_stats):
    counts = result["counts"]
    lines = ["# RAG 2.0 切块校验报告", "", f"程序校验：**{'通过' if result['passed'] else '失败'}**。输入文件哈希未改变：**{result['input_unchanged']}**。", "",
             "本轮为文本切块与图片登记；没有运行 OCR、向量计算或问答评测。程序检查不保证全部政策语义正确。", "",
             "| 项目 | 数量 |", "|---|---:|"]
    for name in ("documents", "units", "structures", "parents", "chunks", "relations", "media_assets", "physical_media_files", "media_occurrences", "issues"):
        lines.append(f"| {name} | {counts.get(name, 0)} |")
    lines += ["", "## 文档、子块及异常分布", "", "| 类别 | 数量 |", "|---|---:|"]
    for key, value in sorted(counts.items()):
        if ":" in key:
            lines.append(f"| {key} | {value} |")
    ready_tokens = [c["embedding_tokens"] for c in read_jsonl(run / "chunks.jsonl") if c["status"] == "ready"]
    lines += ["", f"可用子块最大完整输入：{max(ready_tokens, default=0)} token；硬上限：8192。",
              f"tokenizer 修订：`{result['tokenizer_revision']}`。使用官方模板，计入特殊 token，禁用截断。", "",
              "## 校验项目", "", "- 全文基础单元无缺口、无重叠；快照及来源逐文件哈希一致。",
              "- 所有原文范围逐字还原，父子与章节归属合法，结构无循环。",
              "- 必要关联目标真实存在，已解析上下文进入实际检索输入；未解决依赖不能成为可用块。",
              "- 逐块重建检索输入并重新计数，校验输入哈希、上限及内容指纹。",
              "- 每份输入有状态；可用清单仅在独立校验通过后生成。", "",
              "## 使用边界", "", "`usable_chunks.jsonl` 是可用成员清单；`chunks.jsonl` 同时保留可用与待处理对象，不能全部直接入索引。",
              "`issues.jsonl` 保存异常及范围。源数据疑似乱码、图片未识别、缺失图片、歧义引用与超限内容没有被自动修复。",
              "目录和空白等排除内容仍在 units 与来源快照中。相同字节图片合并资源登记，各文档出现位置分别保留。", "",
              "## 本次运行", "", f"新处理：{build_stats.get('processed', 0)}；检查点复用：{build_stats.get('reused', 0)}。",
              f"构建耗时：{build_stats.get('seconds', 0):.1f} 秒。该时间不包含独立校验。", "",
              "## 程序错误", "", json.dumps(result["errors"], ensure_ascii=False, indent=2)]
    (run / "校验报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    samples = ["# 切块样例", "", "以下只是截短展示，完整原文段与位置请读同批 JSONL 和快照；预览不是新的证据原文。", ""]
    wanted = {"9888", "49151", "39706", "20962", "30740", "47152", "32697", "18307", "19251", "38520"}
    docs = {d["id"]: d for d in read_jsonl(run / "documents.jsonl")}
    shown = Counter()
    for chunk in read_jsonl(run / "chunks.jsonl"):
        doc = docs[chunk["document_id"]]
        if doc["planid"] not in wanted or shown[doc["planid"]] >= 2:
            continue
        shown[doc["planid"]] += 1
        seg = chunk["source_segments"][0]
        samples += [f"## {doc['planid']} · {doc['metadata'].get('title', '')}", "",
                    f"块 `{chunk['id']}`；父块 `{chunk['parent_id']}`；状态 `{chunk['status']}`；完整输入 {chunk['embedding_tokens']} token。",
                    f"原文码点 `[{seg['start']}:{seg['end']}]`；行 {seg['line_start']}–{seg['line_end']}；[冻结原文]({doc['snapshot']})。", "",
                    "```text", seg["text"][:500], "```", ""]
    for doc in docs.values():
        if doc["planid"] in wanted and not shown[doc["planid"]]:
            samples += [f"## {doc['planid']}", "", f"文档状态 `{doc['status']}`，无政策文本子块。[冻结原文]({doc['snapshot']})。", ""]
    (run / "切块样例.md").write_text("\n".join(samples), encoding="utf-8")


def build(project, args):
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    root = Path(args.source).resolve() if args.source else project.parents[1] / "语料库" / "Agent标准引用库"
    counter = TokenCounter(project, config)
    code_hash = code_fingerprint(project)
    config_hash = stable_id("config", config, counter.profile, code_hash)
    baseline = inventory(root)
    sources = sorted([p for group in ("corpus", "edge_cases") for p in (root / group).glob("*/*.md")], key=lambda p: p.relative_to(root).as_posix())
    if args.sample:
        sources = [p for p in sources if p.stem in config["sample_planids"]]
        if set(p.stem for p in sources) != set(config["sample_planids"]):
            raise ValueError("sample policies missing")
    if not sources or len({p.stem for p in sources}) != len(sources):
        raise ValueError("empty corpus or duplicate planid")
    scope = "sample" if args.sample else "all"
    run_id = f"{scope}-{config_hash[-10:]}-{stable_id('input', baseline)[-10:]}"
    run = Path(args.run).resolve() if args.run else project / "outputs" / run_id
    if run.is_relative_to(root.resolve()):
        raise ValueError("output cannot be inside input library")
    run.mkdir(parents=True, exist_ok=True)
    run_config = {"config": config, "config_hash": config_hash, "code_hash": code_hash, "model_profile": counter.profile,
                  "scope": scope,
                  "tokenizer_profile": counter.profile_hash, "source_root": str(root.resolve()),
                  "selected_sources": [p.relative_to(root).as_posix() for p in sources]}
    if (run / "run_config.json").exists():
        if not args.resume:
            raise ValueError("batch exists; use --resume or a new --run directory")
        previous = json.loads((run / "run_config.json").read_text(encoding="utf-8"))
        if previous != run_config or json.loads((run / "input_manifest.json").read_text(encoding="utf-8")) != baseline:
            raise ValueError("batch inputs/config/code changed; select a new batch")
    else:
        write_json(run / "run_config.json", run_config)
        write_json(run / "input_manifest.json", baseline)
        status = subprocess.run(["git", "-c", "core.quotepath=false", "status", "--short"], cwd=project.parents[1], capture_output=True)
        (run / "git_baseline.txt").write_bytes(status.stdout)
    cache = project / ".cache" / "documents" / config_hash
    cache.mkdir(parents=True, exist_ok=True)
    state_path = run / "checkpoint.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    shard_paths, stats = [], {"processed": 0, "reused": 0}
    started = time.monotonic()
    for i, path in enumerate(sources, 1):
        raw = path.read_bytes()
        digest = sha(raw)
        media_prefix = path.parent.relative_to(root).as_posix() + "/media/"
        media_inventory = [entry for entry in baseline if entry["path"].startswith(media_prefix)]
        key = stable_id("job", path.relative_to(root).as_posix(), digest, media_inventory, config_hash)
        shard = cache / f"{key}.json"
        checksum = cache / f"{key}.checksum.json"
        previous = state.get(key) or (json.loads(checksum.read_text(encoding="utf-8")) if checksum.exists() else None)
        reusable = args.resume and previous and shard.exists() and sha(shard.read_bytes()) == previous["sha256"]
        if reusable:
            data = json.loads(shard.read_text(encoding="utf-8"))
            # 失败不是可复用结果；例如临时文件锁解除后，恢复应重新尝试该文档。
            reusable = data["documents"][0]["status"] != "error"
        if reusable:
            stats["reused"] += 1
        else:
            try:
                source = Source.load(path)
                data = process(source, root, counter, config, config_hash)
            except Exception as error:
                version = stable_id("doc", path.stem, digest)
                data = {name: [] for name in TABLES}
                data["documents"] = [{"id": version, "planid": path.stem, "source_path": path.relative_to(root).as_posix(),
                                      "source_hash": digest, "snapshot": f"snapshots/{digest}/{path.stem}.md", "status": "error", "error": repr(error)}]
                data["issues"] = [{"id": stable_id("issue", version, "processing_error"), "document_id": version, "reason": "processing_error", "detail": repr(error)}]
            write_json(shard, data)
            write_json(checksum, {"sha256": sha(shard.read_bytes())})
            stats["processed"] += 1
        document = data["documents"][0]
        snapshot = run / document["snapshot"]
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        damaged_snapshot = snapshot.exists() and snapshot.read_bytes() != raw
        if damaged_snapshot and not args.resume:
            raise ValueError("existing snapshot differs from source bytes")
        if not snapshot.exists() or damaged_snapshot:
            # 快照同样原子落盘。恢复时只用本批已核验的源字节修复不完整派生副本。
            temporary = snapshot.with_name(snapshot.name + ".tmp")
            temporary.write_bytes(raw)
            temporary.replace(snapshot)
        # 只有完整 JSON 原子落盘后才写完成检查点；损坏的检查点会重新处理。
        state[key] = {"sha256": sha(shard.read_bytes()), "planid": path.stem, "status": document["status"]}
        write_json(state_path, state)
        shard_paths.append(shard)
        print(f"{i}/{len(sources)} planid={path.stem} status={document['status']} chunks={len(data['chunks'])} reused={bool(reusable)}", flush=True)
    merge_shards(run, shard_paths)
    stats["seconds"] = time.monotonic() - started
    write_json(run / "build_stats.json", stats)
    packages = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
    write_json(run / "environment.json", {"packages": packages, "worker_count": 1,
                                         "finished_at": datetime.now(timezone(timedelta(hours=8))).isoformat()})
    result = validate_run(run, root, counter, config)
    report(run, result, stats)
    print(json.dumps({"run": str(run), "passed": result["passed"], "counts": result["counts"], "errors": result["errors"]}, ensure_ascii=False), flush=True)
    return 0 if result["passed"] else 1
