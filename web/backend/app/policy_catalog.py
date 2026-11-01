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
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

PLANID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
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
               ("十", "十一", "十二", "十三", "十四", "十五")}
PLANNING_YEARS = {"第十个五年规划": 2001, "第十一个五年规划": 2006,
                  "第十二个五年规划": 2011, "第十三个五年规划": 2016,
                  "第十四个五年规划": 2021, "第十五个五年规划": 2026}
PLANNING_ORDINALS = re.compile(r"第(十|十一|十二|十三|十四|十五)个五年(?:规划|计划)\Z")
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

    @property
    def year(self) -> int | None:
        return reference_year(self.title, self.fyp)


@dataclass(frozen=True)
class CatalogSnapshot:
    generation_id: str
    revision: int
    partial: bool | None
    records: tuple[PolicyRecord, ...]

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
    rows = conn.execute(
        "SELECT document_id,data,data_hash FROM objects WHERE kind='structure'").fetchall()
    for row in rows:
        did = row["document_id"]
        if did not in grouped:
            continue
        raw = row["data"]
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["data_hash"]:
            raise CatalogUnavailable("章节结构校验失败")
        data = json.loads(raw)
        if data.get("kind") == "root":
            continue        # 全文根节点不是章节，目录接口不返回
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
        ordered = sorted(items, key=lambda item: (item.start, item.level, item.end))
        sections[did] = tuple(
            SectionMeta(index=index, level=item.level, path=item.path,
                        start=item.start, end=item.end)
            for index, item in enumerate(ordered))
    return sections


