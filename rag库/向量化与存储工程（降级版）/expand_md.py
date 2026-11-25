"""从复赛 Markdown 分批扩建本地原生文本索引。日志只输出统计和错误码。"""
import argparse
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import yaml

PROJECT = Path(__file__).resolve().parent
RAG = PROJECT.parent
REPO = RAG.parent
sys.path[:0] = [str(RAG / "切块工程"), str(RAG / "检索工程" / "src")]

from filelock import FileLock
from guance_rag.config import RuntimeOptions
from guance_rag.expansion import (ExpansionRegistry, ExpansionRelease, current_documents, database_path,
                                  load_release, normalized, read_database)
from guance_rag.published import PublishedBackend
from policy_chunker.pipeline import code_fingerprint, process
from policy_chunker.source import Source
from policy_chunker.tokens import TokenCounter
from policy_chunker.validation import check_document
from vector_store.bm25s_index import BM25SLexical
from vector_store.candidates import payload, representation
from vector_store.common import digest, file_hash, read_json, write_json
from vector_store.sqlite_metadata import SQLiteMetadata

MIB = 1024 ** 2
GIB = 1024 ** 3
PROFILE = "markdown_lexical_v1"
OBJECTS = {"units": "unit", "structures": "structure", "parents": "parent",
           "chunks": "chunk", "relations": "relation", "issues": "issue"}
FIELDS = ("title", "areaid", "area_level", "area_name", "parent_area_name",
          "admin_level", "fyp_name", "plantype", "plantype_label", "plan_stage")
STAGE_FIELDS = ("plan_stage", "planning_stage", "stage")


def log(event, **data):
    print(json.dumps({"event": event, **data}, ensure_ascii=True), flush=True)


