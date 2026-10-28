"""Run one real BM25/model smoke request for each production task type."""

import argparse
import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

from live_compare_acceptance import audit_result
from organize_real_run import QUESTIONS, redact_strings, sample_accepted

from guance_agent.client import aclose_client
from guance_agent.config import ROOT, load_settings
from guance_agent.kb import close_kb, warm_up
from guance_agent.main import submit
from guance_agent.schemas import DIMENSIONS, RunResult

CASES = (
    ("qa", "qa", "依据37982号人才政策，概括人才培养、引进和激励措施，并引用原文。", ["37982"], []),
    ("extract", "extract", "依据37982号人才政策提取适用对象、政策工具、量化目标、责任主体、期限和资金规模。", ["37982"], []),
    ("organize", "organize", QUESTIONS[0], None, []),
    ("compare", "compare", "按指定维度比较这些人才政策，说明口径限制。", ["37982", "38366"], ["发展目标", "政策工具", "适用对象"]),
    ("compare_plans", "compare_plans", "按指定维度比较这些人才政策，分析方案有原文依据的优势、限制和适用条件，不给绝对排名。", ["37982", "38366"], ["适用对象", "支持措施", "申请或执行条件"]),
)
QA_FOCUSED = (
    "qa_focused", "qa", "依据37982号人才政策，对高层次人才创业团队有哪些支持？请仅引用一条原文作答。",
    ["37982"], [],
)


def _count(value):
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 0


def _claim_refs_valid(result):
    ref_ids = {ref.chunk_id for ref in result.evidence_refs}
    return all(claim.chunk_ids and set(claim.chunk_ids) <= ref_ids for claim in result.claims)


def _quote_counts(result):
    verification = result.meta.get("quote_verification", {})
    return {key: _count(verification.get(key, 0))
            for key in ("approved", "rejected", "unverified")}


def _route_targets(result):
    route = result.route or {}
    return route.get("targets", route.get("planids"))


def _case_checks(name, task_type, result, settings, targets, dimensions, json_compatible, refs_valid):
    quotes = _quote_counts(result)
    expected_targets = targets or None
    checks = {
        "run_result_json_roundtrip": json_compatible,
        "claims_reference_registered_evidence": refs_valid,
        "no_error": result.error is None,
        "task_type_matches": result.task_type == task_type,
        "route_targets_match": _route_targets(result) == expected_targets,
        "quotes_match_claims_and_are_verified": (
            quotes["approved"] == sum(len(claim.quotes) for claim in result.claims)
            and quotes["approved"] > 0 and quotes["rejected"] == 0 and quotes["unverified"] == 0
        ),
    }
    if task_type in {"qa", "extract"}:
        checks["completed_with_verified_claims"] = (
            result.status == "completed" and result.evidence_status == "verified"
            and bool(result.claims) and bool(result.evidence_refs)
        )
    if task_type == "qa":
        checks["qa_has_no_structured_payload"] = result.structured is None
    elif task_type == "extract":
        structured = result.structured or {}
        elements = [claim.model_dump() for claim in result.claims]
        checks["extract_structure_matches_claims"] = (
            all(key in structured for key in ("dimensions", "documents", "elements", "missing"))
            and structured.get("dimensions") == (dimensions or list(DIMENSIONS))
            and bool(structured.get("documents"))
            and bool(structured.get("elements"))
            and structured.get("elements") == elements
        )
    elif task_type == "organize":
        checks["organize_acceptance"] = sample_accepted(result, settings)
    else:
        failures = audit_result(result, targets, scheme=name == "compare_plans")
        checks["comparison_acceptance"] = not failures
    return checks, quotes


