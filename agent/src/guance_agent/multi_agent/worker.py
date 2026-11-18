"""SDK-based Research Worker; each worker remains behind existing verification gates."""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from uuid import uuid4

from .contracts import Assignment, WorkerOutcome
from ..orchestrator import run_sdk_task
from ..routing import decide
from ..runtime import Runtime


def child_question(assignment: Assignment, max_chars: int) -> str:
    original = assignment.original_question.strip()
    focus = assignment.focus.strip()
    supplement = "\n本轮子任务只研究以下要求：" + focus + (
        "\n不得扩大用户指定的地区、时期、planid 范围。"
        "对于其他要求不必回答，引用须来自本轮已核验原文。"
    )
    return original + supplement if len(original) + len(supplement) <= max_chars else original


async def run_worker(assignment: Assignment, parent_task, settings, *,
                     seconds: float, discoverer=None) -> WorkerOutcome:
    """Run a separately budgeted native research core and return its verified output."""
    began = time.monotonic()
    if seconds < 3:
        return WorkerOutcome(assignment=assignment, status="budget_exhausted")
    child = parent_task.model_copy(update={
        "run_id": "run_" + uuid4().hex,
        "session_id": "ma_" + uuid4().hex[:30],
        "question": child_question(assignment, settings.max_input_chars),
        "targets": list(assignment.targets) if assignment.targets is not None else None,
    })
    child_settings = replace(
        settings,
        qa_engine="hyper",
        run_timeout=max(3, min(settings.run_timeout, seconds)),
        max_tool_calls=max(3, min(settings.max_tool_calls, settings.max_tool_calls // 2)),
    )
    rt = Runtime(child_settings, child)
    result = None
    try:
        rt.received()
        rt.prepare()
        result = await asyncio.wait_for(
            run_sdk_task(
                child, child_settings, rt,
                initial_decision=decide(child.question),
                research_discoverer=discoverer,
                experimental_hyper=True,
            ),
            timeout=max(3, seconds),
        )
        core = result.meta.get("research_core") or {}
        return WorkerOutcome(
            assignment=assignment,
            status=("completed" if result.status == "completed" else result.status),
            result=result,
            elapsed_seconds=round(time.monotonic() - began, 3),
            model_calls=int(core.get("model_calls") or 0),
            tool_calls=int((core.get("remaining") or {}).get("search_read", 0)),
        )
    except TimeoutError:
        return WorkerOutcome(
            assignment=assignment, status="timeout",
            elapsed_seconds=round(time.monotonic() - began, 3), error_type="TimeoutError",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return WorkerOutcome(
            assignment=assignment, status="failed",
            elapsed_seconds=round(time.monotonic() - began, 3),
            error_type=type(exc).__name__,
        )
    finally:
        rt.close()
