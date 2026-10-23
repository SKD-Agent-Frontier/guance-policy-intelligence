"""两个只读工具；超时和业务错误统一返回 ToolResult。"""
import asyncio
import re
from time import perf_counter

from agents import RunContextWrapper, function_tool

from .context import RunContext
from .kb import KBNoText, KBNotFound, normalize_fyp
from .schemas import ToolResult


def _validate(s, name, args):
    if name == "kb_search":
        query, area, fyp, top_k = args
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError("query去除首尾空白后须为 1–200 字符")
        for key, value in (("area", area), ("fyp", fyp)):
            if value is not None and (not isinstance(value, str) or len(value) > 50):
                raise ValueError(f"{key}须为不超过 50 字符的字符串")
        if type(top_k) is not int or not 1 <= top_k <= s.top_k_max:
            raise ValueError(f"top_k必须为 1–{s.top_k_max} 的整数")
        return query.strip(), area, normalize_fyp(fyp), top_k
    planid, section_idx, max_chars = args
    if not isinstance(planid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", planid):
        raise ValueError("planid须为 1–64 位字母、数字、下划线或连字符")
    if section_idx is not None and (type(section_idx) is not int or section_idx < 0):
        raise ValueError("section_idx须为非负整数")
    return planid, section_idx, max_chars


async def _call(ctx, name, fn, *args):
    started, detail = perf_counter(), None
    try:
        try:
            args = _validate(ctx.settings, name, args)
        except ValueError as exc:
            result = ToolResult(status="invalid_argument", message=f"参数不合法：{exc}")
        else:
            data = await asyncio.wait_for(asyncio.to_thread(fn, *args), ctx.settings.tool_timeout)
            message = ""
            if name == "kb_search" and not data:
                query, area, fyp, _ = args
                message = (f"未检索到相关政策原文（检索词：{query}；地区：{area or '未指定'}；"
                           f"规划期：{fyp or '未指定'}）")
            result = ToolResult(status="empty" if message else "ok", message=message, data=data)
            if result.status == "ok":
                ctx.evidence.add_result(result.data, name)
    except KBNoText as exc:
        result = ToolResult(status="no_text", message=f"《{exc.title}》（planid={exc.planid}）没有可用正文，无法引用原文")
    except KBNotFound:
        message = f"未找到 planid={args[0]} 的篇目" if args[1] is None else f"该篇没有第 {args[1]} 节"
        result = ToolResult(status="not_found", message=message)
    except TimeoutError:
        result = ToolResult(status="timeout", message="检索超时，检索服务暂时不可用")
    except Exception as exc:
        detail = type(exc).__name__
        result = ToolResult(status="backend_error", message="检索服务异常，暂时不可用（详情只写日志）")
    event = dict(name=name, status=result.status, message=result.message,
                 elapsed_ms=round((perf_counter() - started) * 1000))
    if detail:
        event["error_detail"] = detail
    ctx.tool_calls.append(event)
    return result.model_dump_json()


@function_tool(failure_error_function=None)
async def kb_search(ctx: RunContextWrapper[RunContext], query: str,
                    area: str | None = None, fyp: str | None = None, top_k: int = 8) -> str:
    """检索政策片段。area 可用地区简称；fyp 可用十四五；top_k 为 1–20。
    返回 status 和片段列表，含 chunk_id、planid、篇名、地区、规划期、章节路径和原文。
    """
    return await _call(ctx.context, "kb_search", ctx.context.kb.search, query, area, fyp, top_k)


@function_tool(failure_error_function=None)
async def kb_read(ctx: RunContextWrapper[RunContext], planid: str, section_idx: int | None = None) -> str:
    """读取篇目原文。不给 section_idx 返回章节目录；给了返回该章节原文（最多 6000 字）。"""
    return await _call(ctx.context, "kb_read", ctx.context.kb.read,
                       planid, section_idx, ctx.context.settings.read_max_chars)
