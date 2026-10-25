"""只从本地配置和环境读取设置。"""
import math
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
class ConfigurationError(Exception):
    """Raised only when loading runtime or knowledge-base configuration fails."""
@dataclass(frozen=True)
class Settings:
    llm_base_url: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = "qwen3.8-27b"
    structured_output: bool = False
    llm_temperature: float | None = None
    llm_request_timeout: float = 120   # 校内端点高峰单次可达 50s+，60 会诱发重试
    llm_max_retries: int = 1   # 每次重试都很贵，整体仍受 run_timeout=180 约束
    max_input_chars: int = 4000
    max_user_id_chars: int = 64
    max_turns: int = 12
    max_tool_calls: int = 20
    max_history_items: int = 40
    run_timeout: float = 180
    tool_timeout: float = 60   # >= RAG 队列20s+执行30s，避免 Agent 先放弃而库线程仍在跑
    top_k_default: int = 20   # RAG 基准 Recall@8=95.2%、Recall@20=100%
    top_k_max: int = 20
    search_snippet_chars: int = 300
    read_max_chars: int = 6000
    kb_backend: str = "mock"
    rag_store_project: str = ""   # 留空则用 guance_rag 的默认存储工程路径
    rag_mode: str = "bm25"
    mock_kb_path: Path = ROOT / "mock_data/kb.json"
    session_path: Path = ROOT / "data/sessions.sqlite"
    run_log: Path = ROOT / "data/runs.jsonl"
    log_content: bool = False
    def __post_init__(self):
        for name in ("max_input_chars", "max_user_id_chars", "max_turns", "top_k_default", "top_k_max",
                     "search_snippet_chars", "read_max_chars", "llm_max_retries", "max_tool_calls", "max_history_items"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} 必须为整数")
        positive = ("llm_request_timeout", "max_input_chars", "max_user_id_chars", "max_turns", "run_timeout",
                    "tool_timeout", "top_k_default", "top_k_max", "search_snippet_chars", "read_max_chars",
                    "max_tool_calls", "max_history_items")
        for name in positive:
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须为正数")
        if self.top_k_default > self.top_k_max or self.llm_max_retries < 0:
            raise ValueError("top_k_default / llm_max_retries 超出范围")
        if self.kb_backend not in {"mock", "sample", "rag"}:
            raise ValueError("KB_BACKEND 仅支持 mock、sample 或 rag")
        if self.rag_mode not in {"bm25", "dense", "hybrid"}:
            raise ValueError("RAG_MODE 仅支持 bm25、dense 或 hybrid")
        if self.kb_backend == "rag" and not self.rag_store_project.strip():
            raise ValueError(
                "KB_BACKEND=rag 时必须设置 RAG_STORE_PROJECT，指向恢复后的运行库目录"
                "（guance_rag 的默认路径是源码目录，里面没有数据）")
        if self.llm_temperature is not None and not 0 <= self.llm_temperature <= 2:
            raise ValueError("LLM_TEMPERATURE 必须在 0..2")
        if not self.llm_model.strip():
            raise ValueError("LLM_MODEL 不能为空")
        if self.llm_base_url and not self.llm_base_url.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL 必须以 http:// 或 https:// 开头")
        if self.llm_api_key and self.llm_api_key != self.llm_api_key.strip():
            raise ValueError("LLM_API_KEY 不能包含首尾空白")
        for name in ("mock_kb_path", "session_path", "run_log"):
            path = Path(getattr(self, name))
            object.__setattr__(self, name, path if path.is_absolute() else ROOT / path)
def load_settings(env_file=None) -> Settings:
    values = {**dotenv_values(env_file or ROOT / ".env"), **os.environ}
    kwargs = {}
    for f in fields(Settings):
        raw = values.get(f.name.upper())
        if raw is None:
            continue
        if f.name in ("structured_output", "log_content"):
            if raw not in ("on", "off"):
                raise ValueError(f"{f.name} 只接受 on / off")
            value = raw == "on"
        elif f.name == "llm_temperature":
            value = float(raw) if raw.strip() else None
        elif f.type in (int, float):
            value = f.type(raw)
        elif f.type is Path:
            value = Path(raw)
        else:
            value = raw
        kwargs[f.name] = value
    return Settings(**kwargs)
