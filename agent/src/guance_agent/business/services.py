"""Thin, budgeted access to the existing Agent tools and configured model client."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from ..client import thinking_off
from ..runtime import ToolBudgetExceeded
from ..schemas import ToolResult
from ..tools import _call, out_of_scope
from .contract import BusinessError

if TYPE_CHECKING:
    from ..context import RunContext
    from ..runtime import Runtime


class BusinessServices:
    def __init__(self, ctx: RunContext, rt: Runtime, deadline: float):
        self.ctx = ctx
        self.rt = rt
        self.deadline = deadline
        self.telemetry = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0,
                          "per_call": []}
        self._model_calls = 0
        self._tool_ids = 0

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BusinessError("RUN_TIMEOUT")
        return remaining

    async def _tool(self, name: str, fn) -> ToolResult:
        self._remaining()
        self._tool_ids += 1
        context = SimpleNamespace(context=self.ctx, tool_call_id=f"business-{self._tool_ids}")
        tool = SimpleNamespace(name=name)
        started_at = time.perf_counter()
        started = False
        result_json = None
        try:
            try:
                await self.rt.hooks.on_tool_start(context, None, tool)
            except ToolBudgetExceeded as exc:
                raise BusinessError("TOOL_BUDGET") from exc
            started = True
            remaining = self._remaining()
            result_json = await asyncio.wait_for(fn(), timeout=remaining)
            return ToolResult.model_validate_json(result_json)
        except BusinessError as exc:
            if started:
                status = "timeout" if exc.code == "RUN_TIMEOUT" else "backend_error"
                event = ToolResult(status=status, message=str(exc))
                self.ctx.tool_calls.append(dict(name=name, status=event.status,
                                                message=event.message,
                                                elapsed_ms=round((time.perf_counter() - started_at) * 1000)))
                result_json = event.model_dump_json()
            raise
        except TimeoutError as exc:
            event = ToolResult(status="timeout", message="本次任务剩余时间不足，已停止")
            self.ctx.tool_calls.append(dict(name=name, status=event.status,
                                            message=event.message,
                                            elapsed_ms=round((time.perf_counter() - started_at) * 1000)))
            result_json = event.model_dump_json()
            raise BusinessError("RUN_TIMEOUT") from exc
        except asyncio.CancelledError:
            deadline_hit = time.monotonic() >= self.deadline
            status = "timeout" if deadline_hit else "cancelled"
            message = "本次任务剩余时间不足，已停止" if deadline_hit else "工具调用已取消"
            self.ctx.tool_calls.append(dict(name=name, status=status, message=message,
                                            elapsed_ms=round((time.perf_counter() - started_at) * 1000)))
            result_json = json.dumps({"status": status, "message": message})
            raise
        except Exception:
            event = ToolResult(status="backend_error", message="工具执行异常")
            self.ctx.tool_calls.append(dict(name=name, status=event.status,
                                            message=event.message,
                                            elapsed_ms=round((time.perf_counter() - started_at) * 1000)))
            result_json = event.model_dump_json()
            raise
        finally:
            if started:
                await self.rt.hooks.on_tool_end(context, None, tool, result_json)

    async def search(self, query: str, area: str | None = None, fyp: str | None = None,
                     top_k: int | None = None) -> ToolResult:
        if top_k is None:
            top_k = self.ctx.settings.top_k_default
        scope = self.ctx.scope
        return await self._tool("kb_search", lambda: _call(
            self.ctx, "kb_search", self.ctx.kb.search, query, area, fyp, top_k,
            planids=scope))

    async def read(self, planid: str, section_idx: int | None = None,
                   chunk_id: str | None = None, offset: int = 0) -> ToolResult:
        async def invoke() -> str:
            started = time.perf_counter()
            blocked = out_of_scope(self.ctx, planid)
            if blocked:
                result = ToolResult(status="invalid_argument", message=blocked)
                self.ctx.tool_calls.append(dict(name="kb_read", status=result.status,
                                                message=result.message,
                                                elapsed_ms=round((time.perf_counter() - started) * 1000)))
                return result.model_dump_json()
            return await _call(self.ctx, "kb_read", self.ctx.kb.read, planid,
                               section_idx, self.ctx.settings.read_max_chars,
                               chunk_id=chunk_id, offset=offset)

        return await self._tool("kb_read", invoke)

    async def call_model(self, messages, max_tokens: int | None = None) -> str:
        self._remaining()
        cap = self.ctx.settings.llm_max_output_tokens
        if max_tokens is None:
            token_limit = cap
        elif type(max_tokens) is not int or max_tokens <= 0:
            raise BusinessError("INVALID_INPUT")
        else:
            token_limit = min(max_tokens, cap)
        if self._model_calls >= self.ctx.settings.max_turns:
            raise BusinessError("MAX_TURNS")
        self._model_calls += 1
        call_index = self._model_calls
        started = time.monotonic()
        request_timeout = self.ctx.settings.llm_request_timeout
        record = {"input_tokens": None, "output_tokens": None,
                  "finish_reason": None, "status": "error", "error_code": None}
        try:
            remaining = self._remaining()
            timeout = min(request_timeout, remaining)
            model_kwargs = dict(
                model=self.ctx.settings.llm_model,
                messages=messages,
                max_tokens=token_limit,
                extra_body=thinking_off(self.ctx.settings),
                timeout=timeout,
            )
            if self.ctx.settings.llm_temperature is not None:
                model_kwargs["temperature"] = self.ctx.settings.llm_temperature
            response = await asyncio.wait_for(
                self.rt.client.chat.completions.create(**model_kwargs), timeout=timeout)
            choices = getattr(response, "choices", None) or []
            content = getattr(getattr(choices[0], "message", None), "content", None) if choices else None
            if not isinstance(content, str):
                raise BusinessError("OUTPUT_PARSE")
            usage = getattr(response, "usage", None)
            record.update(input_tokens=getattr(usage, "prompt_tokens", None),
                          output_tokens=getattr(usage, "completion_tokens", None),
                          finish_reason=getattr(choices[0], "finish_reason", None), status="ok")
            return content
        except BusinessError as exc:
            record["error_code"] = exc.code
            raise
        except asyncio.CancelledError:
            record["error_code"] = "RUN_TIMEOUT" if time.monotonic() >= self.deadline else "CANCELLED"
            raise
        except APITimeoutError as exc:
            record["error_code"] = "RUN_TIMEOUT" if time.monotonic() >= self.deadline else "MODEL_UNAVAILABLE"
            raise BusinessError(record["error_code"]) from exc
        except TimeoutError as exc:
            record["error_code"] = "RUN_TIMEOUT" if time.monotonic() >= self.deadline else "MODEL_UNAVAILABLE"
            raise BusinessError(record["error_code"]) from exc
        except Exception as exc:
            code = self._model_error_code(exc)
            record["error_code"] = code
            raise BusinessError(code) from exc
        finally:
            elapsed_ms = round((time.monotonic() - started) * 1000)
            record["elapsed_ms"] = elapsed_ms
            self.telemetry["model_calls"] += 1
            self.telemetry["input_tokens"] += record["input_tokens"] or 0
            self.telemetry["output_tokens"] += record["output_tokens"] or 0
            self.telemetry["per_call"].append(record)
            self.rt.log("business_model_call", call=call_index, **record)

    @staticmethod
    def _model_error_code(exc: Exception) -> str:
        if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
            return "MODEL_UNAVAILABLE"
        if isinstance(exc, APIStatusError):
            return ("MODEL_UNAUTHORIZED" if exc.status_code in (401, 403) else
                    "MODEL_NOT_FOUND" if exc.status_code == 404 else
                    "MODEL_UNAVAILABLE" if exc.status_code == 429 or exc.status_code >= 500
                    else "MODEL_BAD_REQUEST")
        return "INTERNAL"
