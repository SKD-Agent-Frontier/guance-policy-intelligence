"""会话、客户端复用和本地脱敏运行日志。"""
import json
from datetime import datetime, timezone
from hashlib import sha256
from time import perf_counter

from agents import RunHooks, SQLiteSession, set_tracing_disabled
from agents.memory import SessionSettings

from .agent import prompt_version
from .client import get_client
from .context import sanitize_error_detail, sanitize_error_fields
from .sessions import SessionForbidden, claim_session  # noqa: F401 (再导出)

set_tracing_disabled(True)

class ToolBudgetExceeded(Exception):
    """工具调用总数超过预算。"""

def open_session(s, session_id, user_id):
    created = claim_session(s, session_id, user_id)
    # ponytail: limit 按条数截断，可能切断工具调用组；接入长会话前需实测再调。
    session = SQLiteSession(session_id, s.session_path,
                            session_settings=SessionSettings(limit=s.max_history_items))
    return session, created


class Runtime:
    def __init__(self, s, task):
        self.settings, self.task = s, task
        self.started = perf_counter()
        self.session = None
        self.log_error = None
        self.hooks = LogHooks(self)
        self.meta = dict(model=s.llm_model, prompt_version=prompt_version(s.structured_output),
                         tools=["kb_search", "kb_read"], kb_backend=s.kb_backend)

    @property
    def client(self):
        return get_client(self.settings)

    def prepare(self):
        self.session, created = open_session(
            self.settings, self.task.session_id, self.task.user_id)
        self.meta["session_created"] = created
        self.log("run_start", **self.meta)

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
        except OSError as exc:
            # ponytail: 清理失败不覆盖结果，只记日志；进程级资源由 client.aclose_client 收尾。
            self.log("close_failed", error_detail=type(exc).__name__)

class LogHooks(RunHooks):
    def __init__(self, runtime):
        self.rt, self.starts, self.turns, self.calls = runtime, {}, 0, 0
        self.llm_started = perf_counter()

    async def on_tool_start(self, context, agent, tool):
        self.calls += 1
        if self.calls > self.rt.settings.max_tool_calls:
            self.rt.log("tool_rejected", tool=tool.name, calls=self.calls,
                        limit=self.rt.settings.max_tool_calls)
            raise ToolBudgetExceeded(f"已调用 {self.calls - 1} 次 / 上限 {self.rt.settings.max_tool_calls}")
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

    async def on_llm_end(self, context, agent, response):
        self.turns += 1
        self.rt.log("llm_end", turn=self.turns,
                    elapsed_ms=round((perf_counter() - self.llm_started) * 1000))