def catalogue(dataset):
    with (dataset / "metadata" / "sample_plans.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = {}
        for row in csv.DictReader(file):
            pid = row["planid"]
            if not re.fullmatch(r"[0-9]+", pid) or pid in rows or row["split"] not in ("corpus", "edge_cases"):
                raise ValueError("invalid planid or split in sample_plans")
            rows[pid] = row
    return rows


def stratified(rows, exclude):
    groups = defaultdict(list)
    for pid, row in rows.items():
        if row["split"] == "corpus" and pid not in exclude:
            groups[(row["admin_level"], row["fyp_name"])].append(pid)
    candidates = []
    for ids in groups.values():
        ids.sort(key=lambda pid: digest(["guance-md-expansion", pid]))
        for index, pid in enumerate(ids):
            candidates.append(((index + 0.5) / len(ids), pid))
    return [pid for _, pid in sorted(candidates)]


def merge_metadata(raw, row):
    csv_fields = dict(row)
    incoming = dict(raw)
    for values in (incoming, csv_fields):
        if not values.get("plan_stage"):
            values["plan_stage"] = next((values[k] for k in STAGE_FIELDS if values.get(k)), None)
    effective, conflicts = dict(incoming), []
    for key in FIELDS:
        a, b = normalized(key, incoming.get(key)), normalized(key, csv_fields.get(key))
        if a is None and b is not None:
            effective[key] = csv_fields[key]
        elif a is not None and b is not None and a != b:
            conflicts.append(key)
    return effective, conflicts, {key: csv_fields[key] for key in FIELDS if csv_fields.get(key)}


def source_path(dataset, row):
    pid = row["planid"]
    return dataset / row["split"] / pid / (pid + ".md")


def overlap_report(rows, base_docs, dataset, root):
    fingerprint = digest([(pid, base_docs[pid]["source_hash"]) for pid in sorted(base_docs)])
    target = root / "overlap.json"
    if target.exists():
        old = read_json(target)
        if old["fingerprint"] == fingerprint and old["dataset"] == str(dataset):
            return dict(Counter(old["statuses"].values()))
    statuses = {}
    for pid, document in base_docs.items():
        if pid not in rows:
            continue
        path = source_path(dataset, rows[pid])
        try:
            statuses[pid] = "same_hash" if file_hash(path) == document["source_hash"] else "different_hash"
        except OSError:
            statuses[pid] = "unreadable"
    write_json(target, {"fingerprint": fingerprint, "dataset": str(dataset), "statuses": statuses})
    return dict(Counter(statuses.values()))


def bytes_used(path):
    return sum((Path(directory) / name).stat().st_size
               for directory, _, files in os.walk(path) for name in files)


class DiskBudget:
    def __init__(self, root, reserve, cap):
        self.root, self.reserve, self.cap = root, reserve, cap
        self.minimum_free = shutil.disk_usage(root).free
        self.start_free = self.minimum_free
        self.start_used = bytes_used(root)
        self.peak = self.start_used

    def check(self, staging=None, need=0):
        free = shutil.disk_usage(self.root).free
        self.minimum_free = min(self.minimum_free, free)
        size = self.start_used + (bytes_used(staging) if staging else 0)
        self.peak = max(self.peak, size)
        if free - need < self.reserve or size + need > self.cap:
            raise BudgetReached


class BudgetReached(Exception):
    pass


def take_batch(dataset, rows, order, count, max_bytes):
    batch = []
    total = 0
    for pid in order:
        if len(batch) >= count:
            break
        path = source_path(dataset, rows[pid])
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size > max_bytes:
            raise ValueError("a Markdown file exceeds the configured batch byte limit")
        if batch and total + size > max_bytes:
            break
        batch.append(pid)
        total += size
    return batch, total


def checkpoint(db, pid, checksum, status, detail):
    with db.connection:
        db.connection.execute(
            "INSERT INTO md_imports VALUES (?,?,?,?) ON CONFLICT(planid) DO UPDATE "
            "SET checksum=excluded.checksum,status=excluded.status,detail=excluded.detail",
            (pid, checksum, status, json.dumps(detail)),
        )


def prepare_document(dataset, row, counter, config, config_hash):
    path = source_path(dataset, row)
    try:
        if not path.resolve().is_relative_to(dataset):
            raise ValueError("source path escaped dataset")
        source = Source.load(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return None, "source_error", type(exc).__name__
    raw_metadata = dict(source.metadata)
    source.metadata, conflicts, csv_fields = merge_metadata(raw_metadata, row)
    try:
        data = process(source, dataset, counter, config, config_hash, include_media=False)
    except (ValueError, UnicodeError, RecursionError) as exc:
        return source.source_hash, "processing_error", type(exc).__name__
    errors = check_document(source, data, counter, config)
    doc = data["documents"][0]
    if errors:
        return source.source_hash, "validation_error", errors
    origin = source.body_start
    if origin == 0 and source.text.startswith("\ufeff"):
        origin = 1
    while origin < len(source.text) and source.text[origin].isspace():
        origin += 1
    ready = [chunk for chunk in data["chunks"] if chunk["status"] == "ready"
             and len(chunk["source_segments"]) == 1
             and chunk["source_segments"][0]["start"] >= origin]
    if not ready:
        status = doc["status"] if not data["chunks"] else "no_valid_evidence"
        return source.source_hash, status, {}
    doc.update(raw_metadata=raw_metadata, csv_metadata=csv_fields, filter_conflicts=conflicts,
               body_origin=origin, body_codepoints=len(source.text) - origin,
               dataset="guance-finals-v2.1", split=row["split"])
    detail = {"chunks": len(ready), "excluded_chunks": len(data["chunks"]) - len(ready),
              "field_conflicts": conflicts, "explicit_stage": bool(source.metadata.get("plan_stage"))}
    data["chunks"] = ready
    return source.source_hash, "indexed", (source.raw, data, detail)


def ingest_document(db, shard, prepared, pid, result):
    checksum, status, detail = result
    done = db.connection.execute("SELECT checksum,status FROM md_imports WHERE planid=?", (pid,)).fetchone()
    if done and (done["checksum"] != checksum or status != "indexed"):
        raise ValueError("interrupted source changed")
    if status != "indexed":
        checkpoint(db, pid, checksum, status, detail)
        return
    raw, data, detail = detail
    doc = data["documents"][0]
    conflicts = doc["filter_conflicts"]
    checkpoint(db, pid, checksum, "processing", {})
    snapshot = prepared / doc["snapshot"]
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot.exists() or file_hash(snapshot) != checksum:
        temporary = snapshot.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.replace(snapshot)
    db.put_documents([doc])
    for table, kind in OBJECTS.items():
        db.put_objects(kind, data[table])
    db.register_links(data["chunks"], data["relations"])
    for chunk in data["chunks"]:
        description = {"type": "lexical", "expected_tokens": chunk["embedding_tokens"],
                       "retrieval_sha256": hashlib.sha256(chunk["retrieval_text"].encode()).hexdigest()}
        record = representation(doc, chunk, "text_chunk", "text", description, PROFILE)
        member = payload(record, doc, chunk, shard)
        member["period_key"] = normalized("fyp_name", doc["metadata"].get("fyp_name"))
        for key, mapped in (("area_name", "area_name"), ("fyp_name", "period_key")):
            if key in conflicts:
                member[mapped] = None
        db.put_record(shard, record, member, chunk["retrieval_text"], description)
    checkpoint(db, pid, checksum, "indexed", detail)


def init_worker(dataset, config, config_hash, tokenizer):
    global worker_settings
    counter = TokenCounter(RAG / "切块工程", config, tokenizer_directory=tokenizer)
    worker_settings = (dataset, counter, config, config_hash)


def prepare_in_worker(row):
    dataset, counter, config, config_hash = worker_settings
    return prepare_document(dataset, row, counter, config, config_hash)


def prepared_documents(planids, rows, dataset, config, config_hash, tokenizer, workers):
    if workers == 1:
        counter = TokenCounter(RAG / "切块工程", config, tokenizer_directory=tokenizer)
        for pid in planids:
            yield pid, prepare_document(dataset, rows[pid], counter, config, config_hash)
        return
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker,
                             initargs=(dataset, config, config_hash, tokenizer)) as pool:
        remaining = iter(planids)
        pending = deque((pid, pool.submit(prepare_in_worker, rows[pid]))
                        for pid in [next(remaining, None) for _ in range(workers * 2)] if pid)
        while pending:
            pid, future = pending.popleft()
            next_pid = next(remaining, None)
            if next_pid is not None:
                pending.append((next_pid, pool.submit(prepare_in_worker, rows[next_pid])))
            yield pid, future.result()


def verify_batch(path, *, check_segments=True):
    generation, _, documents = current_documents(path)
    if generation != path.name:
        raise ValueError("shard generation mismatch")
    with closing(read_database(database_path(path))) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("failed SQLite integrity")
        db_documents = db.execute("SELECT count(*) FROM documents").fetchone()[0]
        if db_documents != len(documents):
            raise ValueError("document manifest mismatch")
        members = db.execute(
            "SELECT r.id,r.text FROM representations r JOIN members m ON m.representation_id=r.id "
            "WHERE m.generation=? ORDER BY r.id", (generation,)
        ).fetchall()
        if len(members) != db.execute("SELECT count(*) FROM representations").fetchone()[0]:
            raise ValueError("unpublished representations in text-only shard")
        if check_segments:
            runtime_chunks = db.execute("SELECT count(*) FROM objects WHERE kind='chunk'").fetchone()[0]
            if runtime_chunks != len(members):
                raise ValueError("unpaired chunk and BM25 member")
            for doc in documents.values():
                snapshot = (path / "prepared" / doc["snapshot"]).resolve()
                if not snapshot.is_relative_to(path / "prepared"):
                    raise ValueError("invalid snapshot path")
                raw = snapshot.read_bytes()
                if hashlib.sha256(raw).hexdigest() != doc["source_hash"]:
                    raise ValueError("snapshot hash mismatch")
                source = raw.decode("utf-8")
                for (data, checksum) in db.execute(
                    "SELECT data,data_hash FROM objects WHERE document_id=? AND kind IN ('chunk','parent','unit','relation')",
                    (doc["id"],)
                ):
                    if hashlib.sha256(data.encode()).hexdigest() != checksum:
                        raise ValueError("indexed source object hash mismatch")
                    obj = json.loads(data)
                    segments = obj.get("source_segments", []) + obj.get("context_segments", [])
                    if "trigger" in obj:
                        segments.append(obj["trigger"])
                    if obj.get("kind") and "text" in obj and "start" in obj:
                        segments.append(obj)
                    for segment in segments:
                        a, b = segment["start"], segment["end"]
                        if not 0 <= a <= b <= len(source) or source[a:b] != segment["text"]:
                            raise ValueError("citation range mismatch")
        count = len(members)
    index = BM25SLexical(path / "bm25")
    try:
        index.validate(generation, members)
    finally:
        index.close()
    return len(documents), count


def build_batch(path, batch, dataset, rows, config, config_hash, tokenizer, budget, workers):
    path.mkdir(parents=True, exist_ok=True)
    plan = path / "batch.json"
    if plan.exists() and read_json(plan) != batch:
        raise ValueError("batch checkpoint identity mismatch")
    if not plan.exists():
        write_json(plan, batch)
    prepared = path / "prepared"
    prepared.mkdir(exist_ok=True)
    if not (path / "config.json").exists():
        write_json(path / "config.json", {"storage": {"directory": ".", "metadata": "sqlite"},
                                        "text_only": True})
    manifest = {"generation_id": path.name, "prepared_path": str(prepared), "partial": True,
                "text_only": True}
    started = time.monotonic()
    db = SQLiteMetadata(database_path(path))
    try:
        db.connection.execute("CREATE TABLE IF NOT EXISTS md_imports ("
                              "planid TEXT PRIMARY KEY,checksum TEXT,status TEXT NOT NULL,detail TEXT NOT NULL)")
        db.begin(path.name, manifest)
        known = {r[0] for r in db.connection.execute("SELECT planid FROM md_imports WHERE status<>'processing'")}
        pending = [pid for pid in batch["planids"] if pid not in known]
        for index, (pid, result) in enumerate(prepared_documents(
            pending, rows, dataset, config, config_hash, tokenizer, workers
        ), start=len(known) + 1):
            budget.check(path)
            ingest_document(db, path.name, prepared, pid, result)
            if index % 25 == 0:
                log("shard_progress", shard=path.name, completed=index, total=len(batch["planids"]))
        docs = db.connection.execute("SELECT id FROM documents ORDER BY id").fetchall()
        temporary = prepared / "documents.tmp"
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for (did,) in docs:
                stream.write(json.dumps({"id": did}) + "\n")
        temporary.replace(prepared / "documents.jsonl")
        records = [(row["record"]["id"], row["text"]) for row in db.members(path.name)]
        budget.check(path, need=max(32 * MIB, batch["input_bytes"] * 6))
        lexical = BM25SLexical(path / "bm25")
        try:
            lexical.build(path.name, records)
            lexical.validate(path.name, records)
        finally:
            lexical.close()
        if not db.integrity():
            raise ValueError("shard metadata integrity failed")
        stats = dict(db.connection.execute("SELECT status,count(*) FROM md_imports GROUP BY status"))
        conflicts = Counter()
        omitted = 0
        stages = 0
        for (detail,) in db.connection.execute("SELECT detail FROM md_imports WHERE status='indexed'"):
            detail = json.loads(detail)
            conflicts.update(detail["field_conflicts"])
            omitted += detail["excluded_chunks"]
            stages += detail["explicit_stage"]
        expected = {"passed": True, "documents": len(docs), "chunks": len(records)}
        db.ready(path.name, expected)
        if not db.active():
            db.activate(path.name, 0)
    finally:
        db.close()
    actual = verify_batch(path)
    if actual != (expected["documents"], expected["chunks"]):
        raise ValueError("shard verification totals disagree")
    budget.check(path)
    return {**expected, "results": stats, "field_conflicts": dict(conflicts),
            "excluded_chunks": omitted, "explicit_stages": stages,
            "input_bytes": batch["input_bytes"], "stored_bytes": bytes_used(path),
            "peak_added_bytes": budget.peak - budget.start_used,
            "min_free_bytes": budget.minimum_free,
            "seconds": round(time.monotonic() - started, 2)}


def candidate_batches(args, registry, dataset, rows, base_docs, config, tokenizer, config_hash):
    ready = registry.batches()
    exclude = set(base_docs)
    total = sum(summary["documents"] for _, summary in ready)
    for shard, _ in ready:
        exclude.update(read_json(args.root / shard / "batch.json")["planids"])
    unfinished = [path for path in args.root.glob("md_*")
                  if path.is_dir() and (path / "batch.json").is_file() and path.name not in {s for s, _ in ready}]
    if len(unfinished) > 1:
        raise ValueError("multiple incomplete shards")
    order = stratified(rows, exclude)
    reserve, cap = int(args.reserve_gib * GIB), int(args.cap_gib * GIB)
    while order and total < args.limit:
        if unfinished:
            path = unfinished.pop()
            batch = read_json(path / "batch.json")
            if batch["dataset"] != str(dataset) or batch["config"] != config_hash:
                raise ValueError("unfinished batch inputs changed")
            installed = bytes_used(path)
        else:
            pids, input_bytes = take_batch(dataset, rows, order, min(args.batch_size, args.limit - total),
                                           int(args.batch_mib * MIB))
            batch = {"dataset": str(dataset), "config": config_hash, "planids": pids,
                     "input_bytes": input_bytes,
                     "metadata_hash": digest([rows[pid] for pid in pids])}
            path = args.root / ("md_" + digest(batch)[:40])
            installed = 0
        if batch["metadata_hash"] != digest([rows[pid] for pid in batch["planids"]]):
            raise ValueError("unfinished batch metadata changed")
        budget = DiskBudget(args.root, reserve, cap)
        budget.start_used -= installed
        ratio = max((max(s["stored_bytes"], s["peak_added_bytes"]) / max(s["input_bytes"], 1)
                     for _, s in ready if s["documents"]), default=70)
        estimated = max(64 * MIB, int(max(0, 1.5 * ratio * batch["input_bytes"] - installed)))
        try:
            budget.check(path if installed else None, need=estimated)
            log("shard_started", shard=path.name, documents=len(batch["planids"]),
                input_bytes=batch["input_bytes"], estimated_extra_bytes=estimated)
            summary = build_batch(path, batch, dataset, rows, config, config_hash,
                                  tokenizer, budget, args.workers)
        except BudgetReached:
            log("disk_budget_stop", ready_documents=total, free_bytes=budget.minimum_free,
                reserved_bytes=reserve, maximum_total_bytes=cap, next_estimate_bytes=estimated)
            break
        registry.ready(path.name, summary)
        ready.append((path.name, summary))
        total += summary["documents"]
        processed = set(batch["planids"])
        order = [pid for pid in order if pid not in processed]
        log("shard_ready", shard=path.name, cumulative_documents=total, **summary)
    log("build_complete", ready_documents=total, ready_batches=len(ready), stored_bytes=bytes_used(args.root),
        free_bytes=shutil.disk_usage(args.root).free)


def smoke_release(base, batch_paths, wanted_documents):
    preview = ExpansionRelease("preview", 1, tuple(batch_paths), {p.name: p for p in batch_paths})
    options = RuntimeOptions(mode="bm25", store_project=base)
    with closing(PublishedBackend(options, preview=preview)) as backend:
        stats = backend.stats(deadline=time.monotonic() + 30)
        if stats["documents"] != wanted_documents or stats["model_calls"]:
            raise ValueError("combined runtime membership mismatch")
        checked = []
        mixed_ids = []
        representatives = [backend.active[0]]
        if len(backend.active) > 1:
            representatives.extend([backend.active[1], backend.active[-1]])
        for source in dict.fromkeys(representatives):
            searchable = {row["record"]["document_id"] for row in source.store.members.values()}
            candidates = [doc for doc in source.store.document_records.values()
                          if doc["id"] in searchable and doc["metadata"].get("title")]
            if not candidates:
                raise ValueError("no searchable title for release verification")
            document = next((doc for doc in candidates if "规划" in doc["metadata"]["title"]), candidates[0])
            metadata = document["metadata"]
            pid = document["planid"]
            deadline = time.monotonic() + 30
            hits = backend.search(metadata["title"][:100], None, None, 1, planids=[pid],
                                  source_types=["text_chunk"], deadline=deadline)
            if not hits or hits[0].planid != pid or hits[0].generation_id != source.store.generation:
                raise ValueError("combined runtime source identity mismatch")
            hit = hits[0]
            if not backend.verify_quote(hit.chunk_id, hit.content, [(hit.char_start, hit.char_end)],
                                        document_id=hit.document_id, generation_id=hit.generation_id,
                                        deadline=deadline):
                raise ValueError("combined runtime quote mismatch")
            checked.append(pid)
            if "规划" in metadata["title"]:
                mixed_ids.append(pid)
        if len(mixed_ids) >= 2:
            mixed = backend.search("规划", None, None, 8, planids=mixed_ids, source_types=["text_chunk"],
                                   deadline=time.monotonic() + 30)
            if not mixed or any(hit.planid not in mixed_ids for hit in mixed):
                raise ValueError("cross shard search failed")
    log("combined_runtime_verified", total_documents=wanted_documents, sampled_sources=len(checked))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "build", "verify", "activate", "rollback", "status"))
    parser.add_argument("--dataset", type=Path, help="包含 metadata/sample_plans.csv 和 corpus/ 的数据集目录")
    parser.add_argument("--base", type=Path, default=REPO / "GitHub排除" / "比较分析运行库-verified")
    parser.add_argument("--root", type=Path, default=REPO / "GitHub排除" / "rag-md-expansion")
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--batch-mib", type=float, default=16)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--reserve-gib", type=float, default=30)
    parser.add_argument("--cap-gib", type=float, default=12, help="扩容目录累计占用上限（GiB），默认12")
    parser.add_argument("--revision", type=int, help="回退到指定累计发布；省略则回退到原库")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 250 or not 0 < args.batch_mib <= 16 or not 1 <= args.limit:
        parser.error("每批上限为250篇和16 MiB，累计篇数必须为正")
    if not math.isfinite(args.cap_gib) or args.cap_gib <= 0:
        parser.error("扩容目录累计占用上限必须为有限正数")
    if not math.isfinite(args.reserve_gib) or args.reserve_gib < 30:
        parser.error("目标磁盘最低余量为30 GiB，且必须为有限数值")
    if not 1 <= args.workers <= 8:
        parser.error("本地并行进程数为1–8")
    if args.dataset is None and args.action in {"plan", "build", "verify", "activate"}:
        parser.error("此操作需要 --dataset 指定本地数据集目录")
    args.base, args.root = args.base.resolve(), args.root.resolve()
    if args.dataset is not None:
        args.dataset = args.dataset.resolve()
    if args.root.is_relative_to(args.base) or (args.dataset is not None and args.root.is_relative_to(args.dataset)):
        parser.error("扩容输出必须与原库和数据集分开")
    base_gen, base_revision, base_docs = current_documents(args.base)
    if args.action in ("plan", "status"):
        release = load_release(args.base, base_gen, args.root)
        totals = {"base_documents": len(base_docs), "base_generation": base_gen,
                  "free_bytes": shutil.disk_usage(args.root if args.root.exists() else args.root.parent).free,
                  "active_shards": len(release.active) if release else 0,
                  "added_bytes": bytes_used(args.root)}
        if release:
            totals.update(generation=release.generation, revision=release.revision,
                          published_documents=sum(len(current_documents(path)[2]) for path in release.active))
        if args.action == "plan":
            rows = catalogue(args.dataset)
            totals.update(dataset_documents=len(rows), splits=dict(Counter(r["split"] for r in rows.values())),
                          overlap=len(set(rows) & set(base_docs)),
                          eligible=len(stratified(rows, base_docs)))
        log(args.action, **totals)
        return
    args.root.mkdir(parents=True, exist_ok=True)
    lock = "expand.lock" if args.action == "build" else "publish.lock"
    # 发布只读取已登记的不可变分片，可以与下一批构建同时进行。
    with FileLock(str(args.root / lock), timeout=0):
        with closing(ExpansionRegistry(args.base, base_gen, args.root)) as registry:
            if args.action == "build":
                rows = catalogue(args.dataset)
                config_root = RAG / "切块工程"
                config = read_json(config_root / "config.json")
                tokenizer = (args.tokenizer or args.base / ".cache" / "qwen-tokenizer").resolve()
                counter = TokenCounter(config_root, config, tokenizer_directory=tokenizer)
                config_hash = digest([code_fingerprint(config_root), config, counter.profile_hash, PROFILE])
                candidate_batches(args, registry, args.dataset, rows, base_docs, config, tokenizer, config_hash)
            elif args.action in ("verify", "activate"):
                occupied = set(base_docs)
                rows = catalogue(args.dataset)
                overlap = overlap_report(rows, base_docs, args.dataset, args.root)
                log("baseline_overlap", **overlap)
                selected = []
                chunks = 0
                batches = registry.batches()
                for shard, summary in batches:
                    if not summary["documents"]:
                        continue
                    path = args.root / shard
                    if verify_batch(path) != (summary["documents"], summary["chunks"]):
                        raise ValueError("shard counts differ from the verified registry")
                    _, _, docs = current_documents(path)
                    if occupied.intersection(docs):
                        raise ValueError("duplicate planid across published shards")
                    occupied.update(docs)
                    chunks += summary["chunks"]
                    selected.append(shard)
                    log("shard_verified", count=len(selected), added_documents=len(occupied) - len(base_docs))
                if args.action == "activate":
                    smoke_release(args.base, [args.root / shard for shard in selected], len(occupied))
                    revision, generation = registry.activate(selected, base_revision)
                    results, conflicts = Counter(), Counter()
                    for _, summary in batches:
                        results.update(summary["results"])
                        conflicts.update(summary["field_conflicts"])
                    remaining = shutil.disk_usage(args.root).free
                    write_json(args.root / "release_summary.json", {
                        "revision": revision, "generation": generation, "base_documents": len(base_docs),
                        "added_documents": len(occupied) - len(base_docs), "total_documents": len(occupied),
                        "text_chunks_added": chunks, "shards": len(selected), "overlap": overlap,
                        "build_results": dict(results), "field_conflicts": dict(conflicts),
                        "explicit_stages": sum(s["explicit_stages"] for _, s in batches),
                        "excluded_chunks": sum(s["excluded_chunks"] for _, s in batches),
                        "markdown_input_bytes": sum(s["input_bytes"] for _, s in batches),
                        "published_shard_bytes": sum(s["stored_bytes"] for shard, s in batches if shard in selected),
                        "stored_bytes": bytes_used(args.root),
                        "peak_batch_bytes": max((s["peak_added_bytes"] for _, s in batches), default=0),
                        "minimum_observed_free_bytes": min([remaining, *(s["min_free_bytes"] for _, s in batches)]),
                        "free_bytes": remaining, "model_calls": 0,
                    })
                    log("activated", revision=revision, generation=generation)
                log("verified", added_documents=len(occupied)-len(base_docs), shards=len(selected), chunks=chunks)
            else:
                target = []
                if args.revision is not None and args.revision != base_revision:
                    row = registry.db.execute("SELECT shards FROM activation WHERE revision=?", (args.revision,)).fetchone()
                    if row is None:
                        raise ValueError("unknown rollback revision")
                    target = json.loads(row[0])
                revision, generation = registry.activate(target, base_revision)
                log("rolled_back", revision=revision, generation=generation, shards=len(target))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        log("failed", error=type(error).__name__)
        if isinstance(error, (ValueError, RuntimeError)) and re.fullmatch(r"[A-Za-z0-9 _-]{1,120}", str(error)):
            log("failure_reason", reason=str(error))
        raise SystemExit(1) from None
