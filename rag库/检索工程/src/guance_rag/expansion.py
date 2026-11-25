"""本机增量发布清单。原库和批次各自保留来源版本。"""
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .errors import InvalidArgument

ADMIN_LEVELS = {
    "国家": "national", "国家级": "national", "省": "province", "省级": "province",
    "市": "prefecture", "市级": "prefecture", "区县": "county", "区县级": "county",
    "县": "county", "县级": "county",
}
PERIODS = ("一", "二", "三", "四", "五", "六", "七", "八", "九",
           "十", "十一", "十二", "十三", "十四", "十五")
FILTER_KEYS = {"admin_level": "admin_level", "plantype": "plantype_label",
               "plan_stage": "plan_stage"}


def normalized(key, value):
    if value is None or not str(value).strip():
        return None
    value = str(value).strip()
    if key == "admin_level":
        return ADMIN_LEVELS.get(value, value)
    if key == "fyp_name":
        return period_name(value)
    return value


@lru_cache(maxsize=128)
def period_name(value):
    clean = re.sub(r'''[\s"'“”‘’]''', "", value)
    for n in PERIODS:
        if clean in (f"{n}五", f"第{n}个五年规划", f"第{n}个五年计划"):
            return f"第{n}个五年规划"
    return value


def validate_filters(admin_level, plantype, plan_stage):
    result = dict(admin_level=admin_level, plantype=plantype, plan_stage=plan_stage)
    for name, value in result.items():
        if value is not None and (not isinstance(value, str) or not 1 <= len(value.strip()) <= 50):
            raise InvalidArgument(f"{name} 须为1–50字符")
    return {name: normalized(FILTER_KEYS[name], value) for name, value in result.items()}


def metadata_value(document, key):
    if key in document.get("filter_conflicts", ()):
        return None
    return document["metadata"].get(key)


def matches_metadata(document, filters):
    if filters["admin_level"] is not None:
        if normalized("admin_level", metadata_value(document, "admin_level")) != filters["admin_level"]:
            return False
    if filters["plantype"] is not None:
        values = {normalized(key, metadata_value(document, key))
                  for key in ("plantype", "plantype_label")}
        if filters["plantype"] not in values:
            return False
    if filters["plan_stage"] is not None:
        if normalized("plan_stage", metadata_value(document, "plan_stage")) != filters["plan_stage"]:
            return False
    return True


def database_path(project):
    config = json.loads((project / "config.json").read_text(encoding="utf-8"))
    return project / config["storage"]["directory"] / "metadata.sqlite3"


def read_database(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def current_documents(project):
    """读取发布身份与文档元数据，正文不参与目录预检。"""
    with closing(read_database(database_path(project))) as db:
        db.execute("BEGIN")
        generation, revision, manifest = db.execute(
            "SELECT generation,revision,manifest FROM runtime JOIN releases ON releases.id=generation "
            "WHERE releases.status='ready'"
        ).fetchone()
        prepared = Path(json.loads(manifest)["prepared_path"])
        with (prepared / "documents.jsonl").open(encoding="utf-8") as source:
            ids = [json.loads(line)["id"] for line in source if line.strip()]
        documents = {}
        for doc_id in ids:
            data, digest = db.execute("SELECT data,data_hash FROM documents WHERE id=?", (doc_id,)).fetchone()
            if hashlib.sha256(data.encode()).hexdigest() != digest:
                raise ValueError("document metadata hash mismatch")
            doc = json.loads(data)
            if doc["planid"] in documents:
                raise ValueError("duplicate planid")
            documents[doc["planid"]] = doc
        return generation, revision, documents


@dataclass(frozen=True)
class ExpansionRelease:
    generation: str
    revision: int
    active: tuple[Path, ...]
    history: dict[str, Path]


def registry_path(base, root=None):
    path = Path(root or os.environ.get("RAG_EXPANSION_ROOT") or base.parent / "rag-md-expansion")
    return path.resolve() / "registry.sqlite3"


def active_shard_count(base, root=None):
    """读取当前批次数以估算启动等待；发布校验仍由检索线程执行。"""
    path = registry_path(base, root)
    if not path.is_file():
        return 0
    try:
        with closing(read_database(path)) as db:
            row = db.execute(
                "SELECT shards FROM activation WHERE EXISTS (SELECT 1 FROM base WHERE project=?) "
                "ORDER BY revision DESC LIMIT 1", (str(base.resolve()),)
            ).fetchone()
            shards = json.loads(row[0]) if row else []
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return 0
    return len(shards) if isinstance(shards, list) else 0


def load_release(base, base_generation, root=None):
    path = registry_path(base, root)
    if not path.is_file():
        return None
    with closing(read_database(path)) as db:
        db.execute("BEGIN")
        owner = db.execute("SELECT project,generation FROM base").fetchone()
        if owner != (str(base.resolve()), base_generation):
            raise ValueError("expansion registry does not match base release")
        selected = db.execute(
            "SELECT generation,revision,shards FROM activation ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        if selected is None:
            return None
        generation, revision, shards = selected
        history = {}
        for (saved,) in db.execute("SELECT shards FROM activation"):
            for token in json.loads(saved):
                if not re.fullmatch(r"md_[0-9a-f]{40}", token):
                    raise ValueError("invalid expansion shard identity")
                location = path.parent / token
                if location.resolve().parent != path.parent:
                    raise ValueError("expansion shard escaped registry")
                history[token] = location
        return ExpansionRelease(generation, revision, tuple(history[s] for s in json.loads(shards)), history)


class ExpansionRegistry:
    def __init__(self, base, base_generation, root):
        path = registry_path(base, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS base (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                project TEXT NOT NULL, generation TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shards (
                id TEXT PRIMARY KEY, summary TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activation (
                revision INTEGER PRIMARY KEY, generation TEXT NOT NULL, shards TEXT NOT NULL
            );
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO base VALUES (1,?,?)",
                            (str(base.resolve()), base_generation))
        if self.db.execute("SELECT project,generation FROM base").fetchone() != (str(base.resolve()), base_generation):
            self.db.close()
            raise ValueError("expansion registry does not match base release")
        self.base_generation = base_generation

    def ready(self, shard, summary):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO shards VALUES (?,?)", (shard, json.dumps(summary)))

    def batches(self):
        return [(shard, json.loads(summary)) for shard, summary in
                self.db.execute("SELECT id,summary FROM shards ORDER BY rowid")]

    def activate(self, shards, base_revision=1):
        data = json.dumps(shards, separators=(",", ":"))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            last = self.db.execute("SELECT revision,generation,shards FROM activation ORDER BY revision DESC LIMIT 1").fetchone()
            if last and last[2] == data:
                return last[:2]
            if any(self.db.execute("SELECT 1 FROM shards WHERE id=?", (shard,)).fetchone() is None
                   for shard in shards):
                raise ValueError("cannot publish an unverified shard")
            revision = (last[0] if last else base_revision) + 1
            generation = ("md_release_" + hashlib.sha256((self.base_generation + data).encode()).hexdigest()
                          if shards else self.base_generation)
            self.db.execute("INSERT INTO activation VALUES (?,?,?)", (revision, generation, data))
        return revision, generation

    def close(self):
        self.db.close()
