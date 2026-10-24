"""会话归属登记：只在首次创建时进入写事务，避免每请求抢写锁。"""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

TABLE = "guance_session_owner"


class SessionForbidden(PermissionError):
    """Raised when a session is accessed with a different owner identity."""


def claim_session(s, session_id: str, user_id: str | None) -> bool:
    """登记会话归属并返回是否为新建。归属不符抛 SessionForbidden。"""
    s.session_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(s.session_path)) as conn:
        ready = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (TABLE,)).fetchone()
        row = conn.execute(f"SELECT user_id FROM {TABLE} WHERE session_id=?",
                           (session_id,)).fetchone() if ready else None
    if row is not None:
        if row[0] != user_id:
            raise SessionForbidden("会话归属与当前用户不匹配")
        return False
    with closing(sqlite3.connect(s.session_path)) as conn, conn:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} "
                     "(session_id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        again = conn.execute(f"SELECT user_id FROM {TABLE} WHERE session_id=?",
                             (session_id,)).fetchone()
        if again is None:
            conn.execute(f"INSERT INTO {TABLE} VALUES (?, ?, ?)",
                         (session_id, user_id, datetime.now(timezone.utc).isoformat()))
            return True
        if again[0] != user_id:
            raise SessionForbidden("会话归属与当前用户不匹配")
        return False
