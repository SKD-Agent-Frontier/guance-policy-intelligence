"""只从本地配置和环境读取设置。"""
import math
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    llm_base_url: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = "qwen3.8-27b"
    structured_output: bool = False
    llm_temperature: float | None = None
    llm_request_timeout: float = 60
    llm_max_retries: int = 2
    max_input_chars: int = 4000
    max_turns: int = 8
    max_tool_calls: int = 20
    run_timeout: float = 180
    tool_timeout: float = 20
    top_k_default: int = 8
    top_k_max: int = 20
    search_snippet_chars: int = 300
    read_max_chars: int = 6000
    kb_backend: str = "mock"
    mock_kb_path: Path = ROOT / "mock_data/kb.json"
    session_path: Path = ROOT / "data/sessions.sqlite"
    run_log: Path = ROOT / "data/runs.jsonl"
    log_content: bool = False

    def __post_init__(self):
        for name in ("max_input_chars", "max_turns", "top_k_default", "top_k_max",
                     "search_snippet_chars", "read_max_chars", "llm_max_retries",
                     "max_tool_calls"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} 必须为整数")
        positive = ("llm_request_timeout", "max_input_chars", "max_turns", "run_timeout",
                    "tool_timeout", "top_k_default", "top_k_max", "search_snippet_chars",
                    "read_max_chars", "max_tool_calls")
        for name in positive:
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须为正数")
        if self.top_k_default > self.top_k_max or self.llm_max_retries < 0:
            raise ValueError("top_k_default / llm_max_retries 超出范围")
        if self.kb_backend != "mock":
            raise ValueError("KB_BACKEND 仅支持 mock")
        if self.llm_temperature is not None and not 0 <= self.llm_temperature <= 2:
            raise ValueError("LLM_TEMPERATURE 必须在 0..2")
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
