"""会话、客户端复用和本地脱敏运行日志。"""
import asyncio
import json
from datetime import datetime, timezone
from hashlib import sha256
from time import perf_counter

from agents import RunHooks, SQLiteSession, set_tracing_disabled

from .agent import prompt_version
from .client import get_client, thinking_off
from .context import sanitize_error_detail, sanitize_error_fields
from .qa_context import TrustedContextStore
from .sessions import SessionForbidden, claim_session  # noqa: F401 (再导出)

set_tracing_disabled(True)

class ToolBudgetExceeded(Exception):
    """工具调用总数超过预算。"""

class TrimmedSession(SQLiteSession):
    """历史按回合边界截断：保留最后 N 条，但起点必须是 user 消息。

    function_call 与其 function_call_output 按 call_id 成对出现，普通截断会把它们拆开，
    所以宁可少给几轮上下文，也不从半条回合开始。
    ponytail: 为找回合边界需要读全量历史；会话很长时应改成按回合存储。
    """

    def __init__(self, session_id, db_path, max_items):
        super().__init__(session_id, db_path)
        self.max_items = max_items

    async def get_items(self, limit=None):
        items = await super().get_items()
        window = items[-limit:] if limit else items[-self.max_items:]
        for index, item in enumerate(window):
            role = item.get("role") if isinstance(item, dict) else getattr(item, "role", None)
            if role == "user":
                return window[index:]
        return []


def open_session(s, session_id, user_id):
    created = claim_session(s, session_id, user_id)
    return TrimmedSession(session_id, s.session_path, s.max_history_items), created


class Runtime:
    def __init__(self, s, task):
        self.settings, self.task = s, task
        self.started = perf_counter()
        self.session = None
        self.context_store = None
        self.trusted_turns: list[dict] = []
        self.log_error = None
        self.hooks = LogHooks(self)
        self.meta = dict(model=s.llm_model, prompt_version=prompt_version(s.structured_output, task.task_type),
                         tools=["kb_search", "kb_read"], kb_backend=s.kb_backend)

    @property
    def client(self):
        return get_client(self.settings)

    async def complete_text(self, messages, *, max_tokens: int, timeout: float,
                            purpose: str = "text_completion") -> str:
        """Call the text model and record privacy-safe per-purpose telemetry."""
        started = perf_counter()
        request_status = "failed"
        input_tokens = output_tokens = None
        finish_reason = None
        try:
            response = await self.client.chat.completions.create(
                model=self.settings.llm_model, messages=messages, max_tokens=max_tokens,
                extra_body=thinking_off(self.settings), timeout=timeout)
            finish_reason = getattr(response.choices[0], "finish_reason", None)
            if finish_reason not in {"stop", "length", "tool_calls", "content_filter", "function_call"}:
                finish_reason = None
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", None)
            if input_tokens is None:
                input_tokens = getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None)
            if output_tokens is None:
                output_tokens = getattr(usage, "completion_tokens", None)
            content = getattr(getattr(response.choices[0], "message", None), "content", None)
            if not isinstance(content, str) or not content.strip():
                request_status = "empty_output"
                raise ValueError("empty_model_output")
            request_status = "completed"
            return content
        except (Exception, asyncio.CancelledError) as exc:
            self._record_text_request(
                purpose, request_status, started, input_tokens, output_tokens,
                error=type(exc).__name__, finish_reason=finish_reason,
            )
            raise
        finally:
            if request_status == "completed":
                self._record_text_request(
                    purpose, request_status, started, input_tokens, output_tokens,
                    finish_reason=finish_reason)

    def _record_text_request(self, purpose, status, started, input_tokens,
                             output_tokens, error=None, finish_reason=None):
        entry = {
            "purpose": str(purpose)[:64], "status": status,
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "input_tokens": input_tokens, "output_tokens": output_tokens,
        }
        if error:
            entry["error"] = error
        if finish_reason is not None:
            entry["finish_reason"] = finish_reason
        requests = self.meta.setdefault("text_model_requests", [])
        requests.append(entry)
        aggregate = self.meta.setdefault("text_model_usage", {
            "model_calls": 0, "successful_calls": 0, "failed_calls": 0,
            "input_tokens": 0, "output_tokens": 0,
            "calls_with_input_usage": 0, "calls_with_output_usage": 0,
            "by_purpose": {},
        })
        aggregate["model_calls"] += 1
        aggregate["successful_calls" if status == "completed" else "failed_calls"] += 1
        if input_tokens is not None:
            aggregate["input_tokens"] += int(input_tokens)
            aggregate["calls_with_input_usage"] += 1
        if output_tokens is not None:
            aggregate["output_tokens"] += int(output_tokens)
            aggregate["calls_with_output_usage"] += 1
        per_purpose = aggregate["by_purpose"].setdefault(str(purpose)[:64], {
            "model_calls": 0, "successful_calls": 0, "failed_calls": 0,
            "input_tokens": 0, "output_tokens": 0,
            "calls_with_input_usage": 0, "calls_with_output_usage": 0,
        })
        per_purpose["model_calls"] += 1
        per_purpose["successful_calls" if status == "completed" else "failed_calls"] += 1
        if input_tokens is not None:
            per_purpose["input_tokens"] += int(input_tokens)
            per_purpose["calls_with_input_usage"] += 1
        if output_tokens is not None:
            per_purpose["output_tokens"] += int(output_tokens)
            per_purpose["calls_with_output_usage"] += 1

    def prepare(self):
        self.session, created = open_session(
            self.settings, self.task.session_id, self.task.user_id)
        self.meta["session_created"] = created
        # Session ownership has already been claimed above; the sidecar is keyed
        # by that authorized session id, never used as authentication by itself.
        self.context_store = TrustedContextStore(
            self.settings.session_path, self.task.session_id)
        self.trusted_turns = self.context_store.load()
        self.meta["qa_context_available"] = bool(self.trusted_turns)
        self.log("run_start", **self.meta)

    def save_trusted_turn(self, turn: dict | None):
        """Persist only source identities from an actually executed turn."""
        if not turn or self.context_store is None:
            return
        try:
            self.context_store.append(turn)
        except OSError as exc:
            self.meta["qa_context_save_error"] = type(exc).__name__

    def log(self, stage, **values):
        event = dict(time=datetime.now(timezone.utc).isoformat(), run_id=self.task.run_id,
                     session_id=self.task.session_id, stage=stage, tool=None,
                     elapsed_ms=round((perf_counter() - self.started) * 1000),
                     status=None, error_code=None)
        event.update(sanitize_error_fields(values, self.settings, question=self.task.question))
        payload = sanitize_error_detail(json.dumps(event, ensure_ascii=False),
                                        self.settings, self.task.question, limit=None)
        try:
            self.settings.run_log.parent.mkdir(parents=True, exist_ok=True)
            with self.settings.run_log.open("a", encoding="utf-8") as stream:
                stream.write(payload + "\n")
        except OSError as exc:
            # ponytail: 日志尽力而为，写不进去不影响回答；失败标志由 finish 写入结果 meta。
            self.log_error = type(exc).__name__

    def received(self):
        question = self.task.question
        content = {"question": question} if self.settings.log_content else {}
        self.log("received", question_chars=len(question),
                 question_hash=sha256(question.encode()).hexdigest()[:12], **content)

    def finish(self, result, ctx):
        if ctx:
            ctx.tool_calls[:] = sanitize_error_fields(
                ctx.tool_calls, self.settings, question=self.task.question)
            for chunk_id in ctx.evidence.conflicts:
                self.log("evidence_conflict", chunk_id=chunk_id)
        result.meta.update(elapsed_ms=round((perf_counter() - self.started) * 1000),
                           tool_calls=ctx.tool_calls if ctx else [])
        content = {"answer": result.answer} if self.settings.log_content else {}
        self.log("run_end", status=result.status, evidence_status=result.evidence_status,
                 error_code=result.error.code if result.error else None,
                 no_tool_answer=result.meta.get("no_tool_answer", False),
                 evidence_ids=[r.chunk_id for r in result.evidence_refs],
                 dropped_chunk_ids=result.dropped_chunk_ids, **self.meta, **content)
        if self.log_error:
            result.meta["log_write_failed"] = self.log_error

    def close(self):
        if not self.session:
            return
        try:
            self.session.close()
        except Exception as exc:   # noqa: BLE001 - 清理阶段任何异常都不应覆盖已生成的结果
            self.log("close_failed", error_detail=type(exc).__name__)