async def _run_case(case, settings, output_dir):
    name, task_type, question, targets, dimensions = case
    started = time.monotonic()
    result = None
    try:
        result = await submit(
            question=question,
            session_id=f"business-integration-smoke-{name}",
            task_type=task_type,
            targets=targets,
            dimensions=dimensions or None,
            settings=settings,
        )
        payload = redact_strings(
            result.model_dump(mode="json"), (settings.llm_api_key, settings.llm_base_url))
        (output_dir / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        roundtrip = RunResult.model_validate_json(result.model_dump_json())
        checks, quotes = _case_checks(
            name, task_type, result, settings, targets, dimensions,
            roundtrip == result, _claim_refs_valid(result))
        return {
            "case": name,
            "status": result.status,
            "evidence_status": result.evidence_status,
            "claims": len(result.claims),
            "evidence_refs": len(result.evidence_refs),
            "quote_verification": quotes,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "checks": checks,
            "passed": all(checks.values()),
        }
    except Exception as exc:
        if result is None:
            (output_dir / f"{name}.json").write_text(
                json.dumps({"case": name, "exception_type": type(exc).__name__}, indent=2) + "\n",
                encoding="utf-8")
        return {
            "case": name,
            "status": result.status if result else "exception",
            "evidence_status": result.evidence_status if result else "none",
            "claims": len(result.claims) if result else 0,
            "evidence_refs": len(result.evidence_refs) if result else 0,
            "quote_verification": _quote_counts(result) if result else {
                "approved": 0, "rejected": 0, "unverified": 0,
            },
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "checks": {"case_completed_without_exception": False},
            "passed": False,
        }


def _reaudit_first_five(output_dir, settings):
    cases = []
    for case in CASES:
        name, task_type, _, targets, dimensions = case
        path = output_dir / f"{name}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload = redact_strings(payload, (settings.llm_api_key, settings.llm_base_url))
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = RunResult.model_validate(payload)
        json_compatible = RunResult.model_validate_json(result.model_dump_json()) == result
        checks, quotes = _case_checks(
            name, task_type, result, settings, targets, dimensions,
            json_compatible, _claim_refs_valid(result))
        entry = {
            "case": name,
            "status": result.status,
            "evidence_status": result.evidence_status,
            "claims": len(result.claims),
            "evidence_refs": len(result.evidence_refs),
            "quote_verification": quotes,
            "elapsed_ms": result.meta.get("elapsed_ms"),
            "checks": checks,
            "passed": all(checks.values()),
        }
        cases.append(entry)
    return {"passed": all(case["passed"] for case in cases), "cases": cases}


def audit_saved_results(env_file, output_dir):
    settings = load_settings(env_file)
    output_dir = output_dir.resolve()
    first_five = _reaudit_first_five(output_dir, settings)
    focused_path = output_dir / "qa_focused.json"
    focused_payload = redact_strings(
        json.loads(focused_path.read_text(encoding="utf-8")),
        (settings.llm_api_key, settings.llm_base_url))
    focused_path.write_text(
        json.dumps(focused_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    focused = RunResult.model_validate(focused_payload)
    checks, quotes = _case_checks(
        "qa_focused", "qa", focused, settings, QA_FOCUSED[3], QA_FOCUSED[4],
        RunResult.model_validate_json(focused.model_dump_json()) == focused,
        _claim_refs_valid(focused))
    diagnostic = {
        "case": "qa_focused",
        "status": focused.status,
        "evidence_status": focused.evidence_status,
        "claims": len(focused.claims),
        "evidence_refs": len(focused.evidence_refs),
        "quote_verification": quotes,
        "elapsed_ms": focused.meta.get("elapsed_ms"),
        "checks": checks,
        "passed": all(checks.values()),
    }
    report = {
        "first_five": first_five,
        "qa_focused_diagnostic": diagnostic,
        "passed": first_five["passed"] and diagnostic["passed"],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


async def run(env_file, output_dir, diagnostic_only=False):
    settings = load_settings(env_file)
    if not settings.llm_base_url or not settings.llm_api_key:
        raise ValueError("missing_model_configuration")
    if settings.kb_backend != "rag" or settings.rag_mode != "bm25" or not settings.rag_store_project:
        raise ValueError("invalid_rag_configuration")
    if not Path(settings.rag_store_project).is_dir():
        raise ValueError("missing_rag_store_project")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = replace(
        settings,
        session_path=output_dir / "sessions.sqlite",
        run_log=output_dir / "runs.jsonl",
        log_content=False,
    )
    report_path = output_dir / "summary.json"
    first_five = None
    if diagnostic_only:
        existing = json.loads(report_path.read_text(encoding="utf-8"))
        first_five = existing.get("first_five")
        if first_five is None:
            first_five = _reaudit_first_five(output_dir, settings)
    else:
        first_five = None
    try:
        await asyncio.to_thread(warm_up, settings)
        if not diagnostic_only:
            first_five = {"passed": False, "cases": []}
            for case in CASES:
                entry = await _run_case(case, settings, output_dir)
                first_five["cases"].append(entry)
                print(json.dumps(entry, ensure_ascii=False), flush=True)
            first_five["passed"] = all(entry["passed"] for entry in first_five["cases"])

        diagnostic = await _run_case(QA_FOCUSED, settings, output_dir)
        report = {"first_five": first_five, "qa_focused_diagnostic": diagnostic}
        report["passed"] = first_five["passed"] and diagnostic["passed"]
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"qa_focused_diagnostic": diagnostic}, ensure_ascii=False), flush=True)
        return 0 if report["passed"] else 1
    finally:
        close_kb()
        await aclose_client()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/business_integration_smoke")
    parser.add_argument("--diagnostic-only", action="store_true",
                        help="reuse the saved first five results and run only the focused QA diagnostic")
    parser.add_argument("--audit-only", action="store_true",
                        help="recheck saved results without warming RAG or calling the model")
    args = parser.parse_args()
    try:
        if args.audit_only:
            report = audit_saved_results(args.env_file, args.output_dir)
            print(json.dumps(report, ensure_ascii=False), flush=True)
            raise SystemExit(0 if report["passed"] else 1)
        raise SystemExit(asyncio.run(run(args.env_file, args.output_dir, args.diagnostic_only)))
    except Exception as exc:
        print(json.dumps({"passed": False, "error_type": type(exc).__name__,
                          "message": "Smoke run configuration is unavailable."}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
