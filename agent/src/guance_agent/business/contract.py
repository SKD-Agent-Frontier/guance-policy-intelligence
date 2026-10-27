"""Minimal contract shared by business modules and the Agent runner."""
from __future__ import annotations

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


class BusinessError(Exception):
    """A business module failure using the Agent's existing error vocabulary."""

    def __init__(self, code: str):
        from ..output import ERRORS

        self.code = code if isinstance(code, str) and code in ERRORS else "INTERNAL"
        super().__init__(ERRORS[self.code])


class BusinessModule(Protocol):
    name: str

    async def __call__(
        self,
        task: TaskRequest,
        ctx: RunContext,
        services: BusinessServices,
    ) -> BusinessOutcome: ...
