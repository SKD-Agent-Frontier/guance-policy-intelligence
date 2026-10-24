"""独立真实图片/SQLite/BM25/Qdrant夹具；生产模型另由完整库验收验证。"""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import uuid

import numpy as np
from PIL import Image
import pytest

from guance_rag import RagKnowledgeBase
from guance_rag.cli import respond
from guance_rag.errors import BackendError, InvalidArgument, KBNotFound
from guance_rag.legacy import component


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def put_object(db, kind, value):
    text = canonical(value)
    db.execute("INSERT OR REPLACE INTO objects VALUES(?,?,?,?,?)", (value["id"], value.get("document_id"), kind, text, hashlib.sha256(text.encode()).hexdigest()))


def build_lexical(project, generation):
    with sqlite3.connect(project / ".runtime/local/metadata.sqlite3") as db:
        pairs = db.execute("SELECT r.id,r.text FROM members m JOIN representations r ON r.id=m.representation_id WHERE m.generation=? AND r.text IS NOT NULL ORDER BY r.id", (generation,)).fetchall()
    index = component(project, "bm25s_index").BM25SLexical(project / ".runtime/local/bm25")
    index.build(generation, pairs)
    index.close()


@pytest.fixture
def media_options(options):
    project = options.store_project
    prepared = project / "g1"
    output = project / "media-output"
    output.mkdir()
    did = "doc_media"
    header = "---\r\ntitle: 媒体测试\r\n---\r\n"
    body = " \r\n# 图表\r\n![养老服务表](media/table.png)\r\n![未识别照片](media/photo.png)\r\n"
    text = header + body
    snapshot = prepared / "doc_media.md"
    snapshot.write_bytes(text.encode())
    doc = dict(id=did, planid="2001", body_start=len(header), snapshot=snapshot.name, source_path="original/doc_media.md",
               source_hash=hashlib.sha256(text.encode()).hexdigest(), metadata=dict(title="媒体测试规划", area_name="苏州市", fyp_name="十四五"))
    with (prepared / "documents.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n" + canonical(doc) + "\n")
    db = sqlite3.connect(project / ".runtime/local/metadata.sqlite3")
    encoded = canonical(doc)
    db.execute("INSERT INTO documents VALUES(?,?,?,?,?)", (did, "2001", doc["source_hash"], encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    for sid, level in [("section_root_media", 0), ("section_media", 1)]:
        put_object(db, "structure", dict(id=sid, document_id=did, start=len(header) + (3 if level else 0), end=len(text), level=level, path=["图表"] if level else []))
    manifest = dict(prepared_path=str(prepared), media_path=str(output), collection="fixture")
    db.execute("INSERT INTO releases VALUES(?,?,?,?,?)", ("g_media", "ready", canonical(manifest), None, "fixture"))
    db.execute("INSERT INTO members SELECT 'g_media',representation_id,payload FROM members WHERE generation='g1'")
    db.execute("UPDATE runtime SET generation='g_media',revision=2")
    assets = []
    for number, (aid, occurrence_id, marker) in enumerate([
        ("asset_table", "occurrence_table", "![养老服务表](media/table.png)"),
        ("asset_photo", "document_media_occurrence_photo", "![未识别照片](media/photo.png)"),
    ]):
        image = Image.new("RGB", (20, 10))
        image.putdata([((x * 9 + number) % 256, (y * 20) % 256, 60) for y in range(10) for x in range(20)])
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        raw = buffer.getvalue()
        checksum = hashlib.sha256(raw).hexdigest()
        path = prepared / "media" / checksum / "picture.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        asset = dict(id=aid, bytes=len(raw), sha256=checksum, paths=["corpus/media/picture.png"], width=20, height=10, status="registered")
        put_object(db, "media_asset", asset)
        a = text.index(marker)
        occurrence = dict(id=occurrence_id, document_id=did, asset_id=aid, structure_id="section_media", alt="表格" if number == 0 else "未识别照片",
                          status="registered", source_segment=dict(start=a, end=a + len(marker), text=marker))
        if number:
            occurrence.update(source_segment=None, structure_id=None, status="document_only", association_basis="verified_document_directory")
        put_object(db, "media_occurrence", occurrence)
        assets.append(asset)

    heading = "养老补贴标准表"
    table = "|对象|基准补贴|\r\n|老人|100元□|\r\n" * 400
    transcript = heading + "\n\n" + table
    extraction = dict(id="extraction_media", asset_id="asset_table", asset_hash=assets[0]["sha256"], status="pending", image_type="table")
    parent = dict(id="visual_parent_media", document_id=did, asset_id="asset_table", occurrence_id="occurrence_table",
                  extraction_id=extraction["id"], transcript=transcript, transcript_hash=hashlib.sha256(transcript.encode()).hexdigest(), status="pending")
    visual = dict(id="visual_chunk_media", document_id=did, asset_id="asset_table", occurrence_id="occurrence_table", parent_id=parent["id"],
                  extraction_id=extraction["id"], region_id="region_media", bbox=[2, 1, 18, 9], original_size=[20, 10],
                  coordinate_basis="model_estimated_normalized_1000", derived_start=len(heading) + 2, derived_end=len(transcript), text=table,
                  kind="table", status="pending", context_segments=[dict(start=0, end=len(heading), text=heading, role="recognized_heading")])
    for kind, obj in [("visual_extraction", extraction), ("visual_parent", parent), ("visual_chunk", visual)]:
        put_object(db, kind, obj)
    (output / "visual_parents.jsonl").write_text(canonical(parent) + "\n", encoding="utf-8")
    for source, typ, representation_type, words in [
        ("occurrence_table", "image_occurrence", "image", None),
        ("document_media_occurrence_photo", "image_occurrence", "image", None),
        (visual["id"], "visual_chunk", "text", heading + "\n" + table),
        (visual["id"], "visual_chunk", "image", None),
    ]:
        rid = "representation_" + source + "_" + representation_type
        record = dict(id=rid, source_id=source, source_type=typ, document_id=did, document_version_id="version_media",
                      representation_type=representation_type, point_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, rid)))
        payload = dict(planid="2001", document_version_id="version_media", source_type=typ, representation_id=rid,
                       area_name="苏州市", period_key="第十四个五年规划")
        db.execute("INSERT INTO representations VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, source, did, record["point_id"], "input", "profile", canonical(record), words, None, "fixture"))
        db.execute("INSERT INTO members VALUES(?,?,?)", ("g_media", rid, canonical(payload)))
    put_object(db, "media_occurrence", dict(id="occurrence_missing", document_id=did, asset_id=None, status="missing"))
    db.commit()
    rows = db.execute("SELECT record,payload FROM members m JOIN representations r ON r.id=m.representation_id WHERE m.generation='g_media'").fetchall()
    db.close()
    build_lexical(project, "g_media")
    from qdrant_client import QdrantClient, models as m
    client = QdrantClient(path=str(project / ".runtime/local/qdrant"))
    try:
        client.create_collection("fixture", vectors_config={"dense": m.VectorParams(size=4096, distance=m.Distance.COSINE)})
        points = []
        for record, payload in rows:
            record, payload = json.loads(record), json.loads(payload)
            payload.update(source_type=record["source_type"], representation_id=record["id"])
            vector = np.zeros(4096, dtype=np.float32)
            vector[0 if record["source_type"] != "text_chunk" else 1] = 1
            points.append(m.PointStruct(id=record["point_id"], vector={"dense": vector.tolist()}, payload=payload))
        client.upsert("fixture", points=points)
    finally:
        client.close()
    return options


def image_bytes(image):
    return base64.b64decode(image.data_url.split(",", 1)[1], validate=True)


def test_ocr_search_read_pagination_and_quote(media_options):
    with RagKnowledgeBase(media_options) as kb:
        hits = kb.search("基准补贴", area="苏州", fyp="十四五", source_types=["visual_chunk"])
        assert len(hits) == 1 and hits[0].source_id == "visual_chunk_media"
        hit = hits[0]
        assert hit.contract_version == "proposal-v0.4" and hit.content_basis == "ocr_transcript"
        assert "char_start" not in hit.model_dump() and "body_origin" not in hit.model_dump()
        assert hit.reference_span.content == "![养老服务表](media/table.png)"
        assert hit.ocr_contexts[0].content == "养老补贴标准表"
        assert "upstream_status:pending" in hit.quality_warnings
        assert "ocr_status:pending" in hit.quality_warnings
        pieces, offset = [], 0
        while True:
            page = kb.read("2001", source_id=hit.source_id, offset=offset)
            pieces.append(page.content)
            assert len(page.content) <= 6000
            assert page.truncated == (page.next_offset is not None)
            if page.next_offset is None:
                break
            offset = page.next_offset
        assert "".join(pieces) == "|对象|基准补贴|\r\n|老人|100元□|\r\n" * 400
        a = hit.ocr_range.char_start
        assert kb.verify_media_quote(hit.source_id, hit.content[:8], [[a, a + 4], [a + 4, a + 8]])
        assert not kb.verify_media_quote(hit.source_id, hit.content[:8], [[a, a + 4]])
        assert not kb.search("基准补贴", source_types=["text_chunk"])
        assert not kb.search("基准补贴", area="湖南", source_types=["visual_chunk"])
        assert kb.search("基准补贴")[0].source_type == "visual_chunk"
        chapter = kb.read("2001", section_idx=1)
        assert {p.source_id for p in chapter.media_sources} == {"occurrence_table"}
        assert all(p.reference_complete for p in chapter.media_sources)
        directory = kb.read("2001")
        assert len(directory.media_sources) == 2


def test_original_and_region_bytes_use_stored_pixel_bbox(media_options):
    with RagKnowledgeBase(media_options) as kb:
        page = kb.read("2001", source_id="visual_chunk_media")
        original = kb.read_image(page.source_id)
        cropped = kb.read_image(page.source_id, variant="region")
        raw = image_bytes(original)
        assert hashlib.sha256(raw).hexdigest() == original.source_image_sha256 == page.image.sha256
        assert raw == Path(page.image.path).read_bytes()
        assert (cropped.width, cropped.height) == (16, 8)
        assert cropped.bbox_pixels == [2, 1, 18, 9]
        with Image.open(io.BytesIO(raw)) as image, Image.open(io.BytesIO(image_bytes(cropped))) as region:
            assert region.tobytes() == image.crop((2, 1, 18, 9)).tobytes()
        before = kb.stats()
        with ThreadPoolExecutor(3) as pool:
            assert all(pool.map(lambda _: kb.read_image(page.source_id).source_image_sha256 == original.sha256, range(6)))
        after = kb.stats()
        for key in ("media_index_loads", "image_assets_verified", "worker_thread_id", "runtime_instance_id", "metadata_loads", "bm25_loads"):
            assert before[key] == after[key]


def test_no_ocr_does_not_hide_existing_original(media_options):
    with RagKnowledgeBase(media_options) as kb:
        page = kb.read("2001", source_id="document_media_occurrence_photo")
        assert page.recognition_status == "unavailable" and page.ocr_range is None
        assert page.content_basis == "image_alt" and page.content == "未识别照片"
        assert "ocr_unavailable" in page.quality_warnings
        assert page.reference_span is None and page.section_id is None
        assert page.association_basis == "verified_document_directory"
        assert "document_only_image_association" in page.quality_warnings
        assert kb.read_image(page.source_id).byte_length > 0
        assert not kb.verify_media_quote(page.source_id, page.content, [])
        assert not kb.search("照片", source_types=["image_occurrence"])
        with pytest.raises(InvalidArgument):
            kb.read_image(page.source_id, variant="region")
        with pytest.raises(KBNotFound):
            kb.read("2001", source_id="occurrence_missing")
        result = respond(kb, {"operation": "read_image", "arguments": {"source_id": "document_media_occurrence_photo"}})
        assert result["status"] == "ok" and result["data"]["data_url"].startswith("data:image/png;base64,")


def test_media_argument_and_source_ownership_validation(media_options):
    with RagKnowledgeBase(media_options) as kb:
        for arguments in (dict(source_types=[]), dict(source_types="visual_chunk"), dict(source_types=["unknown"])):
            with pytest.raises(InvalidArgument):
                kb.search("养老", **arguments)
        for arguments in (dict(planid="1001", source_id="visual_chunk_media"),
                          dict(planid="2001", source_id="visual_chunk_media", section_idx=0),
                          dict(planid="2001", source_id="visual_chunk_media", document_id="doc_a"),
                          dict(planid="2001", source_id="visual_chunk_media", chunk_id="chunk_a_0"),
                          dict(planid="2001", source_id="visual_chunk_media", offset=-1),
                          dict(planid="2001", source_id="visual_chunk_media", max_chars=6001)):
            with pytest.raises(InvalidArgument):
                kb.read(**arguments)
        with pytest.raises(KBNotFound):
            kb.read("2001", source_id="visual_chunk_media", generation_id="g0")
        with pytest.raises(InvalidArgument):
            kb.read_image("../secrets.local.json")
        with pytest.raises(InvalidArgument):
            kb.read_image("occurrence_table", variant=["region"])


def test_image_mutation_and_response_budget_are_rejected(media_options):
    with RagKnowledgeBase(media_options) as kb:
        page = kb.read("2001", source_id="document_media_occurrence_photo")
        path = Path(page.image.path)
        original = path.read_bytes()
        path.write_bytes(original + b"tampered")
        with pytest.raises(BackendError, match="发生变化"):
            kb.read_image(page.source_id)
        path.write_bytes(original)
    with RagKnowledgeBase(replace(media_options, max_image_bytes=1)) as kb:
        with pytest.raises(InvalidArgument, match="字节预算"):
            kb.read_image("occurrence_table")


def test_published_bad_region_fails_before_ready(media_options):
    with sqlite3.connect(media_options.store_project / ".runtime/local/metadata.sqlite3") as db:
        obj = json.loads(db.execute("SELECT data FROM objects WHERE id='visual_chunk_media'").fetchone()[0])
        obj["bbox"] = [2, 1, 200, 9]
        put_object(db, "visual_chunk", obj)
    with pytest.raises(BackendError, match="像素范围"):
        RagKnowledgeBase(media_options)


def test_dense_multimodal_prefilter_and_source_fusion(media_options, monkeypatch):
    # 仅查询编码器用确定性夹具向量，实际Qdrant过滤、读图与融合均真实运行。
    import guance_rag.backend as module
    class Encoder:
        def __init__(self, options, manifest):
            self.calls = self.cache_hits = 0
        def vector(self, text):
            vector = np.zeros(4096, dtype=np.float32)
            vector[0] = 1
            return vector
        def close(self):
            pass
    monkeypatch.setattr(module, "QueryEncoder", Encoder)
    with RagKnowledgeBase(replace(media_options, mode="hybrid")) as kb:
        images = kb.search("养老补贴标准", area="苏州", top_k=2, source_types=["image_occurrence"])
        assert {h.source_id for h in images} == {"occurrence_table", "document_media_occurrence_photo"}
        ocr = kb.search("基准补贴", source_types=["visual_chunk"])
        assert len(ocr) == 1 and ocr[0].source_id == "visual_chunk_media"
        assert ocr[0].score <= 2 / 61
        mixed = kb.search("基准补贴", top_k=8)
        assert {h.source_type for h in mixed} == {"text_chunk", "visual_chunk", "image_occurrence"}
        assert kb.stats()["dense_loads"] == 1
        assert kb.stats()["api_version"] == "proposal-v0.4"
    with RagKnowledgeBase(replace(media_options, mode="hybrid", max_search_chars=300)) as kb:
        limited = kb.search("基准补贴", top_k=8)
        assert len(limited) == 1
        assert len(limited[0].content) + sum(len(c.content) for c in limited[0].ocr_contexts) <= 300
        assert "search_evidence_budget_reached" in limited[0].quality_warnings


def test_old_media_remains_readable_when_current_release_has_no_media(media_options):
    project = media_options.store_project
    with sqlite3.connect(project / ".runtime/local/metadata.sqlite3") as db:
        db.execute("INSERT INTO releases SELECT 'g_after',status,manifest,validation,created_at FROM releases WHERE id='g_media'")
        db.execute("INSERT INTO members SELECT 'g_after',representation_id,payload FROM members WHERE generation='g1'")
        db.execute("UPDATE runtime SET generation='g_after',revision=3")
    build_lexical(project, "g_after")
    with RagKnowledgeBase(media_options) as kb:
        page = kb.read("2001", source_id="visual_chunk_media")
        assert page.generation_id == "g_media"
        assert kb.read_image(page.source_id, generation_id=page.generation_id).generation_id == "g_media"
        with pytest.raises(KBNotFound):
            kb.read("2001", source_id=page.source_id, generation_id="g_after")
