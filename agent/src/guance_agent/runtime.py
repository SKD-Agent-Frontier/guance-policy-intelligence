"""会话、客户端复用和本地脱敏运行日志。"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from time import perf_counter

from agents import RunHooks, SQLiteSession, set_tracing_disabled
from openai import AsyncOpenAI

from .agent import prompt_version
from .config import Settings
from .context import sanitize_error_fields

set_tracing_disabled(True)

class SessionForbidden(PermissionError):
    """Raised when a session is accessed with a different owner identity."""

class ToolBudgetExceeded(Exception):
    """工具调用总数超过预算。"""

@lru_cache(maxsize=1)
def get_client(s: Settings):
    if not s.llm_base_url or not s.llm_api_key:
        raise ValueError("缺少模型本地配置")
    return AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key,
                       timeout=s.llm_request_timeout, max_retries=s.llm_max_retries)

def open_session(s, session_id, user_id):
    s.session_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(s.session_path)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS guance_session_owner "
                     "(session_id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT user_id FROM guance_session_owner WHERE session_id=?",
                           (session_id,)).fetchone()
        if row and row[0] != user_id:
            raise SessionForbidden("会话归属与当前用户不匹配")
        created = row is None
        if created:
            conn.execute("INSERT INTO guance_session_owner VALUES (?, ?, ?)",
                         (session_id, user_id, datetime.now(timezone.utc).isoformat()))
    return SQLiteSession(session_id, s.session_path), created

class Runtime:
    def __init__(self, s, task):
        self.settings, self.task = s, task
        self.started = perf_counter()
        self.session = None
        self.hooks = LogHooks(self)
        self.meta = dict(model=s.llm_model, prompt_version=prompt_version(),
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
        values = sanitize_error_fields(values, self.settings)
        event = dict(time=datetime.now(timezone.utc).isoformat(), run_id=self.task.run_id,
                     session_id=self.task.session_id, stage=stage, tool=None,
                     elapsed_ms=round((perf_counter() - self.started) * 1000),
                     status=None, error_code=None)
        event.update(values)
        payload = json.dumps(event, ensure_ascii=False)
        for secret in (self.settings.llm_api_key, self.settings.llm_base_url):
            if secret:
                payload = payload.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[REDACTED]")
        try:
            self.settings.run_log.parent.mkdir(parents=True, exist_ok=True)
            with self.settings.run_log.open("a", encoding="utf-8") as stream:
                stream.write(payload + "\n")
        except OSError:
            # ponytail: 运行日志是尽力而为的观测手段，写不进去不应毁掉已经生成的答案。
            pass

    def received(self):
        question = self.task.question
        content = {"question": question} if self.settings.log_content else {}
        self.log("received", question_chars=len(question),
                 question_hash=sha256(question.encode()).hexdigest()[:12], **content)

    def finish(self, result, ctx):
        if ctx:
            ctx.tool_calls[:] = sanitize_error_fields(ctx.tool_calls, self.settings)
        result.meta.update(elapsed_ms=round((perf_counter() - self.started) * 1000),
                           tool_calls=ctx.tool_calls if ctx else [])
        if ctx:
            for chunk_id in ctx.evidence.conflicts:
                self.log("evidence_conflict", chunk_id=chunk_id)
        content = {"answer": result.answer} if self.settings.log_content else {}
        self.log("run_end", status=result.status, evidence_status=result.evidence_status,
                 error_code=result.error.code if result.error else None,
                 no_tool_answer=result.meta.get("no_tool_answer", False),
                 evidence_ids=[r.chunk_id for r in result.evidence_refs],
                 dropped_chunk_ids=result.dropped_chunk_ids, **self.meta, **content)

    def close(self):
        if self.session:
            self.session.close()

class LogHooks(RunHooks):
    def __init__(self, runtime):
        self.rt, self.starts, self.turns, self.calls = runtime, {}, 0, 0
        self.llm_started = perf_counter()

    async def on_tool_start(self, context, agent, tool):
        self.calls += 1
        if self.calls > self.rt.settings.max_tool_calls:
            raise ToolBudgetExceeded(f"已调用 {self.calls - 1} 次 / 上限 {self.rt.settings.max_tool_calls}")
        self.starts[getattr(context, "tool_call_id", id(context))] = perf_counter()
        self.rt.log("tool_start", tool=tool.name)

    async def on_tool_end(self, context, agent, tool, result):
        started = self.starts.pop(getattr(context, "tool_call_id", id(context)), perf_counter())
        payload = json.loads(str(result))
        calls = context.context.tool_calls
        detail = next((c.get("error_detail") for c in reversed(calls) if c["name"] == tool.name), None)
        self.rt.log("tool_end", tool=tool.name, status=payload["status"], error_detail=detail,
                    elapsed_ms=round((perf_counter() - started) * 1000))

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self.llm_started = perf_counter()

    async def on_llm_end(self, context, agent, response):
        self.turns += 1
        self.rt.log("llm_end", turn=self.turns,
                    elapsed_ms=round((perf_counter() - self.llm_started) * 1000))
