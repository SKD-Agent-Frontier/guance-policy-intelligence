"""Real BM25 + real model acceptance. Never generates a sample from mock data.

Run from agent/: python scripts/organize_real_run.py [--env-file PATH]
Credential values and endpoint addresses are not included in run_meta.
"""

import argparse
import asyncio
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from guance_agent.business.organize.constants import MAX_MODEL_CALLS, MIN_SUMMARIZED_DOCS
from guance_agent.config import ROOT, load_settings
from guance_agent.kb import close_kb, warm_up
from guance_agent.main import submit
from guance_agent.runtime import get_client

QUESTIONS = (
    "梳理十四五时期的科技创新政策",
    "梳理十四五时期北京市科技创新领域的相关政策",
    "梳理十四五时期的专项规划中有关数字经济的政策",
)
SAMPLE = ROOT / "src/guance_agent/business/organize/sample.json"
RUNS = ROOT / "data/organize_runs"


def git_metadata():
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                      text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT,
                                             text=True, stderr=subprocess.DEVNULL).strip())
        return {"commit_sha": sha, "working_tree_dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit_sha": None, "working_tree_dirty": None}


def sample_accepted(result, settings):
    """A real failure is saved as a run, but must not replace the accepted sample."""
    structured = result.structured or {}
    coverage = structured.get("coverage", {})
    quotes = result.meta.get("quote_verification", {})
    return (
        result.status == "completed" and result.error is None
        and result.evidence_status == "verified" and bool(result.claims)
        and structured.get("schema_version") == "organize/1"
        and coverage.get("documents_summarized", 0) >= MIN_SUMMARIZED_DOCS
        and quotes.get("approved") == len(result.claims)
        and not quotes.get("rejected") and not quotes.get("unverified")
        and "answer_rebuilt" not in result.meta
        and len(result.meta.get("tool_calls", [])) <= settings.max_tool_calls
        and 1 <= result.meta.get("business_telemetry", {}).get("model_calls", 0) <= MAX_MODEL_CALLS
    )


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def redact_strings(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [redact_strings(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact_strings(item, secrets) for key, item in value.items()}
    return value


async def acceptance(env_file=None):
    try:
        settings = load_settings(env_file=env_file)
    except (ValueError, KeyError):
        print("真实运行未开始：配置不完整或格式无效；请核对本地环境变量与 .env。")
        return 2
    missing = []
    if settings.kb_backend != "rag" or settings.rag_mode != "bm25":
        missing.append("KB_BACKEND=rag 与 RAG_MODE=bm25")
    if not settings.rag_store_project:
        missing.append("RAG_STORE_PROJECT")
    if not settings.llm_base_url or not settings.llm_api_key:
        missing.append("LLM_BASE_URL / LLM_API_KEY")
    if missing:
        print("真实运行未开始：缺少或未启用 " + "、".join(missing) + "。sample.json 待真实环境运行后补交。")
        return 2
    preheat_start = time.monotonic()
    initialized_client = False
    try:
        try:
            await asyncio.to_thread(warm_up, settings)
        except Exception as exc:
            # Exception messages can contain provider endpoints or credentials.
            print(f"真实 RAG 预热失败（{type(exc).__name__}）；未生成 sample.json。")
            return 2
        preheat_ms = round((time.monotonic() - preheat_start) * 1000)
        revision = git_metadata()
        sample_written, all_completed = False, True
        for index, question in enumerate(QUESTIONS):
            started_at = datetime.now(timezone.utc).isoformat()
            started = time.monotonic()
            initialized_client = True
            result = await submit(question=question, task_type="organize", settings=settings)
            payload = result.model_dump(mode="json")
            coverage = (result.structured or {}).get("coverage", {})
            payload["run_meta"] = dict(
                **revision, question=question, kb_backend=settings.kb_backend,
                rag_mode=settings.rag_mode, model=settings.llm_model,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                warm_up_ms=preheat_ms, started_at=started_at, is_mock=False,
                model_calls=result.meta.get("business_telemetry", {}).get("model_calls", 0),
                tool_calls=len(result.meta.get("tool_calls", [])),
                documents_found=coverage.get("documents_found"),
                documents_read=coverage.get("documents_read"),
                documents_summarized=coverage.get("documents_summarized"),
                claims=len(result.claims),
                quote_verification=result.meta.get("quote_verification"),
                number_verification=result.meta.get("number_verification"),
            )
            # Preserve the result schema; redact provider secrets even if a backend
            # unexpectedly places them inside a free-text error field.
            payload = redact_strings(payload, (settings.llm_api_key, settings.llm_base_url))
            write_json(RUNS / f"{index + 1}-{result.run_id}.json", payload)
            if index == 0 and sample_accepted(result, settings):
                write_json(SAMPLE, payload)
                sample_written = True
            all_completed = all_completed and result.status == "completed"
            print(json.dumps(dict(case=index + 1, status=result.status,
                                  evidence_status=result.evidence_status,
                                  error_code=result.error.code if result.error else None,
                                  **payload["run_meta"]), ensure_ascii=False))
        print("真实主样例已生成。" if sample_written else
              "主问题未满足真实多文献核验要求；未生成或覆盖 sample.json，请检查真实运行记录。")
        return 0 if sample_written and all_completed else 1
    finally:
        close_kb()
        if initialized_client:
            await get_client(settings).close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=None,
                        help="本地 .env 路径；进程环境变量优先于该文件")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(acceptance(args.env_file)))
