"""A: unchanged application baseline. Run from agent/: python experiments/run.py --question '...'."""
import argparse
import asyncio
import json
import time
from guance_agent.client import aclose_client
from guance_agent.config import load_settings
from guance_agent.kb import warm_up, close_kb
from guance_agent.main import submit

async def execute(question: str) -> dict:
    settings = load_settings()
    started = time.monotonic()
    try:
        await asyncio.to_thread(warm_up, settings)
        result = await asyncio.wait_for(submit(question, settings=settings), timeout=settings.run_timeout + 5)
        return {
            "variant": "A", "question": question,
            "status": result.status, "answer": result.answer,
            "evidence_status": result.evidence_status,
            "evidence_planids": sorted({ref.planid for ref in result.evidence_refs}),
            "warnings": result.warnings, "error": result.error.model_dump() if result.error else None,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "meta": {"qa_engine": settings.qa_engine, "kb_backend": settings.kb_backend,
                     "tool_calls": result.meta.get("tool_calls", []),
                     "llm": result.meta.get("llm")},
        }
    finally:
        close_kb()
        await aclose_client()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(execute(args.question)), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"variant": "A", "status": "failed",
                          "error_type": type(exc).__name__}, ensure_ascii=False))
        raise SystemExit(1)
