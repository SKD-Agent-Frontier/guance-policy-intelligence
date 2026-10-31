"""只读政策目录、Facets 与完整行政区划树接口。"""
import asyncio
import logging
import os
import re
import threading

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .policy_catalog import (
    DEFAULT_CONTENT_LIMIT,
    DEFAULT_PAGE_SIZE,
    MAX_CONTENT_LIMIT,
    MAX_PAGE_SIZE,
    CatalogUnavailable,
    InvalidParameter,
    PolicyCatalogService,
    load_published_catalog,
)
from .compare_resolution import CompareResolveRequest, resolve_compare
from .policy_regions import RegionResourceUnavailable, load_region_mapping, load_region_tree
from .policy_schemas import (
    PolicyContentResponse,
    PolicyDetailResponse,
    PolicyFacetsResponse,
    PolicyListResponse,
    RegionTreeResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["policies"])
_BUILD_LOCK = threading.Lock()
_GENERATION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FILTERS = {"q", "area", "fyp", "plantype", "admin_level", "province_code",
            "prefecture_code", "county_code", "region_status", "year", "fyp_status",
            "plantype_status"}
_PINS = {"generation_id", "revision", "region_version", "region_mapping_version"}


def _reject_unknown_query(request: Request, allowed: set[str]) -> None:
    unknown = sorted(set(request.query_params.keys()) - allowed)
    repeated = sorted(key for key in set(request.query_params.keys())
                      if len(request.query_params.getlist(key)) > 1)
    if repeated:
        raise HTTPException(status_code=400, detail="查询参数不得重复：" + "、".join(repeated))
    if unknown:
        raise HTTPException(status_code=400, detail="不支持的查询参数：" + "、".join(unknown))


def _resource_dir():
    value = os.environ.get("GUANCE_POLICY_REGIONS_DIR")
    return value or None


async def _regions(request: Request) -> dict:
    cached = getattr(request.app.state, "policy_regions", None)
    if cached is None:
        service = getattr(request.app.state, "policy_catalog", None)
        cached = getattr(service, "region_payload", None) if service else None
    if cached is not None:
        return cached
    def build():
        with _BUILD_LOCK:
            existing = getattr(request.app.state, "policy_regions", None)
            if existing is not None:
                return existing
            payload = load_region_tree(_resource_dir())
            request.app.state.policy_regions = payload
            return payload
    try:
        return await asyncio.to_thread(build)
    except RegionResourceUnavailable as exc:
        logger.error("地区树加载失败 exception_type=RegionResourceUnavailable")
        raise HTTPException(status_code=503, detail="地区树资源不可用") from exc


