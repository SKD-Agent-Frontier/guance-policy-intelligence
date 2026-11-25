"""政策文献目录：只读冻结发布快照，提供目录、字面检索、统计、详情与原文分页。

数据来源固定为 RAG 运行库当前发布（runtime 表中的 generation）：
- `metadata.sqlite3` 的 releases/documents/objects 表；
- release manifest 指向的 prepared 目录中的冻结原文快照（snapshot）。

不初始化 ReadStore 的全文索引（真实库约 53 秒 / 365MB），只加载目录所需字段与
原文，初始化约 1 秒、内存约 60MB，随后所有查询都在进程内存中完成。
不写任何文件、不修改发布指针、不调用模型。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path

from .policy_format import read_original_html_headings
from .policy_preview import search_preview
from .policy_quality import body_status
from .policy_toc import printed_toc_range

PLANID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
IMAGE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,160}\Z")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_QUERY_CHARS = 100
MAX_FILTER_CHARS = 50
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20
SNIPPET_RADIUS = 60          # 命中片段左右各取 60 个字符
MAX_SNIPPETS_PER_FIELD = 1   # 每个字段只返回首个命中片段
MAX_CONTENT_LIMIT = 20000    # 单次正文读取上限
DEFAULT_CONTENT_LIMIT = 4000

# 与 guance_rag.storage.body_origin 同语义：跳过 BOM 与正文前的空白。
FYP_ALIASES = {f"{n}五": f"第{n}个五年规划" for n in
               ("一", "二", "三", "四", "五", "六", "七", "八", "九",
                "十", "十一", "十二", "十三", "十四", "十五")}
PLANNING_YEARS = {"第十个五年规划": 2001, "第十一个五年规划": 2006,
                  "第十二个五年规划": 2011, "第十三个五年规划": 2016,
                  "第十四个五年规划": 2021, "第十五个五年规划": 2026}
PLANNING_ORDINALS = re.compile(r"第([一二三四五六七八九十]{1,2})个五年(?:规划|计划)\Z")
TITLE_YEARS = re.compile(r"(?<!\d)((?:19|20)\d{2})年")
TITLE_YEAR_TOKENS = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
VISION_YEAR = re.compile(r"((?:19|20)\d{2})年\s*远景")
YEAR_RANGE = re.compile(r"(?:19|20)\d{2}\s*[—–―~～至到-]\s*(?:19|20)\d{2}年?")
ADMIN_LEVEL_ORDER = {"国家": 0, "国家级": 0, "省": 1, "省级": 1,
                     "市": 2, "市级": 2, "区县": 3, "区县级": 3,
                     "县": 3, "县级": 3}
ADMIN_LEVEL_VALUES = ("national", "province", "prefecture", "county", "unclassified")
ADMIN_LEVEL_LABELS = dict(zip(ADMIN_LEVEL_VALUES, ("国家级", "省级", "市级", "区县级", "未分类")))
ADMIN_LEVEL_NORMALIZATION = {"国家": "national", "国家级": "national", "省": "province",
                             "省级": "province", "市": "prefecture", "市级": "prefecture",
                             "区县": "county", "区县级": "county", "县": "county", "县级": "county"}
REGION_STATUSES = ("mapped", "unclassified", "not_applicable")


class CatalogUnavailable(RuntimeError):
    """当前发布档案不可用：配置缺失、发布未就绪或快照校验失败。"""


class InvalidParameter(ValueError):
    """请求参数不合法；消息可直接返回给调用方。"""


@dataclass(frozen=True)
class SectionMeta:
    index: int
    level: int
    path: str
    start: int      # 相对正文起点的码点偏移
    end: int

    def as_dict(self) -> dict:
        return {"index": self.index, "level": self.level, "path": self.path,
                "start": self.start, "end": self.end}


@dataclass(frozen=True)
class FrozenMarkdown:
    path: Path
    database: Path
    document_id: str
    source_hash: str
    body_start: int

    def read(self) -> tuple[str, int]:
        try:
            raw = self.path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != self.source_hash:
                raise CatalogUnavailable("冻结原文校验失败")
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise CatalogUnavailable("冻结原文不可读") from exc
        origin = body_origin(text, self.body_start)
        return text[origin:], origin

    def sections(self, origin: int, body_size: int) -> tuple[SectionMeta, ...]:
        did = self.document_id
        try:
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                return _read_sections(db, {did}, {did: origin}, {did: origin + body_size}).get(did, ())
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise CatalogUnavailable("冻结章节不可读") from exc


@dataclass(frozen=True)
class PolicyRecord:
    planid: str
    title: str | None
    area: str | None
    fyp: str | None
    plantype_label: str | None
    document_id: str
    body: str
    sections: tuple[SectionMeta, ...]
    admin_level: str | None = None
    plan_stage: str | None = None
    plantype_code: str | None = None
    parent_area: str | None = None
    frozen: FrozenMarkdown | None = None
    text_only: bool = False
    generation_id: str | None = None
    source_hash: str | None = None

    @property
    def year(self) -> int | None:
        return reference_year(self.title, self.fyp)

    @property
    def body_status(self) -> str:
        return body_status(self.body_text, self.title)

    @property
    def body_text(self) -> str:
        return self.frozen.read()[0] if self.frozen else self.body

    @property
    def has_body(self) -> bool:
        return self.text_only or bool(self.body_text.strip())


@dataclass(frozen=True)
class CatalogSnapshot:
    generation_id: str
    revision: int
    partial: bool | None
    records: tuple[PolicyRecord, ...]
    media_roots: tuple[Path, ...] = ()
    base_snapshot: CatalogSnapshot | None = None
    historical_projects: dict[str, Path] = field(default_factory=dict)
    literal_indexes: dict[str, Path] = field(default_factory=dict)

    @property
    def total_documents(self) -> int:
        return len(self.records)


def body_origin(text: str, start) -> int:
    if type(start) is not int or not 0 <= start <= len(text):
        raise CatalogUnavailable("正文起点无效")
    if start == 0 and text.startswith("\ufeff"):
        start = 1
    while start < len(text) and text[start].isspace():
        start += 1
    return start


def normalize_fyp(value: str | None) -> str | None:
    """把展示名别名规范到 metadata 中的规划期名称；不做日期推断。"""
    if value is None:
        return None
    key = "".join(char for char in value if not char.isspace()
                  and char not in '"\'“”‘’')
    alias = FYP_ALIASES.get(key)
    if alias:
        return alias
    planning_period = PLANNING_ORDINALS.fullmatch(key)
    if planning_period:
        return f"第{planning_period.group(1)}个五年规划"
    return value


def reference_year(title: str | None, fyp: str | None) -> int | None:
    """返回目录排序/展示用参考年份；不从正文或远景目标推断。"""
    if title:
        vision_years = set(VISION_YEAR.findall(title))
        years = [int(match) for match in TITLE_YEARS.findall(title)
                 if match not in vision_years]
        non_vision_years = set(TITLE_YEAR_TOKENS.findall(title)) - vision_years
        if (not YEAR_RANGE.search(title) and len(set(years)) == 1
                and len(non_vision_years) <= 1):
            return years[0]
    if fyp:
        normalized = normalize_fyp(fyp)
        match = PLANNING_ORDINALS.fullmatch(normalized or "")
        if match:
            ordinal = match.group(1)
            canonical = f"第{ordinal}个五年规划"
            return PLANNING_YEARS.get(canonical)
    return None


def _sort_key(record: PolicyRecord, q: str | None) -> tuple:
    title_hit = bool(q is not None and record.title and q in record.title)
    year = record.year
    admin_rank = ADMIN_LEVEL_ORDER.get(record.admin_level or "", len(ADMIN_LEVEL_ORDER))
    report = any(value == "政府工作报告"
                 for value in (record.fyp, record.plantype_label))
    report = report or bool(record.title and record.title.rstrip().endswith("政府工作报告"))
    return (0 if q is None or title_hit else 1,
            year is None, -(year or 0), admin_rank, not report, record.planid)


def _relative(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise CatalogUnavailable("冻结快照路径越界")
    return resolved


def _read_sections(conn: sqlite3.Connection, document_ids: set[str],
                   origins: dict[str, int], lengths: dict[str, int]) -> dict[str, tuple[SectionMeta, ...]]:
    grouped: dict[str, list[SectionMeta]] = {did: [] for did in document_ids}
    sql = "SELECT document_id,data,data_hash FROM objects WHERE kind='structure'"
    rows = (conn.execute(sql + " AND document_id=?", tuple(document_ids)) if len(document_ids) == 1
            else conn.execute(sql))
    for row in rows:
        did = row["document_id"]
        if did not in grouped:
            continue
        raw = row["data"]
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["data_hash"]:
            raise CatalogUnavailable("章节结构校验失败")
        data = json.loads(raw)
        # Only genuine heading structures form the navigation outline.
        # RAG structure objects also contain the root and numbered articles;
        # article items are kept only as a fallback for documents with no
        # chapter headings (e.g. regulations made entirely of numbered clauses).
        if data.get("kind") not in ("section", "article"):
            continue
        origin = origins[did]
        start = max(int(data["start"]), origin) - origin
        end = min(int(data["end"]), lengths[did]) - origin
        if end <= start:
            continue
        path = data.get("path") or []
        label = " / ".join(str(part) for part in path) if isinstance(path, list) else ""
        grouped[did].append(SectionMeta(
            index=0, level=int(data.get("level") or 0), path=label or (data.get("title") or ""),
            start=start, end=end))
    sections: dict[str, tuple[SectionMeta, ...]] = {}
    for did, items in grouped.items():
        # Give users a usable chapter index instead of thousands of individual
        # article clauses. An article-only policy remains navigable.
        chapters = [item for item in items if 1 <= item.level <= 6]
        candidates = chapters if chapters else [item for item in items if item.level == 7]
        ordered = sorted(candidates, key=lambda item: (item.start, item.level, item.end))
        seen: set[tuple[int, str]] = set()
        unique: list[SectionMeta] = []
        for item in ordered:
            label = item.path.split(" / ")[-1].strip().lstrip("#").strip()
            if not label or len(label) > 100 or "|" in label or "![" in label:
                continue
            identity = (item.start, label)
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(item)
        sections[did] = tuple(
            SectionMeta(index=index, level=item.level, path=item.path,
                        start=item.start, end=item.end)
            for index, item in enumerate(unique))
    return sections


def _with_indexes(snapshot: CatalogSnapshot, root: Path) -> CatalogSnapshot:
    if not (root / "catalog-index-v1").is_dir():
        return snapshot
    from guance_rag.catalog_index import index_path
    paths = {g: index_path(root, g) for g in {r.generation_id for r in snapshot.records} if g}
    return replace(snapshot, literal_indexes={g: p for g, p in paths.items() if p.is_file()})


def load_published_catalog(project: str | Path, *, expand: bool = True, release=None) -> CatalogSnapshot:
    """目录和检索使用同一 SQLite 累计发布，独立保留原库的地区映射身份。"""
    project = Path(project).resolve()
    expansion_root = Path(os.environ.get("RAG_EXPANSION_ROOT") or project.parent / "rag-md-expansion")
    if not expand or (release is None and not (expansion_root / "registry.sqlite3").is_file()):
        return _with_indexes(_load_project(project), expansion_root)
    try:
        from guance_rag.expansion import database_path, load_release, read_database
        if release is None:
            with closing(read_database(database_path(project))) as db:
                generation = db.execute("SELECT generation FROM runtime").fetchone()[0]
            release = load_release(project, generation)
    except (ImportError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise CatalogUnavailable("累计发布清单不可用") from exc
    if release is None:
        return _with_indexes(_load_project(project), expansion_root)
    base = _load_project(project, lazy=True)
    records = {record.planid: record for record in base.records}
    for shard in release.active:
        current = _load_project(shard, lazy=True)
        if current.generation_id != shard.name:
            raise CatalogUnavailable("增量发布身份不一致")
        for record in current.records:
            if record.planid in records:
                raise CatalogUnavailable("累计发布存在重复政策编号")
            records[record.planid] = record
    snapshot = CatalogSnapshot(release.generation, release.revision,
                           True if release.active else base.partial,
                           tuple(sorted(records.values(), key=lambda record: record.planid)),
                           base.media_roots, base, release.history)
    return _with_indexes(snapshot, expansion_root)


def _load_project(project: str | Path, *, lazy: bool = False) -> CatalogSnapshot:
    """读取当前已发布的冻结知识库；失败一律抛 CatalogUnavailable（不伪造数据）。"""
    project_path = Path(project).resolve()
    config_path = project_path / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogUnavailable("当前发布档案不可用") from exc
    try:
        root = _relative(project_path / config["storage"]["directory"], project_path)
    except (KeyError, TypeError) as exc:
        raise CatalogUnavailable("当前发布档案配置不完整") from exc
    database = root / "metadata.sqlite3"
    if not database.is_file():
        raise CatalogUnavailable("当前发布档案不可用")
    conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        runtime_row = conn.execute("SELECT generation,revision FROM runtime").fetchone()
        if runtime_row is None:
            raise CatalogUnavailable("没有可用的冻结发布")
        generation, revision = runtime_row["generation"], int(runtime_row["revision"])
        release = conn.execute(
            "SELECT manifest FROM releases WHERE id=? AND status='ready'",
            (generation,)).fetchone()
        if release is None:
            raise CatalogUnavailable("当前发布尚未就绪")
        manifest = json.loads(release["manifest"])
        prepared = _relative(Path(manifest["prepared_path"]), project_path)
        documents_file = prepared / "documents.jsonl"
        if not documents_file.is_file():
            raise CatalogUnavailable("当前发布缺少文档清单")
        document_ids = set()
        with documents_file.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                did = json.loads(line)["id"]
                if not isinstance(did, str) or did in document_ids:
                    raise CatalogUnavailable("当前发布文档清单无效")
                document_ids.add(did)
        records: list[PolicyRecord] = []
        planids: set[str] = set()
        origins: dict[str, int] = {}
        lengths: dict[str, int] = {}
        for did in document_ids:
            row = conn.execute("SELECT data,data_hash FROM documents WHERE id=?",
                               (did,)).fetchone()
            if row is None:
                raise CatalogUnavailable("当前发布缺少文档记录")
            raw_data = row["data"]
            if hashlib.sha256(raw_data.encode("utf-8")).hexdigest() != row["data_hash"]:
                raise CatalogUnavailable("来源对象内容校验失败")
            data = json.loads(raw_data)
            snapshot = _relative(prepared / data["snapshot"], prepared)
            frozen = FrozenMarkdown(snapshot, database, did, data["source_hash"], data["body_start"])
            body = ""
            if not lazy:
                body, origin = frozen.read()
                origins[did], lengths[did] = origin, len(body) + origin
            meta = {key: value for key, value in data["metadata"].items()
                    if key not in data.get("filter_conflicts", ())}
            planid = data["planid"]
            if not isinstance(planid, str) or planid in planids:
                raise CatalogUnavailable("当前发布存在重复或无效的政策编号")
            planids.add(planid)
            records.append(PolicyRecord(
                planid=planid,
                title=(meta.get("title") or None),
                area=(meta.get("area_name") or None),
                fyp=(meta.get("fyp_name") or None),
                plantype_label=(meta.get("plantype_label") or None),
                document_id=did,
                body=body,
                sections=(),
                admin_level=(meta.get("admin_level") or None),
                plan_stage=(str(meta["plan_stage"]).strip() if meta.get("plan_stage") else None),
                plantype_code=(str(meta["plantype"]).strip() if meta.get("plantype") else None),
                parent_area=(meta.get("parent_area_name") or None),
                frozen=frozen if lazy else None,
                text_only=manifest.get("text_only", False),
                generation_id=generation,
                source_hash=data["source_hash"],
            ))
        if not lazy:
            sections = _read_sections(conn, document_ids, origins, lengths)
            records = [replace(record, sections=sections.get(record.document_id, ())) for record in records]
        records.sort(key=lambda record: record.planid)
    except CatalogUnavailable:
        raise
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        raise CatalogUnavailable("当前发布档案不可用") from exc
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    # Only read from existing clean corpus / frozen media, never mutate sources.
    # The clean corpus may be mounted separately from the restored RAG runtime.
    clean_root = os.environ.get("GUANCE_POLICY_CLEAN_ROOT", "").strip()
    configured_media = os.environ.get("GUANCE_POLICY_MEDIA_ROOT", "").strip()
    repo_root = Path(__file__).resolve().parents[3]
    media_roots = tuple(dict.fromkeys(
        p.resolve() for p in (
            *((Path(clean_root),) if clean_root else ()),
            *((Path(configured_media),) if configured_media else ()),
            repo_root / "语料库" / "样例数据（干净版）",
            repo_root / "语料库" / "Agent标准引用库",
            prepared,
        )
    ))
    return CatalogSnapshot(generation_id=generation, revision=revision,
                           partial=manifest.get("partial"), records=tuple(records),
                           media_roots=media_roots)


class PolicyCatalogService:
    """只读目录服务：所有查询基于加载完成的内存快照，线程安全（快照不可变）。"""

    def __init__(self, snapshot: CatalogSnapshot, *, region_payload: dict | None = None,
                 region_mapping: dict[str, dict[str, str | None]] | None = None,
                 region_mapping_version: str | None = None):
        self.snapshot = snapshot
        self.region_payload = region_payload
        self.region_mapping = region_mapping
        self.region_mapping_version = region_mapping_version
        self._by_planid = {record.planid: record for record in snapshot.records}
        self._by_title: dict[str, list[PolicyRecord]] = {}
        for record in snapshot.records:
            if record.title:
                self._by_title.setdefault(record.title.strip("“”"), []).append(record)
        if len(self._by_planid) != len(snapshot.records):
            raise CatalogUnavailable("当前发布存在重复政策编号")
        self._region_nodes: dict[str, dict] = {}
        self._region_parent: dict[str, str | None] = {}
        self._region_descendants: dict[str, set[str]] = {}
        if region_payload:
            def index_nodes(nodes: list[dict], parent: str | None = None):
                for node in nodes:
                    code = node["code"]
                    self._region_nodes[code] = node
                    self._region_parent[code] = parent
                    index_nodes(node["children"], code)
            index_nodes(region_payload["data"])
            def collect_descendants(node: dict) -> set[str]:
                descendants = {node["code"]}
                for child in node["children"]:
                    descendants.update(collect_descendants(child))
                self._region_descendants[node["code"]] = descendants
                return descendants
            for root in region_payload["data"]:
                collect_descendants(root)

    def dataset(self) -> dict:
        return {"generation_id": self.snapshot.generation_id,
                "revision": self.snapshot.revision,
                "total_documents": self.snapshot.total_documents,
                "partial": self.snapshot.partial,
                "source": "current_published_frozen_release",
                "region_version": self.region_payload.get("region_version") if self.region_payload else None,
                "region_mapping_version": self.region_mapping_version}

    def _legacy_dataset(self) -> dict:
        return {key: value for key, value in self.dataset().items()
                if key not in {"region_version", "region_mapping_version"}}

    def record_by_id(self, planid):
        return self._by_planid.get(planid)

    def records_by_title(self, title):
        return self._by_title.get(title.strip("“”"), ())

    def _keyword_ids(self, records, keyword):
        indexes = self.snapshot.literal_indexes
        hits = set()
        if indexes:
            from guance_rag.catalog_index import matching_ids
            try:
                for generation in {r.generation_id for r in records} & indexes.keys():
                    hits.update(matching_ids(indexes[generation], generation, keyword))
            except (sqlite3.Error, ValueError, OSError) as exc:
                raise CatalogUnavailable("目录索引不可用") from exc
        for record in records:
            if record.title and keyword in record.title:
                hits.add(record.planid)
            elif record.generation_id not in indexes and keyword in record.body_text:
                hits.add(record.planid)
        return hits

    def list_policies(self, *, q: str | None = None, area: str | None = None,
                      fyp: str | None = None, plantype: str | None = None,
                      page: int = 1, page_size: int = DEFAULT_PAGE_SIZE,
                      admin_level: str | None = None, province_code: str | None = None,
                      prefecture_code: str | None = None, county_code: str | None = None,
                      region_status: str | None = None, year: str | int | None = None,
                      fyp_status: str | None = None, plantype_status: str | None = None,
                      plan_stage: str | None = None) -> dict:
        filters, query = self._normalize_filters(
            q=q, area=area, fyp=fyp, plantype=plantype, admin_level=admin_level,
            province_code=province_code, prefecture_code=prefecture_code,
            county_code=county_code, region_status=region_status, year=year,
            fyp_status=fyp_status, plantype_status=plantype_status, plan_stage=plan_stage)
        page, page_size = self._check_paging(page, page_size)
        keyword = filters["q"]
        structured = self._without(filters, "q")
        matched = [record for record in self.snapshot.records if self._matches(record, structured)]
        if keyword:
            hit_ids = self._keyword_ids(matched, keyword)
            matched = [record for record in matched if record.planid in hit_ids]
        matched.sort(key=lambda record: _sort_key(record, keyword))
        total = len(matched)
        start = (page - 1) * page_size
        items = []
        for record in matched[start:start + page_size]:
            body = record.body_text if keyword else ""
            matches = self._match_record(record, keyword, body) if keyword else []
            mapping = (self.region_mapping or {}).get(record.planid)
            items.append({"planid": record.planid, "title": record.title, "area": record.area,
                          "fyp": record.fyp, "plantype_label": record.plantype_label,
                          "plan_stage": record.plan_stage,
                          "year": record.year,
                          "admin_level": self._admin_level(record),
                          "region_code": mapping.get("region_code") if mapping else None,
                          "region_status": mapping.get("region_status") if mapping else None,
                          "matches": matches,
                          "preview": search_preview(body, record.title, filters["q"])
                          if filters["q"] else None})
        return {"dataset": self.dataset(), "query": {**query,
                "match_mode": "literal" if filters["q"] is not None else None},
                "matched_documents": total, "page": page, "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "items": items}

    def facets(self, **params) -> dict:
        if self.region_payload is None and self.snapshot.base_snapshot is None:
            raise CatalogUnavailable("地区树资源不可用")
        if self.region_mapping is None and self.snapshot.base_snapshot is None:
            raise CatalogUnavailable("地区映射尚未就绪")
        base, query = self._normalize_filters(**params)
        if base["q"]:
            base["_keyword_ids"] = self._keyword_ids(self.snapshot.records, base["q"])
        def counts(filters: dict) -> dict[str, int]:
            result: dict[str, int] = {}
            for record in self.snapshot.records:
                if self._matches(record, filters):
                    result[record.planid] = 1
            return result
        def count(filters: dict) -> int:
            return len(counts(filters))
        admin_filters = self._without(base, "admin_level", "region_status", "province_code",
                                      "prefecture_code", "county_code", "area")
        admin_options = [{"value": value, "label": ADMIN_LEVEL_LABELS[value],
                          "count": count({**admin_filters, "admin_level": value})}
                         for value in ADMIN_LEVEL_VALUES]
        year_filters = self._without(base, "year")
        year_values = sorted({record.year for record in self.snapshot.records if record.year is not None}, reverse=True)
        year_options = [{"value": year_value, "label": str(year_value),
                         "count": count({**year_filters, "year": year_value})} for year_value in year_values]
        year_options.append({"value": "unknown", "label": "年份未知",
                             "count": count({**year_filters, "year": "unknown"})})
        fyp_filters = self._without(base, "fyp", "fyp_status")
        fyp_values = sorted({normalize_fyp(r.fyp) for r in self.snapshot.records if r.fyp is not None})
        fyp_options = [{"value": value, "label": value,
                        "count": count({**fyp_filters, "fyp": value})} for value in fyp_values]
        fyp_missing = count({**fyp_filters, "fyp_status": "unclassified"})
        plantype_filters = self._without(base, "plantype", "plantype_status")
        plantype_values = sorted({r.plantype_label for r in self.snapshot.records if r.plantype_label is not None})
        plantype_options = [{"value": value, "label": value,
                             "count": count({**plantype_filters, "plantype": value})} for value in plantype_values]
        plantype_missing = count({**plantype_filters, "plantype_status": "unclassified"})
        region_filter_base = self._without(base, "province_code", "prefecture_code", "county_code",
                                           "region_status", "area")
        province_options = [self._region_option(node, region_filter_base)
                            for node in ((self.region_payload or {}).get("data", ()))
                            if self.region_mapping is not None and node["tier"] == "province"]
        province = base.get("province_code")
        prefecture = base.get("prefecture_code")
        prefecture_options = []
        county_options = []
        if province:
            for node in self._region_nodes[province]["children"]:
                if node["tier"] == "prefecture":
                    prefecture_options.append(self._region_option(node, region_filter_base))
        if prefecture:
            county_nodes = self._region_nodes[prefecture]["children"]
        elif province:
            county_nodes = [node for node in self._region_nodes[province]["children"]
                            if node["tier"] == "county"]
        else:
            county_nodes = []
        county_options = [self._region_option(node, region_filter_base) for node in county_nodes
                          if node["tier"] == "county"]
        region_state_filters = self._without(base, "province_code", "prefecture_code", "county_code",
                                             "region_status", "area")
        region_unc = count({**region_state_filters, "region_status": "unclassified"})
        region_na = count({**region_state_filters, "region_status": "not_applicable"})
        return {"dataset": self.dataset(), "query": query, "facets": {
            "admin_level": {"options": admin_options, "regions": {
                "province_options": province_options, "prefecture_options": prefecture_options,
                "county_options": county_options,
                "unclassified": {"label": "地区未分类", "count": region_unc},
                "not_applicable": {"label": "不适用地区树", "count": region_na}}},
            "year": {"options": year_options},
            "fyp": {"options": fyp_options, "unclassified": {"label": "未分类", "count": fyp_missing}},
            "plantype": {"options": plantype_options,
                         "unclassified": {"label": "未分类", "count": plantype_missing}},
        }}

    def _region_option(self, node: dict, filters: dict) -> dict:
        descendants = self._region_descendants[node["code"]]
        selected = dict(filters)
        selected["region_status"] = "mapped"
        selected["_region_codes"] = descendants
        return {"code": node["code"], "name": node["name"], "source_type": node["source_type"],
                "tier": node["tier"], "count": sum(1 for record in self.snapshot.records
                    if self._matches(record, selected))}

    @staticmethod
    def _without(filters: dict, *keys: str) -> dict:
        return {key: value for key, value in filters.items() if key not in keys}

    @staticmethod
    def _admin_level(record: PolicyRecord) -> str:
        value = record.admin_level if isinstance(record.admin_level, str) else ""
        return ADMIN_LEVEL_NORMALIZATION.get(value or "", "unclassified")

    def _normalize_filters(self, *, q=None, area=None, fyp=None, plantype=None,
                           admin_level=None, province_code=None, prefecture_code=None,
                           county_code=None, region_status=None, year=None, fyp_status=None,
                           plantype_status=None, plan_stage=None) -> tuple[dict, dict]:
        q = self._check_query(q)
        for name, value in (("area", area), ("fyp", fyp), ("plantype", plantype), ("plan_stage", plan_stage)):
            self._check_filter(value, name)
        fyp = normalize_fyp(fyp)
        if admin_level is not None and admin_level not in ADMIN_LEVEL_VALUES:
            raise InvalidParameter("admin_level 值无效")
        if region_status is not None and region_status not in REGION_STATUSES:
            raise InvalidParameter("region_status 值无效")
        if region_status is not None and self.region_mapping is None:
            raise CatalogUnavailable("地区映射尚未就绪")
        if fyp_status not in (None, "unclassified") or plantype_status not in (None, "unclassified"):
            raise InvalidParameter("状态筛选值无效")
        if fyp is not None and fyp_status is not None:
            raise InvalidParameter("fyp 与 fyp_status 不能同时使用")
        if plantype is not None and plantype_status is not None:
            raise InvalidParameter("plantype 与 plantype_status 不能同时使用")
        normalized_year: int | str | None = None
        if year is not None:
            year_text = str(year)
            if year_text == "unknown":
                normalized_year = "unknown"
            elif re.fullmatch(r"\d{4}", year_text) and 1900 <= int(year_text) <= 2099:
                normalized_year = int(year_text)
            else:
                raise InvalidParameter("year 必须为 1900–2099 的四位年份或 unknown")
        codes = {"province_code": province_code, "prefecture_code": prefecture_code,
                 "county_code": county_code}
        if any(value is not None for value in codes.values()):
            if self.region_payload is None or self.region_mapping is None:
                raise CatalogUnavailable("地区映射尚未就绪")
            for key, value in codes.items():
                if value is not None and value not in self._region_nodes:
                    raise InvalidParameter("地区代码无效")
                expected_tier = key.removesuffix("_code")
                if value is not None and self._region_nodes[value]["tier"] != expected_tier:
                    raise InvalidParameter("地区代码层级不符")
            if admin_level == "national" and any(codes.values()):
                raise InvalidParameter("国家级文献不能与地区代码同时筛选")
            if any(codes.values()) and region_status in {"unclassified", "not_applicable"}:
                raise InvalidParameter("地区代码与当前地区状态不能同时筛选")
            if prefecture_code and not province_code:
                province_code = self._ancestor(prefecture_code, "province")
            if county_code:
                actual_prefecture = self._ancestor(county_code, "prefecture")
                actual_province = self._ancestor(county_code, "province")
                if province_code is not None and province_code != actual_province:
                    raise InvalidParameter("地区选择的父级关系冲突")
                if prefecture_code is not None and prefecture_code != actual_prefecture:
                    raise InvalidParameter("地区选择的父级关系冲突")
                province_code = actual_province
                prefecture_code = actual_prefecture
            if prefecture_code and self._ancestor(prefecture_code, "province") != province_code:
                raise InvalidParameter("地区选择的父级关系冲突")
        if region_status == "not_applicable" and admin_level not in (None, "national"):
            # Valid empty intersection; matching remains ordinary AND semantics.
            pass
        filters = {"q": q, "area": area, "fyp": fyp, "plantype": plantype,
                   "admin_level": admin_level, "province_code": province_code,
                   "prefecture_code": prefecture_code, "county_code": county_code,
                   "region_status": region_status, "year": normalized_year,
                   "fyp_status": fyp_status, "plantype_status": plantype_status}
        query = {key: value for key, value in filters.items()}
        if plan_stage is not None:
            filters["plan_stage"] = query["plan_stage"] = plan_stage
        return filters, query

    def _ancestor(self, code: str, tier: str) -> str | None:
        parent = self._region_parent[code]
        while parent is not None:
            if self._region_nodes[parent]["tier"] == tier:
                return parent
            parent = self._region_parent[parent]
        return None

    def _matches(self, record: PolicyRecord, filters: dict) -> bool:
        if filters.get("area") is not None and record.area != filters["area"]:
            return False
        if filters.get("admin_level") is not None and self._admin_level(record) != filters["admin_level"]:
            return False
        mapping = (self.region_mapping or {}).get(record.planid)
        if filters.get("region_status") is not None and (
            mapping is None or mapping["region_status"] != filters["region_status"]
        ):
            return False
        code_set = filters.get("_region_codes")
        if code_set is not None and (mapping is None or mapping.get("region_code") not in code_set):
            return False
        for key in ("province_code", "prefecture_code", "county_code"):
            code = filters.get(key)
            if code is None:
                continue
            if mapping is None or mapping.get("region_status") != "mapped":
                return False
            if key == "county_code":
                if mapping.get("region_code") != code:
                    return False
            elif mapping.get("region_code") not in self._region_descendants[code]:
                return False
        if filters.get("fyp_status") == "unclassified" and record.fyp is not None:
            return False
        if filters.get("fyp") is not None and normalize_fyp(record.fyp) != filters["fyp"]:
            return False
        if filters.get("plantype_status") == "unclassified" and record.plantype_label is not None:
            return False
        if filters.get("plantype") is not None and filters["plantype"] not in (
            record.plantype_label, record.plantype_code
        ):
            return False
        if filters.get("plan_stage") is not None and record.plan_stage != filters["plan_stage"]:
            return False
        if "year" in filters and filters["year"] is not None:
            if filters["year"] == "unknown" and record.year is not None:
                return False
            if isinstance(filters["year"], int) and record.year != filters["year"]:
                return False
        if "_keyword_ids" in filters:
            return record.planid in filters["_keyword_ids"]
        return filters.get("q") is None or self._match_record(record, filters["q"])

    def _source_record(self, planid, generation_id=None, document_id=None) -> PolicyRecord:
        planid = self._check_planid(planid)
        for identity in (generation_id, document_id):
            if identity is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
                raise InvalidParameter("来源版本标识无效")
        record = self._by_planid.get(planid)
        actual = record.generation_id if record and record.generation_id else self.snapshot.generation_id
        if generation_id is not None and actual != generation_id:
            project = self.snapshot.historical_projects.get(generation_id)
            if project is None:
                raise LookupError("未找到该原文版本")
            historical = _load_project(project, lazy=True)
            if historical.generation_id != generation_id:
                raise CatalogUnavailable("历史发布身份不一致")
            record = next((item for item in historical.records if item.planid == planid), None)
        if record is None or (document_id is not None and document_id != record.document_id):
            raise LookupError("未找到该原文版本")
        return record

    def get_policy(self, planid: str, *, generation_id=None, document_id=None) -> dict:
        record = self._source_record(planid, generation_id, document_id)
        body, origin = record.frozen.read() if record.frozen else (record.body, 0)
        sections = record.frozen.sections(origin, len(body)) if record.frozen else record.sections
        return {"dataset": self._legacy_dataset(),
                "policy": {"planid": record.planid, "title": record.title,
                           "area": record.area, "fyp": record.fyp,
                           "plantype_label": record.plantype_label,
                           "plan_stage": record.plan_stage,
                           "generation_id": record.generation_id,
                           "document_id": record.document_id,
                           "source_hash": record.source_hash,
                           "year": record.year,
                           "body_chars": len(body),
                           "printed_toc": printed_toc_range(body),
                           "body_status": body_status(body, record.title),
                           "sections": [section.as_dict() for section in sections]}}

    def read_content(self, planid: str, *, offset: int, limit: int, generation_id=None, document_id=None) -> dict:
        if type(offset) is not int or offset < 0:
            raise InvalidParameter("offset 必须为非负整数")
        if type(limit) is not int or not 1 <= limit <= MAX_CONTENT_LIMIT:
            raise InvalidParameter(f"limit 必须为 1–{MAX_CONTENT_LIMIT} 的整数")
        record = self._source_record(planid, generation_id, document_id)
        body = record.body_text
        total = len(body)
        if offset > total:
            raise InvalidParameter("offset 超出正文长度")
        end = min(offset + limit, total)
        return {"dataset": self._legacy_dataset(), "planid": record.planid,
                "title": record.title, "offset": offset, "limit": limit,
                "total_chars": total, "has_more": end < total,
                "body_status": record.body_status,
                "text": body[offset:end]}

    @staticmethod
    def _reading_boundary(body: str, target: int, page_chars: int = 4000) -> int:
        """Find a repeatable nearby paragraph/line boundary for a display page.

        This is a *view* boundary only; read_content() and all evidence offsets
        retain their original code-point semantics.
        """
        total = len(body)
        if target <= 0:
            return 0
        if target >= total:
            return total
        # Adjacent display page searches must not overlap, or a short final
        # page could collapse into the previous page at the same boundary.
        radius = min(480, max(50, page_chars // 8))
        low, high = max(0, target - radius), min(total, target + radius)
        nearby = body[low:high]
        paragraphs = [low + match.end()
                      for match in re.finditer(r"\r?\n(?:[ \t]*\r?\n)+", nearby)
                      if 0 < low + match.end() < total]
        if paragraphs:
            return min(paragraphs, key=lambda pos: (abs(pos - target), pos < target))
        # Hard-wrapped rows are the next safest boundary. If the entire window
        # is one huge line, use the exact target rather than skipping text.
        newlines = [low + i + 1 for i, char in enumerate(nearby)
                    if char == "\n" and 0 < low + i + 1 < total]
        if newlines:
            return min(newlines, key=lambda pos: (abs(pos - target), pos < target))
        return target

    def read_display_page(self, planid: str, *, page: int, page_chars: int = 4000) -> dict:
        """Present complete nearby paragraphs without mutating frozen content.

        Nominal pages use fixed code-point positions, actual display slices use
        deterministic boundaries. Adjacent pages therefore neither omit nor
        duplicate characters.
        """
        planid = self._check_planid(planid)
        record = self._by_planid.get(planid)
        if record is None:
            raise LookupError("未找到该政策")
        if type(page_chars) is not int or not 500 <= page_chars <= 10000:
            raise InvalidParameter("page_chars 必须在 500–10000 范围")
        body = record.body_text
        total = len(body)
        page_count = max(1, (total + page_chars - 1) // page_chars)
        if type(page) is not int or page < 1 or page > page_count:
            raise InvalidParameter("页码超出正文范围")
        nominal_start, nominal_end = (page - 1) * page_chars, min(page * page_chars, total)
        start = self._reading_boundary(body, nominal_start, page_chars)
        end = self._reading_boundary(body, nominal_end, page_chars)
        if end < start or end - start > MAX_CONTENT_LIMIT:
            # This can only occur for an unusual source; keep exact boundaries.
            start, end = nominal_start, nominal_end
        return {
            "dataset": self._legacy_dataset(), "planid": record.planid,
            "title": record.title, "offset": start, "limit": end - start,
            "total_chars": total, "has_more": end < total,
            "body_status": body_status(body, record.title),
            "text": body[start:end], "page": page, "page_count": page_count,
        }

    def heading_hints(self, planid: str) -> dict:
        """Read matching original HTML for presentation hints only (never writes)."""
        planid = self._check_planid(planid)
        if planid not in self._by_planid:
            raise LookupError("未找到该政策")
        if self._by_planid[planid].text_only:
            return {"source": "unavailable", "headings": []}
        root_override = (os.environ.get("GUANCE_POLICY_ORIGINAL_ROOT")
                         or os.environ.get("GUANCE_POLICY_CLEAN_ROOT"))
        root = (Path(root_override) if root_override
                else Path(__file__).resolve().parents[3] / "语料库" / "Agent标准引用库")
        return read_original_html_headings(root, planid)

    def resolve_media(self, planid: str, filename: str) -> tuple[Path, str]:
        """Resolve only an image the selected policy's frozen text actually cites."""
        planid = self._check_planid(planid)
        record = self._by_planid.get(planid)
        if record is None:
            raise LookupError("未找到该政策")
        if record.text_only:
            raise LookupError("图片原件未发布")
        if not isinstance(filename, str) or not IMAGE_NAME.fullmatch(filename) or ".." in filename:
            raise InvalidParameter("图片文件名无效")
        mime = IMAGE_TYPES.get(Path(filename).suffix.lower())
        if mime is None:
            raise InvalidParameter("不支持的图片类型")
        # Markdown permits both media/file.png and <media/file.png>. Keep
        # paths policy-local and never loosen the allowed filename pattern.
        local_target = re.escape(filename)
        reference = re.compile(
            r'!\[[^\]\n]{0,200}\]\s*\(\s*(?:<media/' + local_target
            + r'>|media/' + local_target + r')(?:\s+"[^"]*")?\s*\)',
            re.IGNORECASE,
        )
        if not reference.search(record.body_text):
            raise LookupError("原文没有引用此图片")
        for root in self.snapshot.media_roots:
            root = root.resolve()
            # The cleaned sample uses Chinese-named corpus partitions. The
            # freeze-compatible reference corpus and separately mounted media
            # roots may instead use their normalized English counterparts.
            # Search only bounded, policy-specific locations; never glob
            # arbitrary directories or search another document's images.
            candidates = tuple(
                root / section / planid / "media" / filename
                for section in (
                    "corpus（代表性样本）", "edge_cases（边界压力样本）",
                    "corpus", "edge_cases", "snapshots",
                )
            ) + (root / planid / "media" / filename,)
            for candidate in candidates:
                path = candidate.resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    continue
                if path.stat().st_size > MAX_IMAGE_BYTES:
                    raise InvalidParameter("图片文件过大")
                with path.open("rb") as source:
                    signature = source.read(16)
                valid = ((mime == "image/png" and signature.startswith(b"\x89PNG\r\n\x1a\n"))
                         or (mime == "image/jpeg" and signature.startswith(b"\xff\xd8\xff"))
                         or (mime == "image/gif" and signature.startswith((b"GIF87a", b"GIF89a")))
                         or (mime == "image/webp" and signature[:4] == b"RIFF"
                             and signature[8:12] == b"WEBP"))
                if not valid:
                    raise InvalidParameter("图片格式无效")
                return path, mime
        raise LookupError("图片原件未发布")

    # ---- 内部校验与匹配 -------------------------------------------------
    @staticmethod
    def _check_planid(planid: str) -> str:
        if not isinstance(planid, str) or not PLANID_PATTERN.fullmatch(planid):
            raise InvalidParameter("planid 格式无效")
        return planid

    @staticmethod
    def _check_query(q: str | None) -> str | None:
        if q is None:
            return None
        if not isinstance(q, str):
            raise InvalidParameter("q 必须为字符串")
        if not q.strip():
            raise InvalidParameter("q 不能为空；如需列举目录请省略 q 参数")
        if len(q) > MAX_QUERY_CHARS:
            raise InvalidParameter(f"q 不能超过 {MAX_QUERY_CHARS} 个字符")
        return q

    @staticmethod
    def _check_filter(value: str | None, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise InvalidParameter(f"{name} 必须为字符串")
        if not value.strip():
            raise InvalidParameter(f"{name} 不能为空")
        if len(value) > MAX_FILTER_CHARS:
            raise InvalidParameter(f"{name} 不能超过 {MAX_FILTER_CHARS} 个字符")
        return value

    @staticmethod
    def _check_paging(page: int, page_size: int) -> tuple[int, int]:
        if type(page) is not int or page < 1:
            raise InvalidParameter("page 必须为从 1 开始的正整数")
        if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise InvalidParameter(f"page_size 必须为 1–{MAX_PAGE_SIZE} 的整数")
        return page, page_size

    @staticmethod
    def _match_record(record: PolicyRecord, q: str, body: str | None = None) -> list[dict]:
        matches: list[dict] = []
        if record.title:
            position = record.title.find(q)
            if position >= 0:
                matches.append({"field": "title", "offset": position,
                                "snippet": _snippet(record.title, position, len(q))})
        if body is None:
            body = record.body_text
        position = body.find(q)
        if position >= 0:
            matches.append({"field": "body", "offset": position,
                            "snippet": _snippet(body, position, len(q))})
        return matches[:2]


def _snippet(text: str, position: int, length: int) -> str:
    start = max(0, position - SNIPPET_RADIUS)
    end = min(len(text), position + length + SNIPPET_RADIUS)
    return text[start:end]
