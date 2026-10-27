"""Independent organize module; importing this module never registers it."""

import time

from guance_agent.business import BusinessError, BusinessOutcome, BusinessServices
from guance_agent.context import RunContext
from guance_agent.schemas import TaskRequest

from .constants import MAX_MODEL_CALLS, MODEL_RESERVE_S
from .discovery import discover
from .prompt import build_messages
from .reading import read_candidates
from .scope import parse_scope
from .synthesis import assemble, insufficient_outcome, parse_and_validate


class OrganizeModule:
    name = "organize"
    requires_targets = False

    async def __call__(self, task: TaskRequest, ctx: RunContext,
                       services: BusinessServices) -> BusinessOutcome:
        scope = parse_scope(task, ctx)
        found = await discover(scope, services)
        if not found.candidates:
            return insufficient_outcome()
        reading = await read_candidates(found, ctx, services)
        if not reading.windows:
            return insufficient_outcome()
        messages = build_messages(scope, reading)
        for attempt in range(MAX_MODEL_CALLS):
            # Service errors propagate unchanged; only malformed returned JSON retries.
            raw = await services.call_model(messages)
            try:
                synthesis = parse_and_validate(raw, reading, ctx, scope)
            except (ValueError, RecursionError) as exc:
                if attempt + 1 == MAX_MODEL_CALLS or services.deadline - time.monotonic() <= MODEL_RESERVE_S:
                    raise BusinessError("OUTPUT_PARSE") from exc
                messages = [*messages, {"role": "user", "content":
                            "上次输出无法解析。只输出一个完整的 JSON 对象，必须包含 policies 数组。"}]
                continue
            return assemble(scope, found, reading, synthesis)
        raise BusinessError("OUTPUT_PARSE")
