"""政策文献目录的独立 HTTP 接口。

不依赖 Agent、不调用模型、不写任何文件；数据来自当前已发布的 RAG 冻结运行库。
路由独立挂载，便于前端直接使用或供任务准备层复用。
"""
import asyncio
import logging
import threading

from fastapi import APIRouter, HTTPException, Query, Request

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
from .policy_schemas import (
    PolicyContentResponse,
    PolicyDetailResponse,
    PolicyListResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["policies"])
_BUILD_LOCK = threading.Lock()


def _reject_unknown_query(request: Request, allowed: set[str]) -> None:
    """未知查询参数直接拒绝：避免调用方误以为不支持的筛选（如日期）已生效。"""
    unknown = sorted(set(request.query_params.keys()) - allowed)
    if unknown:
        raise HTTPException(status_code=400,
                            detail="不支持的查询参数：" + "、".join(unknown))


async def _catalog(request: Request) -> PolicyCatalogService:
    """按应用实例懒加载目录；失败一律 503，且不返回任何伪造文献。"""
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
            built = PolicyCatalogService(snapshot)
            app.state.policy_catalog = built
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


@router.get("/policies", response_model=PolicyListResponse)
async def list_policies(
    request: Request,
    q: str | None = Query(default=None, description="标题与正文的字面连续子串；省略则列举目录"),
    area: str | None = Query(default=None, description="地区精确匹配（metadata.area）"),
    fyp: str | None = Query(default=None, description="规划期精确匹配，支持“十四五”等展示别名"),
    plantype: str | None = Query(default=None, description="政策类型精确匹配（metadata.plantype_label）"),
    page: int = Query(default=1, ge=1, description="从 1 开始"),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    _reject_unknown_query(request, {"q", "area", "fyp", "plantype", "page", "page_size"})
    service = await _catalog(request)
    try:
        return await asyncio.to_thread(
            service.list_policies, q=q, area=area, fyp=fyp, plantype=plantype,
            page=page, page_size=page_size)
    except InvalidParameter as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/policies/{planid}", response_model=PolicyDetailResponse)
async def get_policy(request: Request, planid: str):
    _reject_unknown_query(request, set())
    service = await _catalog(request)
    try:
        return await asyncio.to_thread(service.get_policy, planid)
    except InvalidParameter as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="未找到该政策") from exc


@router.get("/policies/{planid}/content", response_model=PolicyContentResponse)
async def get_policy_content(
    request: Request,
    planid: str,
    offset: int = Query(default=0, ge=0, description="正文码点偏移，0 为正文起点"),
    limit: int = Query(default=DEFAULT_CONTENT_LIMIT, ge=1, le=MAX_CONTENT_LIMIT),
):
    _reject_unknown_query(request, {"offset", "limit"})
    service = await _catalog(request)
    try:
        return await asyncio.to_thread(service.read_content, planid,
                                       offset=offset, limit=limit)
    except InvalidParameter as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="未找到该政策") from exc
