import pytest

from guance_agent.config import Settings
from guance_agent.schemas import TaskRequest


@pytest.fixture
def env(tmp_path):
    s = Settings(session_path=tmp_path / "sessions.sqlite", run_log=tmp_path / "runs.jsonl")
    task = TaskRequest(run_id="run_test", session_id="s_test",
                       question="甲市农业重点", created_at="2026-10-23T00:00:00Z")
    return s, task
