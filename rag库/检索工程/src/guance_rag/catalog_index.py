"""Exact substring postings for immutable policy catalog generations."""
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from .expansion import current_documents, database_path, load_release, read_database, registry_path
from .storage import body_origin


def index_path(root, generation):
    digest = hashlib.sha256(generation.encode()).hexdigest()
    return Path(root) / "catalog-index-v1" / (digest + ".sqlite3")


@lru_cache(maxsize=8192)
def _token(char):
    return format(ord(char), "x")


def _phrase(text):
    # One token per code point: FTS phrase matching is exactly str.find,
    # including one-character Chinese words, punctuation, spaces and case.
    return " ".join(map(_token, text))


def matching_ids(path, generation, keyword):
    with closing(read_database(path)) as db:
        if db.execute("SELECT generation FROM release").fetchone() != (generation,):
            raise ValueError("catalog index release mismatch")
        rows = db.execute(
            "SELECT planid FROM postings JOIN docs ON docs.id=postings.rowid "
            "WHERE postings MATCH ?", ('"' + _phrase(keyword) + '"',))
        return {row[0] for row in rows}


def build_index(project, root, check_space=None):
    generation, _, docs = current_documents(project)
    target = index_path(root, generation)
    if target.is_file():
        with closing(read_database(target)) as db:
            if db.execute("SELECT generation FROM release").fetchone() != (generation,):
                raise ValueError("catalog index release mismatch")
        return target
    with closing(read_database(database_path(project))) as db:
        manifest, = db.execute("SELECT manifest FROM releases WHERE id=?", (generation,)).fetchone()
    prepared = Path(json.loads(manifest)["prepared_path"]).resolve()
    if not prepared.is_relative_to(Path(project).resolve()):
        raise ValueError("prepared release outside project")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as tmp:
        temp = Path(tmp.name)
    try:
        with closing(sqlite3.connect(temp)) as db:
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY, planid TEXT NOT NULL UNIQUE)")
            db.execute("CREATE VIRTUAL TABLE postings USING fts5(body, content='')")
            db.execute("CREATE TABLE release(generation TEXT NOT NULL)")
            for idx, (pid, doc) in enumerate(docs.items(), 1):
                path = (prepared / doc["snapshot"]).resolve()
                if not path.is_relative_to(prepared):
                    raise ValueError("catalog source outside frozen snapshot")
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != doc["source_hash"]:
                    raise ValueError("catalog source hash mismatch")
                text = raw.decode("utf-8")
                body = text[body_origin(text, doc["body_start"]):]
                db.execute("INSERT INTO docs VALUES (?,?)", (idx, pid))
                db.execute("INSERT INTO postings(rowid,body) VALUES (?,?)", (idx, _phrase(body)))
                if check_space and idx % 25 == 0:
                    check_space()
            db.execute("INSERT INTO postings(postings) VALUES ('integrity-check')")
            db.execute("INSERT INTO release VALUES (?)", (generation,))
            db.commit()
        if check_space:
            check_space()
        os.replace(temp, target)
        return target
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="预建当前冻结发布的目录字面检索索引")
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    root = registry_path(args.base, args.root).parent
    generation, _, _ = current_documents(args.base)
    release = load_release(args.base, generation, root)
    for project in [args.base, *(release.active if release else ())]:
        print(build_index(project, root))


if __name__ == "__main__":
    main()
