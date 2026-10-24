"""真实完整发布的多模态验收，不改索引、不重做OCR；不作为问答准确率。"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from guance_rag import RagKnowledgeBase, RuntimeOptions
from guance_rag.errors import KBNotFound
from acceptance import digest_file, windows_peak_working_set


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["bm25", "hybrid"], default="bm25")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/media-acceptance.json")
    args = parser.parse_args()
    options = RuntimeOptions(mode=args.mode)
    config = json.loads((options.store_project / "config.json").read_text(encoding="utf-8"))
    path = options.store_project / config["storage"]["directory"] / "metadata.sqlite3"
    before_hash = digest_file(path)
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    generation = db.execute("SELECT generation FROM runtime").fetchone()[0]
    manifest = json.loads(db.execute("SELECT manifest FROM releases WHERE id=?", (generation,)).fetchone()[0])
    rows = db.execute("SELECT r.record FROM representations r JOIN members m ON r.id=m.representation_id WHERE m.generation=? ORDER BY r.id", (generation,))
    sources = {}
    for (record,) in rows:
        record = json.loads(record)
        if record["source_type"] != "text_chunk":
            sources.setdefault(record["source_id"], record)
    docs, objects, snapshots = {}, {}, {}
    def object_value(oid):
        if oid not in objects:
            objects[oid] = json.loads(db.execute("SELECT data FROM objects WHERE id=?", (oid,)).fetchone()[0])
        return objects[oid]

    def verify_page(page):
        did = page.document_id
        if did not in docs:
            docs[did] = json.loads(db.execute("SELECT data FROM documents WHERE id=?", (did,)).fetchone()[0])
            raw = (Path(manifest["prepared_path"]) / docs[did]["snapshot"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == docs[did]["source_hash"] == page.source_hash
            snapshots[did] = raw.decode("utf-8")
        assert page.generation_id == generation
        assert page.source_hash == docs[did]["source_hash"]
        assert "char_start" not in page.model_dump() and "chunk_char_start" not in page.model_dump()
        if page.reference_span:
            anchor = page.reference_span
            assert anchor.content == snapshots[did][anchor.body_origin + anchor.char_start:anchor.body_origin + anchor.char_end]
        else:
            assert "document_only_image_association" in page.quality_warnings
        if page.ocr_range:
            span = page.ocr_range
            transcript = object_value(page.ocr_parent_id)["transcript"]
            assert hashlib.sha256(transcript.encode()).hexdigest() == span.transcript_hash
            assert page.content == transcript[span.char_start:span.char_end]
            for context in page.ocr_contexts:
                assert context.content == transcript[context.char_start:context.char_end]
        else:
            assert page.content_basis in {"none", "image_alt"}

    samples = ROOT.parent / "设计方案2.0/Agent对接说明/samples"
    samples.mkdir(exist_ok=True)
    output = args.output.parent
    output.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with RagKnowledgeBase(options) as kb:
        startup = time.perf_counter() - start
        before = kb.stats()
        print(json.dumps(dict(phase="ready", mode=args.mode, startup_seconds=round(startup, 3), image_assets_verified=before["image_assets_verified"])), flush=True)
        pages, long_units, unanchored, no_ocr, pending, kinds = 0, 0, 0, 0, 0, {}
        selected, seen_kinds = [], set()
        for sid in sorted(sources):
            record = sources[sid]
            if record["document_id"] not in docs:
                docs[record["document_id"]] = json.loads(db.execute("SELECT data FROM documents WHERE id=?", (record["document_id"],)).fetchone()[0])
                raw = (Path(manifest["prepared_path"]) / docs[record["document_id"]]["snapshot"]).read_bytes()
                snapshots[record["document_id"]] = raw.decode("utf-8")
            first = kb.read(docs[record["document_id"]]["planid"], source_id=sid, document_id=record["document_id"], generation_id=generation)
            verify_page(first)
            pages += 1
            unanchored += first.reference_span is None
            no_ocr += first.recognition_status == "unavailable"
            pending += first.recognition_status == "pending"
            if first.source_type == "visual_chunk":
                kind = first.kind
                kinds[kind] = kinds.get(kind, 0) + 1
                if kind not in seen_kinds and len(selected) < 4:
                    selected.append(first)
                    seen_kinds.add(kind)
            if first.next_offset is not None:
                long_units += 1
                parts, offset = [first.content], first.next_offset
                while offset is not None:
                    page = kb.read(first.planid, source_id=sid, offset=offset, generation_id=generation)
                    verify_page(page)
                    parts.append(page.content)
                    pages += 1
                    offset = page.next_offset
                span = first.ocr_range
                assert "".join(parts) == object_value(first.ocr_parent_id)["transcript"][span.full_char_start:span.full_char_end]
        print(json.dumps(dict(phase="all_media_read", sources=len(sources), pages=pages, long_ocr_units=long_units)), flush=True)

        query = "逝有所安重点工程 骨灰安放 殡仪馆"
        ocr_hits = kb.search(query, area="江苏", fyp="十四五", top_k=20, source_types=["visual_chunk"])
        assert ocr_hits and all(h.source_type == "visual_chunk" for h in ocr_hits)
        target = "visual_chunk_2d7f1a17b95d298e1b043552f5efc52e14dc455132118fdd458134078338bf76"
        assert target in {h.source_id for h in ocr_hits}
        for hit in ocr_hits:
            verify_page(hit)
        actual_hit = next(h for h in ocr_hits if h.source_id == target)
        source_page = kb.read(actual_hit.planid, source_id=target, generation_id=generation)
        (samples / "MediaHit.json").write_text(actual_hit.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (samples / "MediaPassage.json").write_text(source_page.model_dump_json(indent=2) + "\n", encoding="utf-8")
        assert not kb.search(query, area="不存在地区", source_types=["visual_chunk", "image_occurrence"])
        picture_hits = kb.search(query, area="江苏", fyp="十四五", top_k=3, source_types=["image_occurrence"]) if args.mode == "hybrid" else []
        if args.mode == "hybrid":
            assert picture_hits and all(h.source_type == "image_occurrence" for h in picture_hits)
        images_checked = []
        for index, page in enumerate(selected + ([source_page] if source_page.source_id not in {p.source_id for p in selected} else [])):
            original = kb.read_image(page.source_id, generation_id=generation)
            cropped = kb.read_image(page.source_id, variant="region", generation_id=generation)
            original_bytes = base64.b64decode(original.data_url.split(",", 1)[1], validate=True)
            crop_bytes = base64.b64decode(cropped.data_url.split(",", 1)[1], validate=True)
            assert hashlib.sha256(original_bytes).hexdigest() == original.sha256 == page.image.sha256
            assert original_bytes == Path(page.image.path).read_bytes()
            with Image.open(io.BytesIO(original_bytes)) as image, Image.open(io.BytesIO(crop_bytes)) as crop:
                assert crop.size == (page.image.bbox_pixels[2] - page.image.bbox_pixels[0], page.image.bbox_pixels[3] - page.image.bbox_pixels[1])
                assert image.crop(tuple(page.image.bbox_pixels)).convert("RGBA").tobytes() == crop.convert("RGBA").tobytes()
            if index == 0:
                (output / "real-region.png").write_bytes(crop_bytes)
            images_checked.append(dict(source_id=page.source_id, kind=page.kind, asset_id=page.asset_id,
                                       original_sha256=original.sha256, bbox_pixels=cropped.bbox_pixels, region_size=[cropped.width, cropped.height]))
        for hit in picture_hits:
            picture = kb.read_image(hit.source_id, generation_id=generation)
            assert picture.source_image_sha256 == hit.image.sha256
        image_summary = kb.read_image(target, variant="region", generation_id=generation).model_dump(exclude={"data_url"})
        image_summary["excerpt_notice"] = "真实read_image响应摘要；省略data_url，完整调用会返回经核验的图片数据"
        (samples / "ImageData摘要.json").write_text(json.dumps(image_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        span = actual_hit.ocr_range
        assert kb.verify_media_quote(target, actual_hit.content[:20], [[span.char_start, span.char_end]], generation_id=generation)
        assert not kb.verify_media_quote(target, "完全不在OCR中的引句987654321", [[span.char_start, span.char_end]])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: kb.read_image(target, variant="region") if i % 2 else kb.read(actual_hit.planid, source_id=target), range(12)))
        assert all(results)
        directory = kb.read(actual_hit.planid)
        assert any(p.source_id == actual_hit.occurrence_id for p in directory.media_sources)
        after = kb.stats()
        for key in ("generation_id", "revision", "worker_thread_id", "runtime_instance_id", "metadata_loads", "bm25_loads", "dense_loads", "media_index_loads", "image_assets_verified", "snapshot_loads"):
            assert before[key] == after[key], key
    db.close()
    assert before_hash == digest_file(path)
    report = dict(timestamp=datetime.now(timezone.utc).isoformat(), mode=args.mode, contract_version="proposal-v0.4", passed=True,
                  startup_seconds=round(startup, 3), elapsed_seconds=round(time.perf_counter() - start, 3), peak_working_set_gib=windows_peak_working_set(),
                  before=before, after=after, sources_read=len(sources), original_occurrences=sum(r["source_type"] == "image_occurrence" for r in sources.values()),
                  visual_chunks=sum(r["source_type"] == "visual_chunk" for r in sources.values()), pages_read=pages, long_ocr_units_reconstructed=long_units,
                  source_units_without_markdown_anchor=unanchored, source_units_without_ocr=no_ocr, source_units_with_pending_recognition=pending,
                  visual_kinds=kinds, real_ocr_query=dict(query=query, hits=len(ocr_hits), expected_source_rank=next(i + 1 for i, h in enumerate(ocr_hits) if h.source_id == target)),
                  original_image_query_hits=[h.source_id for h in picture_hits], images_checked=images_checked,
                  concurrent_requests=12, metadata_database_unchanged=True, agent_modified=False, ocr_rerun=False, vectors_rebuilt=False,
                  answer_accuracy_evaluated=False)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(passed=True, mode=args.mode, sources_read=len(sources), images_checked=len(images_checked), elapsed_seconds=report["elapsed_seconds"])), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
