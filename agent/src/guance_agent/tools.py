"""两个只读工具；超时和业务错误统一返回 ToolResult。"""
import asyncio
import re
from time import perf_counter

from agents import RunContextWrapper, function_tool
from openai import APIStatusError

from .context import RunContext, sanitize_error_detail
from .kb import KBNoText, KBNotFound, normalize_fyp
from .schemas import Passage, ToolResult


def _validate(s, name, args, kwargs):
    if name == "kb_search":
        query, area, fyp, top_k = args
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError("query去除首尾空白后须为 1–200 字符")
        for key, value in (("area", area), ("fyp", fyp)):
            if value is not None and (not isinstance(value, str) or len(value) > 50):
                raise ValueError(f"{key}须为不超过 50 字符的字符串")
        if type(top_k) is not int or not 1 <= top_k <= s.top_k_max:
            raise ValueError(f"top_k必须为 1–{s.top_k_max} 的整数")
        if isinstance(area, str) and not area.strip():
            area = None
        if isinstance(fyp, str) and not fyp.strip():
            fyp = None
        planids = kwargs.get("planids")
        if planids is not None:
            if (not isinstance(planids, (tuple, list)) or not planids
                    or any(not isinstance(p, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", p)
                           for p in planids)):
                raise ValueError("planids须为非空政策编号列表")
            planids = tuple(planids)
        return (query.strip(), area, normalize_fyp(fyp), top_k), ({"planids": planids} if planids else {})
    planid, section_idx, max_chars = args
    if not isinstance(planid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", planid):
        raise ValueError("planid须为 1–64 位字母、数字、下划线或连字符")
    if section_idx is not None and (type(section_idx) is not int or section_idx < 0):
        raise ValueError("section_idx须为非负整数")
    chunk_id = kwargs.get("chunk_id")
    if chunk_id is not None and (not isinstance(chunk_id, str) or not 1 <= len(chunk_id) <= 200):
        raise ValueError("chunk_id须为 1–200 字符的字符串")
    offset = kwargs.get("offset", 0)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset须为非负整数")
    if section_idx is not None and chunk_id is not None:
        raise ValueError("section_idx与chunk_id只能二选一")
    return (planid, section_idx, max_chars), {"chunk_id": chunk_id, "offset": offset}

async def _call(ctx, name, fn, *args, **kwargs):
    """只把后端调用包进异常处理；本层自身的处理错误不冒充服务故障。"""
    started, detail = perf_counter(), None
    try:
        args, kwargs = _validate(ctx.settings, name, args, kwargs)
    except ValueError as exc:
        result = ToolResult(status="invalid_argument", message=f"参数不合法：{exc}")
    else:
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(lambda: fn(*args, **kwargs)), ctx.settings.tool_timeout)
        except KBNoText as exc:
            result = ToolResult(status="no_text",
                                message=f"《{exc.title}》（planid={exc.planid}）没有可用正文，无法引用原文")
        except KBNotFound as exc:
            result = ToolResult(
                status="not_found",
                message=(f"未找到 planid={args[0]} 的篇目" if exc.section_idx is None
                         else f"该篇没有第 {args[1]} 节"))
        except TimeoutError:
            result = ToolResult(status="timeout", message="检索超时，检索服务暂时不可用")
        except Exception as exc:
            detail = (f"{type(exc).__name__}: HTTP {exc.status_code}"
                      if isinstance(exc, APIStatusError) else f"{type(exc).__name__}: {exc}")
            detail = sanitize_error_detail(detail, ctx.settings)
            # 真实 RAG 的业务异常带 .status，直接映射；其余才算后端故障
            status = getattr(exc, "status", None)
            if status == "not_found":
                result = ToolResult(status="not_found", message=str(exc))
            elif status == "no_text":
                result = ToolResult(status="no_text", message="该篇目没有可用正文，无法引用原文")
            elif status == "invalid_argument":
                result = ToolResult(status="invalid_argument", message=f"参数不合法：{exc}")
            elif status == "timeout":
                result = ToolResult(status="timeout", message="检索超时，检索服务暂时不可用")
            else:
                result = ToolResult(status="backend_error",
                                    message="检索服务异常，暂时不可用（详情只写日志）")
        else:
            # 返回类型必须与本次操作匹配，否则拒绝而不是悄悄登记为证据
            if not isinstance(data, list if name == "kb_search" else Passage):
                detail = f"{name} 返回类型错误: {type(data).__name__}"
                result = ToolResult(status="backend_error",
                                    message="检索服务返回了无法识别的数据，暂时不可用")
            elif name == "kb_search" and not data:
                query, area, fyp, _ = args
                result = ToolResult(status="empty", message=(
                    f"未检索到相关政策原文（检索词：{query}；地区：{area or '未指定'}；"
                    f"规划期：{fyp or '未指定'}）"))
            else:
                result = ToolResult(status="ok", data=data)
                # 证据登记在本层，出错属程序缺陷，应上抛成 INTERNAL 而不是 backend_error
                before = len(ctx.evidence.rejected)
                ctx.evidence.add_result(result.data, name)
                broken = ctx.evidence.rejected[before:]
                if broken:
                    # 坐标自相矛盾说明后端数据不可信：整次结果作废，不能挑着用
                    detail = "; ".join(f"{b['chunk_id']}: {b['reason']}" for b in broken)
                    result = ToolResult(status="backend_error",
                                        message="检索服务返回了不一致的坐标数据，暂时不可用")
    event = dict(name=name, status=result.status, message=result.message,
                 elapsed_ms=round((perf_counter() - started) * 1000))
    if detail:
        event["error_detail"] = detail
    ctx.tool_calls.append(event)
    return result.model_dump_json()

@function_tool(failure_error_function=None)
async def kb_search(ctx: RunContextWrapper[RunContext], query: str,
                    area: str | None = None, fyp: str | None = None,
                    top_k: int | None = None) -> str:
    """检索政策片段。area 可用地区简称；fyp 可用十四五；top_k 为 1–20，不填用服务默认值。
    返回 status 和片段列表，含 chunk_id、planid、篇名、地区、规划期、章节路径和原文。
    """
    if top_k is None:
        top_k = ctx.context.settings.top_k_default
    # 路由确定的范围在整轮任务内持续生效：每次检索都限定，不是只在第一次限定
    scope = ctx.context.scope
    return await _call(ctx.context, "kb_search", ctx.context.kb.search,
                       query, area, fyp, top_k, planids=scope)

@function_tool(failure_error_function=None)
async def kb_read(ctx: RunContextWrapper[RunContext], planid: str,
                  section_idx: int | None = None, chunk_id: str | None = None,
                  offset: int = 0) -> str:
    """读取政策原文。

    - 都不给：返回该篇的章节目录
    - 给 section_idx：读该章节的一页
    - 给 chunk_id：读该片段的完整原文，并从块内 offset 起返回一页
    - 续读时把上一页返回的 next_offset 原样传给 offset，不要自己计算

    section_idx 与 chunk_id 只能二选一。
    """
    return await _call(ctx.context, "kb_read", ctx.context.kb.read,
                       planid, section_idx, ctx.context.settings.read_max_chars,
                       chunk_id=chunk_id, offset=offset)
