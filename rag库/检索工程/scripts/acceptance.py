"""真实冻结库验收；输出程序事实，不计算问答准确率。"""
import argparse
import ctypes
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from guance_rag import RagKnowledgeBase, RuntimeOptions
from guance_rag.errors import InvalidArgument, KBNotFound


def digest_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def windows_peak_working_set():
    if sys.platform != "win32":
        return None
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    values = Counters()
    values.cb = ctypes.sizeof(values)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = ctypes.c_void_p
    read = ctypes.windll.psapi.GetProcessMemoryInfo
    read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    return round(values.PeakWorkingSetSize / 1024 ** 3, 3) if read(process(), ctypes.byref(values), values.cb) else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["bm25", "hybrid", "dense"], default="bm25")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/acceptance.json")
    args = parser.parse_args()
    options = RuntimeOptions(mode=args.mode)
    config = json.loads((options.store_project / "config.json").read_text(encoding="utf-8"))
    db_path = options.store_project / config["storage"]["directory"] / "metadata.sqlite3"
    before_hash = digest_file(db_path)
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    generation = connection.execute("SELECT generation FROM runtime").fetchone()[0]
    manifest = json.loads(connection.execute("SELECT manifest FROM releases WHERE id=?", (generation,)).fetchone()[0])
    prepared = Path(manifest["prepared_path"])
    source_texts = {}

    def verify_source(value):
        key = value.document_id
        if key not in source_texts:
            doc = json.loads(connection.execute("SELECT data FROM documents WHERE id=?", (key,)).fetchone()[0])
            raw = (prepared / doc["snapshot"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == value.source_hash
            source_texts[key] = raw.decode("utf-8")
        text = source_texts[key]
        assert value.content == text[value.body_origin + value.char_start:value.body_origin + value.char_end]
        for context in value.contexts:
            if context.document_id not in source_texts:
                doc = json.loads(connection.execute("SELECT data FROM documents WHERE id=?", (context.document_id,)).fetchone()[0])
                source_texts[context.document_id] = (prepared / doc["snapshot"]).read_bytes().decode("utf-8")
            assert context.content == source_texts[context.document_id][context.body_origin + context.char_start:context.body_origin + context.char_end]

    start = time.perf_counter()
    with RagKnowledgeBase(options) as kb:
        startup = time.perf_counter() - start
        baseline = kb.stats()
        print(json.dumps({"phase": "ready", "startup_seconds": round(startup, 3), "mode": args.mode}), flush=True)
        samples = ROOT.parent / "设计方案2.0/Agent对接说明/samples"
        checked_samples = []
        for filename in ("Passage.json", "章节Passage.json", "长块第1页.json", "长块第2页.json"):
            expected = json.loads((samples / filename).read_text(encoding="utf-8"))
            cid = expected.get("chunk_id")
            offset = expected["char_start"] - (expected["chunk_char_start"] if cid else expected["section_char_start"])
            result = kb.read(expected["planid"], section_idx=expected["section_idx"] if not cid else None,
                             chunk_id=cid, offset=offset)
            for key in ("contract_version", "document_id", "source_hash", "generation_id", "body_origin", "body_start", "section_id", "section_idx", "section_path",
                        "chunk_char_start", "chunk_char_end", "char_start", "char_end", "content", "truncated", "next_offset"):
                assert getattr(result, key) == expected.get(key), (filename, key)
            assert [c.model_dump(exclude={"section_id", "section_path"}) for c in result.chunks] == expected["chunks"]
            verify_source(result)
            checked_samples.append(filename)

        search_start = time.perf_counter()
        hits = kb.search("养老服务", area="苏州", fyp="十四五", top_k=8)
        search_seconds = time.perf_counter() - search_start
        assert hits and all("苏州" in h.area and h.fyp in ("十四五", "第十四个五年规划") for h in hits)
        for hit in hits:
            verify_source(hit)
            full = kb.get_chunk_source(hit.chunk_id)
            assert full.content[hit.char_start - full.chunk_char_start:hit.char_end - full.chunk_char_start] == hit.content
        assert any(h.chunk_id == "chunk_c5481643e7c67d653e29160c30719104" for h in hits)
        assert not kb.search("养老服务", area="不存在的筛选地区")
        live_query = None
        if args.mode in {"dense", "hybrid"}:
            query = "苏州市十四五养老服务设施布局与智慧养老分别有哪些安排？"
            extra = kb.search(query, area="苏州", fyp="十四五", top_k=2)
            assert extra
            for hit in extra:
                verify_source(hit)
            live_query = dict(query=query, hits=len(extra), model_calls=kb.stats()["model_calls"], query_cache_hits=kb.stats()["query_cache_hits"])
        if args.mode == "bm25":
            assert not kb.search("zzzzNoSuchToken987654321")

        # 同一实例的并发调用实际执行search/read，随后核对线程及预加载次数。
        with ThreadPoolExecutor(max_workers=4) as pool:
            concurrent = list(pool.map(lambda i: kb.search("养老服务", area="苏州", fyp="十四五", top_k=2)
                                       if i % 2 else kb.read("48787", chunk_id="chunk_594a17c56902e7fdac2a38662282ebda", offset=6000), range(12)))
        assert all(concurrent)
        long_chunks = []
        for row in connection.execute("SELECT data FROM objects WHERE kind='chunk'"):
            chunk = json.loads(row[0])
            span = chunk["source_segments"][0]
            if span["end"] - span["start"] > 6000:
                long_chunks.append(chunk)
        total_pages = 0
        for chunk in long_chunks:
            full = kb.get_chunk_source(chunk["id"])
            offset, pages = 0, []
            while True:
                page = kb.read(full.planid, chunk_id=chunk["id"], offset=offset)
                verify_source(page)
                assert len(page.content) <= 6000
                pages.append(page.content)
                total_pages += 1
                if page.next_offset is None:
                    break
                assert page.next_offset > offset
                offset = page.next_offset
            assert "".join(pages) == full.content == chunk["source_segments"][0]["text"]
        sample_hit = hits[0]
        assert kb.verify_quote(sample_hit.chunk_id, sample_hit.content[:20], [[sample_hit.char_start, sample_hit.char_end]])
        assert not kb.verify_quote(sample_hit.chunk_id, "完全不在原文中的引句987654321", [[sample_hit.char_start, sample_hit.char_end]])
        status_checks = []
        for kwargs, error in [(dict(planid="000000000000"), KBNotFound),
                              (dict(planid="48787", chunk_id="chunk_594a17c56902e7fdac2a38662282ebda", offset=6134), InvalidArgument)]:
            try:
                kb.read(**kwargs)
                raise AssertionError("错误请求被接受")
            except error as exc:
                status_checks.append(exc.status)
        after = kb.stats()
        for name in ("generation_id", "revision", "worker_thread_id", "runtime_instance_id", "metadata_loads", "bm25_loads", "dense_loads", "snapshot_loads"):
            assert baseline[name] == after[name], name
    connection.close()
    assert digest_file(db_path) == before_hash
    report = dict(timestamp=datetime.now(timezone.utc).isoformat(), mode=args.mode, contract_version="proposal-v0.3", passed=True,
                  startup_seconds=round(startup, 3), first_search_seconds=round(search_seconds, 3), baseline=baseline, after=after,
                  samples_matched=checked_samples, hit_sample_recalled=True, concurrent_requests=12,
                  long_chunks_reconstructed=len(long_chunks), longest_chunk_chars=max(c["source_segments"][0]["end"] - c["source_segments"][0]["start"] for c in long_chunks),
                  long_chunk_pages=total_pages, errors_checked=status_checks, metadata_database_unchanged=True,
                  service_query=live_query, peak_working_set_gib=windows_peak_working_set(),
                  hits=[dict(chunk_id=h.chunk_id, planid=h.planid, score=h.score, char_start=h.char_start, char_end=h.char_end,
                             contexts=len(h.contexts), quality_warnings=h.quality_warnings) for h in hits],
                  elapsed_seconds=round(time.perf_counter() - start, 3), answer_accuracy_evaluated=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "mode": args.mode, "long_chunks": len(long_chunks), "pages": total_pages,
                      "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