async def _catalog(request: Request) -> PolicyCatalogService:
    app = request.app
    service = getattr(app.state, "policy_catalog", None)
    if service is not None:
        return service
    settings = getattr(app.state, "settings", None)
    if settings is None or settings.kb_backend != "rag" or not settings.rag_store_project:
        raise HTTPException(status_code=503, detail="政策目录服务不可用：未配置真实 RAG 发布库")

    def build() -> PolicyCatalogService:
        with _BUILD_LOCK:
            existing = getattr(app.state, "policy_catalog", None)
            if existing is not None:
                return existing
            snapshot = load_published_catalog(settings.rag_store_project)
            try:
                region_payload = load_region_tree(_resource_dir())
            except RegionResourceUnavailable:
                region_payload = None
            region_map = (load_region_mapping(_resource_dir(), snapshot, region_payload)
                          if region_payload is not None else None)
            if region_map is None:
                mapping, mapping_version = None, None
            else:
                mapping, mapping_version = region_map
            built = PolicyCatalogService(snapshot, region_payload=region_payload,
                                         region_mapping=mapping,
                                         region_mapping_version=mapping_version)
            app.state.policy_catalog = built
            if region_payload is not None:
                app.state.policy_regions = region_payload
            return built
    try:
        return await asyncio.to_thread(build)
    except CatalogUnavailable as exc:
        logger.error("政策目录加载失败 exception_type=CatalogUnavailable")
        raise HTTPException(status_code=503,
                            detail="政策目录服务不可用：当前发布档案无法读取") from exc
    except Exception as exc:
        logger.error("政策目录加载失败 exception_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="政策目录服务不可用") from exc


def _check_pins(request: Request, service: PolicyCatalogService) -> None:
    present = _PINS.intersection(request.query_params.keys())
    if not present:
        return
    if present != _PINS:
        raise HTTPException(status_code=400, detail="版本固定参数必须完整提供")
    query = request.query_params
    generation = query.get("generation_id", "")
    revision_text = query.get("revision", "")
    region_version = query.get("region_version", "")
    mapping_version = query.get("region_mapping_version", "")
    if not _GENERATION_ID.fullmatch(generation) or not _SHA256.fullmatch(region_version):
        raise HTTPException(status_code=400, detail="版本固定参数格式无效")
    if not re.fullmatch(r"\d+", revision_text) or int(revision_text) < 1:
        raise HTTPException(status_code=422, detail="请求参数无效")
    if mapping_version != "unavailable" and not _SHA256.fullmatch(mapping_version):
        raise HTTPException(status_code=400, detail="版本固定参数格式无效")
    current = service.dataset()
    expected_mapping = current["region_mapping_version"] or "unavailable"
    if (generation != current["generation_id"] or int(revision_text) != current["revision"]
            or region_version != current["region_version"]
            or mapping_version != expected_mapping):
        raise _VersionPinConflict(_current_payload(service))


def _current_payload(service: PolicyCatalogService) -> dict:
    dataset = service.dataset()
    return {key: dataset[key] for key in
            ("generation_id", "revision", "region_version", "region_mapping_version")}


class _VersionPinConflict(Exception):
    def __init__(self, current: dict):
        self.current = current


async def _service(request: Request, pin: bool = True) -> PolicyCatalogService | JSONResponse:
    result = await _catalog(request)
    if pin:
        try:
            _check_pins(request, result)
        except _VersionPinConflict as exc:
            return JSONResponse(status_code=409, content={
                "detail": "请求版本与当前目录或地区版本不一致", "current": exc.current})
    return result


def _raise_parameter(exc: InvalidParameter):
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/policies/regions", response_model=RegionTreeResponse)
async def policy_regions(request: Request, region_version: str | None = None):
    _reject_unknown_query(request, {"region_version"})
    payload = await _regions(request)
    if region_version is not None and not _SHA256.fullmatch(region_version):
        raise HTTPException(status_code=400, detail="region_version 格式无效")
    if region_version is not None and region_version != payload["region_version"]:
        return JSONResponse(status_code=409, content={
            "detail": "请求地区版本与当前版本不一致",
            "current": {"region_version": payload["region_version"]}})
    return payload


@router.get("/policies/facets", response_model=PolicyFacetsResponse)
async def policy_facets(
    request: Request,
    q: str | None = None, area: str | None = None, fyp: str | None = None,
    plantype: str | None = None, admin_level: str | None = None,
    province_code: str | None = None, prefecture_code: str | None = None,
    county_code: str | None = None, region_status: str | None = None,
    year: str | None = None, fyp_status: str | None = None,
    plantype_status: str | None = None, generation_id: str | None = None,
    revision: int | None = Query(default=None, ge=1), region_version: str | None = None,
    region_mapping_version: str | None = None,
):
    _reject_unknown_query(request, _FILTERS | _PINS)
    service = await _service(request)
    if isinstance(service, JSONResponse):
        return service
    try:
        return await asyncio.to_thread(service.facets, q=q, area=area, fyp=fyp,
            plantype=plantype, admin_level=admin_level, province_code=province_code,
            prefecture_code=prefecture_code, county_code=county_code,
            region_status=region_status, year=year, fyp_status=fyp_status,
            plantype_status=plantype_status)
    except InvalidParameter as exc:
        _raise_parameter(exc)
    except CatalogUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/policies", response_model=PolicyListResponse)
async def list_policies(
    request: Request,
    q: str | None = Query(default=None, description="标题与正文的字面连续子串；省略则列举目录"),
    area: str | None = Query(default=None, description="地区精确匹配（metadata.area）"),
    fyp: str | None = Query(default=None, description="规划期精确匹配，支持展示别名"),
    plantype: str | None = Query(default=None, description="政策类型精确匹配"),
    page: int = Query(default=1, ge=1, description="从 1 开始"),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    admin_level: str | None = None, province_code: str | None = None,
    prefecture_code: str | None = None, county_code: str | None = None,
    region_status: str | None = None, year: str | None = None,
    fyp_status: str | None = None, plantype_status: str | None = None,
    generation_id: str | None = None, revision: int | None = Query(default=None, ge=1),
    region_version: str | None = None, region_mapping_version: str | None = None,
):
    _reject_unknown_query(request, _FILTERS | _PINS | {"page", "page_size"})
    service = await _service(request)
    if isinstance(service, JSONResponse):
        return service
    try:
        return await asyncio.to_thread(service.list_policies, q=q, area=area, fyp=fyp,
            plantype=plantype, page=page, page_size=page_size, admin_level=admin_level,
            province_code=province_code, prefecture_code=prefecture_code,
            county_code=county_code, region_status=region_status, year=year,
            fyp_status=fyp_status, plantype_status=plantype_status)
    except InvalidParameter as exc:
        _raise_parameter(exc)
    except CatalogUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/policies/{planid}", response_model=PolicyDetailResponse)
async def get_policy(request: Request, planid: str):
    _reject_unknown_query(request, set())
    service = await _catalog(request)
    try:
        return await asyncio.to_thread(service.get_policy, planid)
    except InvalidParameter as exc:
        _raise_parameter(exc)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="未找到该政策") from exc


@router.get("/policies/{planid}/content", response_model=PolicyContentResponse)
async def get_policy_content(
    request: Request, planid: str,
    offset: int = Query(default=0, ge=0, description="正文码点偏移，0 为正文起点"),
    limit: int = Query(default=DEFAULT_CONTENT_LIMIT, ge=1, le=MAX_CONTENT_LIMIT),
):
    _reject_unknown_query(request, {"offset", "limit"})
    service = await _catalog(request)
    try:
        return await asyncio.to_thread(service.read_content, planid, offset=offset, limit=limit)
    except InvalidParameter as exc:
        _raise_parameter(exc)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="未找到该政策") from exc


@router.post("/research/resolve-compare")
async def resolve_compare_request(request: Request, payload: CompareResolveRequest):
    """用真实发布目录发现跨省比较对象；不调用 Agent，也不生成虚构编号。"""
    service = await _catalog(request)
    return await asyncio.to_thread(resolve_compare, payload.question, service)
