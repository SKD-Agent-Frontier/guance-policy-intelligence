"""检索只引用已有发布；运行缓存写到本工程，不回写切块或数据库。"""
from dataclasses import dataclass
from pathlib import Path
import math

ENGINE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE = ENGINE_ROOT.parent / "向量化与存储工程（降级版）"


@dataclass(frozen=True)
class RuntimeOptions:
    store_project: Path = DEFAULT_STORE
    cache_directory: Path = ENGINE_ROOT / ".cache"
    mode: str = "hybrid"
    queue_capacity: int = 32
    queue_timeout: float = 20.0
    execution_timeout: float = 30.0
    startup_timeout: float = 180.0
    close_timeout: float = 60.0
    snippet_chars: int = 300
    max_read_chars: int = 6000
    max_context_chars: int = 6000
    max_search_chars: int = 16000
    top_k_max: int = 20

    def __post_init__(self):
        for name in ("store_project", "cache_directory"):
            object.__setattr__(self, name, Path(getattr(self, name)).resolve())
        if self.mode not in {"bm25", "dense", "hybrid"}:
            raise ValueError("mode必须为bm25、dense或hybrid")
        for name in ("queue_capacity", "snippet_chars", "max_read_chars", "max_context_chars", "max_search_chars", "top_k_max"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(name + "必须为正整数")
        for name in ("queue_timeout", "execution_timeout", "startup_timeout", "close_timeout"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(name + "必须为正数")
