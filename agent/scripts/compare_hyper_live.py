"""Reproducible LIVE comparison. Never run from CI or without explicit consent.

Run inside agent/: python scripts/compare_hyper_live.py --allow-real
Requires .env with authorized campus Qwen and published RAG store.
Output is local-only under agent/data/ (gitignored).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from guance_agent.client import aclose_client
from guance_agent.config import load_settings
from guance_agent.kb import close_kb, warm_up
from guance_agent.main import submit

DEFAULT_QUESTIONS = [
    "列出所有与舆论相关的政策，按地区、政策名称和主要措施整理，并说明是否能够证明完整覆盖。",
    "比较浙江省和江苏省十四五时期数字经济政策的重点方向、支持措施及异同，附原文依据。",
    "归纳政策文件中的发展目标，明确区分政策目标和已经实现的事实，提供对应来源。",
]


async def compare(questions: list[str], destination: Path, runs_per_case: int) -> None:
    base = load_settings()
    if base.kb_backend != "rag" or not base.llm_base_url or not base.llm_api_key:
        raise SystemExit(
            "本脚本只用于真实验收：需要 KB_BACKEND=rag 和已配置的校内 Qwen 模型。"
        )
    warm_up(base)
    rows = []
    try:
        for index, question in enumerate(questions, start=1):
            for repetition in range(1, runs_per_case + 1):
                for engine in ("sdk", "hyper"):
                    settings = replace(base, qa_engine=engine)
                    session_id = "hpr_eval_" + uuid4().hex[:24]
                    started = time.perf_counter()
                    result = await submit(
                        question, session_id=session_id, settings=settings, task_type="qa"
                    )
                    elapsed = round(time.perf_counter() - started, 3)
                    structured = result.structured if isinstance(result.structured, dict) else {}
                    blocks = structured.get("blocks") or []
                    meta = result.meta or {}
                    research = meta.get("research_core") or {}
                    records = []
                    for ref in result.evidence_refs:
                        records.append({
                            "planid": ref.planid, "title": ref.title,
                            "chunk_id": ref.chunk_id,
                        })
                    rows.append({
                        "case": index, "repetition": repetition, "engine": engine,
                        "question": question, "question_sha256": hashlib.sha256(
                            question.encode("utf-8")
                        ).hexdigest(),
                        "elapsed_seconds": elapsed,
                        "run_status": result.status,
                        "evidence_status": result.evidence_status,
                        "research_status": (meta.get("research_quality") or {}).get("status"),
                        "claims_count": len(result.claims),
                        "sources_count": len(set(ref.planid for ref in result.evidence_refs)),
                        "approved_analysis_blocks": sum(
                            block.get("kind") == "analysis" for block in blocks
                            if isinstance(block, dict)
                        ),
                        "model_calls": research.get("model_calls"),
                        "remaining": research.get("remaining"),
                        "experiment": meta.get("hyperresearch_experiment"),
                        "answer": result.answer,
                        "warnings": result.warnings,
                        "evidence_refs": records,
                    })
                    print(
                        f"case={index} try={repetition} engine={engine} "
                        f"status={result.status} sources={rows[-1]['sources_count']} "
                        f"claims={len(result.claims)} duration={elapsed}s",
                        flush=True,
                    )
    finally:
        close_kb()
        await aclose_client()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({
                "created_at": datetime.now(UTC).isoformat(),
                "note": "是真实端点执行记录，不代表自动正确率；须人工核验对照。",
                "results": rows,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(f"本地对照结果：{destination}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-real", action="store_true")
    parser.add_argument("--questions-json", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/hyper-vs-sdk-live.json"))
    parser.add_argument("--runs-per-case", type=int, default=1)
    args = parser.parse_args()
    if not args.allow_real:
        parser.error("真实模型会产生调用开销；明确加 --allow-real 才会运行")
    if not 1 <= args.runs_per_case <= 3:
        parser.error("--runs-per-case 必须在1到3之间")
    questions = DEFAULT_QUESTIONS
    if args.questions_json:
        questions = json.loads(args.questions_json.read_text(encoding="utf-8"))
    if not isinstance(questions, list) or not questions or any(
        not isinstance(q, str) or not q.strip() for q in questions
    ):
        parser.error("questions-json 必须是非空问题字符串数组")
    asyncio.run(compare(questions, args.output, args.runs_per_case))


if __name__ == "__main__":
    main()
