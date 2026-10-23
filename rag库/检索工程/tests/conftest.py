"""小型真实SQLite/BM25产物；不调用模型、不改生产库。"""
import hashlib
import json
import shutil
import sqlite3
import uuid

import pytest

from guance_rag.config import DEFAULT_STORE, RuntimeOptions
from guance_rag.legacy import component


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def store_project(tmp_path):
    project = tmp_path / "store-project"
    project.mkdir()
    shutil.copytree(DEFAULT_STORE / "vector_store", project / "vector_store", ignore=shutil.ignore_patterns("__pycache__"))
    (project / "config.json").write_text(encoded({"storage": {"metadata": "sqlite", "directory": ".runtime/local"}}), encoding="utf-8")
    root = project / ".runtime/local"
    root.mkdir(parents=True)
    db = sqlite3.connect(root / "metadata.sqlite3")
    db.executescript((DEFAULT_STORE / "schema.sql").read_text(encoding="utf-8"))

    def object_row(kind, value):
        text = encoded(value)
        db.execute("INSERT INTO objects VALUES(?,?,?,?,?)", (value["id"], value["document_id"], kind, text, hashlib.sha256(text.encode()).hexdigest()))

    generations = {"g1": [], "g0": []}
    for generation in generations:
        db.execute("INSERT INTO releases VALUES(?,?,?,?,?)", (generation, "ready", "{}", None, "fixture"))
    lexical = []
    definitions = [("a", "1001", "苏州市", "g1", "养老服务😀 空格\r\n" + "完善养老服务设施。\n" * 700),
                   ("b", "1002", "湖南省", "g1", "养老服务\n湖南财政补贴与就业支持。\n"),
                   ("old", "1001", "旧地区", "g0", "旧版养老标准与历史条文。\r\n"),
                   ("empty", "1003", None, "g1", "")]
    for suffix, planid, area, generation, body in definitions:
        header = "---\r\ntitle: 测试\r\n---\r\n"
        leading = " \t\r\n"
        source = header + leading + body
        origin, did = len(header + leading), "doc_" + suffix
        prepared = project / generation
        prepared.mkdir(exist_ok=True)
        snapshot = prepared / (did + ".md")
        snapshot.write_bytes(source.encode())
        doc = dict(id=did, planid=planid, body_start=len(header), snapshot=snapshot.name,
                   source_hash=hashlib.sha256(source.encode()).hexdigest(), source_path="original/" + snapshot.name,
                   metadata=dict(title="测试规划" + suffix, area_name=area, fyp_name="十四五", admin_level="市级", plantype_label="专项规划"))
        text = encoded(doc)
        db.execute("INSERT INTO documents VALUES(?,?,?,?,?)", (did, planid, doc["source_hash"], text, hashlib.sha256(text.encode()).hexdigest()))
        generations[generation].append(doc)
        root_section = "section_root_" + suffix
        own_section = "section_" + suffix
        for sid, level, path in [(root_section, 0, []), (own_section, 1, ["养老保障"])]:
            object_row("structure", dict(id=sid, document_id=did, start=len(header) if level == 0 else origin, end=len(source), level=level, path=path))
        if not body:
            continue
        parentid = "parent_" + suffix
        span = dict(start=origin, end=len(source), text=body)
        object_row("parent", dict(id=parentid, document_id=did, structure_id=own_section, start=origin,
                                  end=len(source), section_path=["养老保障"], source_segments=[span]))
        for i, (a, b) in enumerate([(origin, min(origin + 20, len(source))), (min(origin + 20, len(source)), len(source))]):
            if a == b:
                continue
            cid = f"chunk_{suffix}_{i}"
            original = source[a:b]
            object_row("chunk", dict(id=cid, document_id=did, parent_id=parentid,
                                     source_segments=[dict(start=a, end=b, text=original)],
                                     retrieval_text="检索前缀：" + original, status="pending" if suffix == "a" else "ready",
                                     reasons=["fixture_pending"] if suffix == "a" else [],
                                     required_relation_ids=["relation_not_registered"] if suffix == "a" else []))
            for duplicate in range(2 if suffix == "a" and i == 0 else 1):
                rid = f"representation_{suffix}_{i}_{duplicate}"
                record = dict(id=rid, source_id=cid, source_type="text_chunk", document_id=did,
                              document_version_id="version_" + suffix, point_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, rid)))
                payload = dict(planid=planid, document_version_id="version_" + suffix, area_name=area, period_key="第十四个五年规划")
                db.execute("INSERT INTO representations VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (rid, cid, did, record["point_id"], "input", "profile", encoded(record), original, None, "fixture"))
                db.execute("INSERT INTO members VALUES(?,?,?)", (generation, rid, encoded(payload)))
                if generation == "g1":
                    lexical.append((rid, original))
        if suffix == "a":
            unit = dict(id="unit_condition", document_id=did, start=origin, end=origin + 8, text=source[origin:origin + 8])
            object_row("unit", unit)
            for suffix2, target, status in [("ok", unit["id"], "resolved"), ("missing", None, "pending")]:
                rel = dict(id="relation_" + suffix2, document_id=did, required=True, kind="condition", target_id=target, status=status)
                object_row("relation", rel)
                db.execute("INSERT INTO links VALUES(?,?,?,?)", (rel["id"], parentid, target, status))
            object_row("parent_context", dict(id="parent_context_a", document_id=did, member_chunk_ids=["chunk_a_0"],
                                             source_segments=[dict(start=origin, end=origin + 9, text=source[origin:origin + 9])]))
    for generation, docs in generations.items():
        path = project / generation
        (path / "documents.jsonl").write_text("\n".join(encoded(d) for d in docs), encoding="utf-8")
        db.execute("UPDATE releases SET manifest=? WHERE id=?", (encoded(dict(prepared_path=str(path), collection="fixture")), generation))
    db.execute("INSERT INTO runtime VALUES(1,'g1',1)")
    db.commit()
    db.close()
    builder = component(project, "bm25s_index").BM25SLexical(root / "bm25")
    builder.build("g1", lexical)
    builder.close()
    return project


@pytest.fixture
def options(store_project, tmp_path):
    return RuntimeOptions(store_project=store_project, cache_directory=tmp_path / "query-cache", mode="bm25")
