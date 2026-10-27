"""Explicit registry for implemented business modules; intentionally empty."""
from __future__ import annotations

import re

from .contract import BusinessError, BusinessModule, BusinessOutcome
from .services import BusinessServices

_MODULES: dict[str, BusinessModule] = {}
_RESERVED = frozenset({"qa", "extract"})
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def register(module: BusinessModule) -> None:
    name = getattr(module, "name", None)
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name in _RESERVED:
        raise ValueError("业务模块名称不合法或为保留名称")
    if name in _MODULES:
        raise ValueError(f"业务模块已注册：{name}")
    if not callable(module):
        raise TypeError("业务模块必须可调用")
    _MODULES[name] = module


def get_module(name: str) -> BusinessModule | None:
    return _MODULES.get(name)


__all__ = ["BusinessServices", "BusinessModule", "BusinessOutcome", "BusinessError",
           "get_module", "register"]
