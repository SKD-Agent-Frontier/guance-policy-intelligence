"""Minimal contract shared by business modules and the Agent runner."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from ..schemas import Claim

if TYPE_CHECKING:
    from ..context import RunContext
    from ..schemas import TaskRequest
    from .services import BusinessServices


class BusinessOutcome(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    structured: dict = Field(default_factory=dict)
    insufficient: bool = False


def normalize_structured(value: dict) -> dict:
    """Return a JSON-safe copy of structured output or reject it before assembly."""
    if not isinstance(value, dict):
        raise ValueError("structured output must be a dictionary")

    try:
        def check_keys(item):
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError("structured output keys must be strings")
                for child in item.values():
                    check_keys(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    check_keys(child)

        check_keys(value)
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("structured output is not valid JSON") from exc


class BusinessError(Exception):
    """A business module failure using the Agent's existing error vocabulary."""

    def __init__(self, code: str):
        from ..output import ERRORS

        self.code = code if isinstance(code, str) and code in ERRORS else "INTERNAL"
        super().__init__(ERRORS[self.code])


class BusinessModule(Protocol):
    # 可选属性：requires_targets=False 时允许开放主题执行。
    name: str

    async def __call__(
        self,
        task: TaskRequest,
        ctx: RunContext,
        services: BusinessServices,
    ) -> BusinessOutcome: ...