class LogHooks(RunHooks):
    def __init__(self, runtime):
        self.rt, self.starts, self.turns, self.calls = runtime, {}, 0, 0
        self.llm_started = perf_counter()
        self.usage_per_call: list[dict] = []

    async def on_tool_start(self, context, agent, tool):
        self.calls += 1
        state = getattr(self, "core_state", None)
        limit = 48 if state is not None else self.rt.settings.max_tool_calls
        if self.calls > limit:
            self.rt.log("tool_rejected", tool=tool.name, calls=self.calls,
                        limit=limit)
            raise ToolBudgetExceeded(f"已调用 {self.calls - 1} 次 / 上限 {limit}")
        self.starts[getattr(context, "tool_call_id", id(context))] = perf_counter()
        self.rt.log("tool_start", tool=tool.name)

    async def on_tool_end(self, context, agent, tool, result):
        started = self.starts.pop(getattr(context, "tool_call_id", id(context)), perf_counter())
        try:
            status = json.loads(str(result))["status"]
        except (ValueError, KeyError, TypeError):
            status = "unparsed"
        calls = context.context.tool_calls
        detail = next((c.get("error_detail") for c in reversed(calls) if c["name"] == tool.name), None)
        self.rt.log("tool_end", tool=tool.name, status=status, error_detail=detail,
                    elapsed_ms=round((perf_counter() - started) * 1000))

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self.llm_started = perf_counter()
        state = getattr(self, "core_state", None)
        if state is not None:
            state.begin_model_call()

    async def on_llm_end(self, context, agent, response):
        self.turns += 1
        usage = getattr(response, "usage", None)
        call = {"input": getattr(usage, "input_tokens", None),
                "output": getattr(usage, "output_tokens", None)}
        self.usage_per_call.append(call)
        state = getattr(self, "core_state", None)
        if state is not None:
            state.finish_model_call(usage)
        self.rt.log("llm_end", turn=self.turns,
                    elapsed_ms=round((perf_counter() - self.llm_started) * 1000),
                    input_tokens=call["input"], output_tokens=call["output"])
