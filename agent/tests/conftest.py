import pytest

import guance_agent.cli as cli
import guance_agent.config as config
import guance_agent.main as main_mod
import guance_agent.orchestrator as orchestrator
from guance_agent.config import Settings
from guance_agent.schemas import TaskRequest


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch, tmp_path):
    """测试一律不读开发者本机的 agent/.env。

    否则本机把 KB_BACKEND 设成 rag 之后，本地测试会因为没装 guance_rag 而挂，
    而 CI 没有 .env 却能过——这种本地/CI 分叉比任何单个 bug 都危险。
    """
    blank = tmp_path / "absent.env"
    real = config.load_settings

    def patched(env_file=None):
        return real(env_file=blank)

    for module in (config, main_mod, orchestrator, cli):
        if hasattr(module, "load_settings"):
            monkeypatch.setattr(module, "load_settings", patched)


@pytest.fixture
def env(tmp_path):
    s = Settings(session_path=tmp_path / "sessions.sqlite", run_log=tmp_path / "runs.jsonl")
    task = TaskRequest(run_id="run_test", session_id="s_test",
                       question="甲市农业重点", created_at="2026-10-23T00:00:00Z")
    return s, task