def load_published_catalog(project: str | Path) -> CatalogSnapshot:
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
        document_ids = []
        for line in documents_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            did = json.loads(line)["id"]
            if not isinstance(did, str) or did in document_ids:
                raise CatalogUnavailable("当前发布文档清单无效")
            document_ids.append(did)
        records: list[PolicyRecord] = []
        planids: set[str] = set()
        origins: dict[str, int] = {}
        lengths: dict[str, int] = {}
        bodies: dict[str, str] = {}
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
            snapshot_bytes = snapshot.read_bytes()
            if hashlib.sha256(snapshot_bytes).hexdigest() != data["source_hash"]:
                raise CatalogUnavailable("冻结原文校验失败")
            text = snapshot_bytes.decode("utf-8")
            origin = body_origin(text, data["body_start"])
            meta = data["metadata"]
            planid = data["planid"]
            if not isinstance(planid, str) or planid in planids:
                raise CatalogUnavailable("当前发布存在重复或无效的政策编号")
            planids.add(planid)
            origins[did], lengths[did], bodies[did] = origin, len(text), text[origin:]
            records.append(PolicyRecord(
                planid=planid,
                title=(meta.get("title") or None),
                area=(meta.get("area_name") or None),
                fyp=(meta.get("fyp_name") or None),
                plantype_label=(meta.get("plantype_label") or None),
                document_id=did,
                body=bodies[did],
                sections=(),
                admin_level=(meta.get("admin_level") or None),
            ))
        sections = _read_sections(conn, set(document_ids), origins, lengths)
        records = [PolicyRecord(**{**record.__dict__, "sections": sections.get(
            record.document_id, ())}) for record in records]
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
    return CatalogSnapshot(generation_id=generation, revision=revision,
                           partial=manifest.get("partial"), records=tuple(records))


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

    def list_policies(self, *, q: str | None = None, area: str | None = None,
                      fyp: str | None = None, plantype: str | None = None,
                      page: int = 1, page_size: int = DEFAULT_PAGE_SIZE,
                      admin_level: str | None = None, province_code: str | None = None,
                      prefecture_code: str | None = None, county_code: str | None = None,
                      region_status: str | None = None, year: str | int | None = None,
                      fyp_status: str | None = None, plantype_status: str | None = None) -> dict:
        filters, query = self._normalize_filters(
            q=q, area=area, fyp=fyp, plantype=plantype, admin_level=admin_level,
            province_code=province_code, prefecture_code=prefecture_code,
            county_code=county_code, region_status=region_status, year=year,
            fyp_status=fyp_status, plantype_status=plantype_status)
        page, page_size = self._check_paging(page, page_size)
        matched = [(record, self._match_record(record, filters["q"]) if filters["q"] else [])
                   for record in self.snapshot.records if self._matches(record, filters)]
        matched.sort(key=lambda item: _sort_key(item[0], filters["q"]))
        total = len(matched)
        start = (page - 1) * page_size
        items = []
        for record, matches in matched[start:start + page_size]:
            mapping = (self.region_mapping or {}).get(record.planid)
            items.append({"planid": record.planid, "title": record.title, "area": record.area,
                          "fyp": record.fyp, "plantype_label": record.plantype_label,
                          "year": record.year,
                          "admin_level": self._admin_level(record),
                          "region_code": mapping.get("region_code") if mapping else None,
                          "region_status": mapping.get("region_status") if mapping else None,
                          "matches": matches})
        return {"dataset": self.dataset(), "query": {**query,
                "match_mode": "literal" if filters["q"] is not None else None},
                "matched_documents": total, "page": page, "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "items": items}

    def facets(self, **params) -> dict:
        if self.region_payload is None:
            raise CatalogUnavailable("地区树资源不可用")
        if self.region_mapping is None or self.region_mapping_version is None:
            raise CatalogUnavailable("地区映射尚未就绪")
        base, query = self._normalize_filters(**params)
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
        province_options = [self._region_option(node, region_filter_base) for node in self.region_payload["data"]
                            if node["tier"] == "province"]
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
                           plantype_status=None) -> tuple[dict, dict]:
        q = self._check_query(q)
        for name, value in (("area", area), ("fyp", fyp), ("plantype", plantype)):
            self._check_filter(value, name)
        fyp, plantype = normalize_fyp(fyp), plantype
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
        if filters.get("region_status") is not None:
            if mapping is None or mapping["region_status"] != filters["region_status"]:
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
        if filters.get("plantype") is not None and record.plantype_label != filters["plantype"]:
            return False
        if "year" in filters and filters["year"] is not None:
            if filters["year"] == "unknown" and record.year is not None:
                return False
            if isinstance(filters["year"], int) and record.year != filters["year"]:
                return False
        if filters.get("q") is not None and not self._match_record(record, filters["q"]):
            return False
        return True

    def get_policy(self, planid: str) -> dict:
        planid = self._check_planid(planid)
        record = self._by_planid.get(planid)
        if record is None:
            raise LookupError("未找到该政策")
        return {"dataset": self._legacy_dataset(),
                "policy": {"planid": record.planid, "title": record.title,
                           "area": record.area, "fyp": record.fyp,
                           "plantype_label": record.plantype_label,
                           "year": record.year,
                           "body_chars": len(record.body),
                           "sections": [section.as_dict() for section in record.sections]}}

    def read_content(self, planid: str, *, offset: int, limit: int) -> dict:
        planid = self._check_planid(planid)
        if type(offset) is not int or offset < 0:
            raise InvalidParameter("offset 必须为非负整数")
        if type(limit) is not int or not 1 <= limit <= MAX_CONTENT_LIMIT:
            raise InvalidParameter(f"limit 必须为 1–{MAX_CONTENT_LIMIT} 的整数")
        record = self._by_planid.get(planid)
        if record is None:
            raise LookupError("未找到该政策")
        total = len(record.body)
        if offset > total:
            raise InvalidParameter("offset 超出正文长度")
        end = min(offset + limit, total)
        return {"dataset": self._legacy_dataset(), "planid": record.planid,
                "title": record.title, "offset": offset, "limit": limit,
                "total_chars": total, "has_more": end < total,
                "text": record.body[offset:end]}

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
    def _match_record(record: PolicyRecord, q: str) -> list[dict]:
        matches: list[dict] = []
        if record.title:
            position = record.title.find(q)
            if position >= 0:
                matches.append({"field": "title", "offset": position,
                                "snippet": _snippet(record.title, position, len(q))})
        position = record.body.find(q)
        if position >= 0:
            matches.append({"field": "body", "offset": position,
                            "snippet": _snippet(record.body, position, len(q))})
        return matches[:2]


def _snippet(text: str, position: int, length: int) -> str:
    start = max(0, position - SNIPPET_RADIUS)
    end = min(len(text), position + length + SNIPPET_RADIUS)
    return text[start:end]
