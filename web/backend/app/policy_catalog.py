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
               ("十一", "十二", "十三", "十四", "十五")}


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
    return FYP_ALIASES.get(key, value)


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
            ))
        sections = _read_sections(conn, set(document_ids), origins, lengths)
        records = [PolicyRecord(**{**record.__dict__, "sections": sections.get(
            record.document_id, ())}) for record in records]
    except CatalogUnavailable:
        raise
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        raise CatalogUnavailable("当前发布档案不可用") from exc
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    records.sort(key=lambda record: record.planid)
    return CatalogSnapshot(generation_id=generation, revision=revision,
                           partial=manifest.get("partial"), records=tuple(records))


class PolicyCatalogService:
    """只读目录服务：所有查询基于加载完成的内存快照，线程安全（快照不可变）。"""

    def __init__(self, snapshot: CatalogSnapshot):
        self.snapshot = snapshot
        self._by_planid = {record.planid: record for record in snapshot.records}
        if len(self._by_planid) != len(snapshot.records):
            raise CatalogUnavailable("当前发布存在重复政策编号")

    # ---- 公共查询 -------------------------------------------------------
    def dataset(self) -> dict:
        return {"generation_id": self.snapshot.generation_id,
                "revision": self.snapshot.revision,
                "total_documents": self.snapshot.total_documents,
                "partial": self.snapshot.partial,
                "source": "current_published_frozen_release"}

    def list_policies(self, *, q: str | None, area: str | None, fyp: str | None,
                      plantype: str | None, page: int, page_size: int) -> dict:
        q = self._check_query(q)
        area = self._check_filter(area, "area")
        fyp = self._check_filter(fyp, "fyp")
        plantype = self._check_filter(plantype, "plantype")
        page, page_size = self._check_paging(page, page_size)
        wanted_fyp = normalize_fyp(fyp)
        matched: list[tuple[PolicyRecord, list[dict]]] = []
        for record in self.snapshot.records:
            if area is not None and record.area != area:
                continue
            if wanted_fyp is not None and normalize_fyp(record.fyp) != wanted_fyp:
                continue
            if plantype is not None and record.plantype_label != plantype:
                continue
            matches: list[dict] = []
            if q is not None:
                matches = self._match_record(record, q)
                if not matches:
                    continue
            matched.append((record, matches))
        matched_documents = len(matched)
        start = (page - 1) * page_size
        page_items = matched[start:start + page_size]
        items = []
        for record, matches in page_items:
            item = {"planid": record.planid, "title": record.title, "area": record.area,
                    "fyp": record.fyp, "plantype_label": record.plantype_label,
                    "matches": matches if q is not None else []}
            items.append(item)
        total_pages = (matched_documents + page_size - 1) // page_size if page_size else 0
        return {"dataset": self.dataset(),
                "query": {"q": q, "area": area, "fyp": fyp, "plantype": plantype,
                          "match_mode": "literal" if q is not None else None},
                "matched_documents": matched_documents,
                "page": page, "page_size": page_size, "total_pages": total_pages,
                "items": items}

    def get_policy(self, planid: str) -> dict:
        planid = self._check_planid(planid)
        record = self._by_planid.get(planid)
        if record is None:
            raise LookupError("未找到该政策")
        return {"dataset": self.dataset(),
                "policy": {"planid": record.planid, "title": record.title,
                           "area": record.area, "fyp": record.fyp,
                           "plantype_label": record.plantype_label,
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
        return {"dataset": self.dataset(), "planid": record.planid,
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
