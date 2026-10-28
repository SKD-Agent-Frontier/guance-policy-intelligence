"""真实学校模型 + 真实RAG比较验收；正式报告可入Git，配置和原始日志仅存本机。"""
import argparse
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from guance_agent.client import aclose_client
from guance_agent.config import load_settings
from guance_agent.kb import KBNoText, close_kb, get_kb, warm_up
from guance_agent.main import submit

REPO = Path(__file__).resolve().parents[2]


def audit_result(result, targets, *, scheme=False, missing=None):
    """核对对外契约、事实绑定和降级；不把字面检查声称为语义支持判断。"""
    failures = []
    if result.status != "completed" or not result.structured:
        return [result.error.code if result.error else "missing_structured_result"]
    data = result.structured
    if [obj["planid"] for obj in data.get("objects", [])] != targets:
        failures.append("object_identity_or_order")
    if any(not obj.get("title") for obj in data.get("objects", [])):
        failures.append("missing_document_title")
    rows = data.get("matrix", [])
    refs = {ref.chunk_id: ref for ref in result.evidence_refs}
    cells, complete = {}, 0
    for row in rows:
        if [cell["planid"] for cell in row["cells"]] != targets:
            failures.append("matrix_object_order")
        for cell in row["cells"]:
            cells[cell["cell_id"]] = cell
            if cell["status"] == "found":
                if not cell.get("text") or not cell.get("quotes") or not cell.get("chunk_ids"):
                    failures.append("found_cell_without_fact_payload")
                if cell.get("text") != "；".join(cell.get("quotes", [])):
                    failures.append("fact_cell_not_extractive")
                if not any(claim.dimension == row["dimension"] and claim.text == cell["text"]
                           and claim.chunk_ids == cell["chunk_ids"] and claim.quotes == cell["quotes"]
                           for claim in result.claims):
                    failures.append("cell_without_matching_claim")
                if any(cid not in refs or refs[cid].planid != cell["planid"]
                       for cid in cell["chunk_ids"]):
                    failures.append("cell_with_wrong_document_source")
            elif cell["status"] not in {"not_retrieved", "insufficient"}:
                failures.append("unsupported_absence_status")
        judgment = row["difference"]
        if judgment.get("kind") != "analysis":
            failures.append("difference_misrepresented_as_literal_fact")
        if judgment["status"] == "supported":
            if set(judgment["supporting_cells"]) != {cell["cell_id"] for cell in row["cells"]}:
                failures.append("difference_without_complete_basis")
            if any(cell["status"] != "found" for cell in row["cells"]):
                failures.append("difference_based_on_missing_fact")
            complete += 1
        if "comparable" not in row.get("comparability", {}):
            failures.append("missing_dimension_comparability")
    if len(rows) < 2 or (missing is None and complete < 2):
        failures.append("less_than_two_supported_dimensions")
    if missing is not None:
        if any(cell["status"] == "found" for cell in cells.values() if cell["planid"] == missing):
            failures.append("controlled_missing_source_still_found")
        if any(row["difference"]["status"] == "supported" for row in rows):
            failures.append("comparison_survived_missing_side")
    if scheme:
        analyses = data.get("scheme_analysis", [])
        if [item["planid"] for item in analyses] != targets:
            failures.append("missing_scheme_analysis")
        for item in analyses:
            for field in ("advantages", "limitations", "applicability"):
                if missing is None and not item[field]:
                    failures.append("missing_evidence_backed_" + field)
                for judgment in item[field]:
                    support = judgment.get("supporting_cells", [])
                    if not support or any(cid not in cells or cells[cid]["status"] != "found"
                                          for cid in support):
                        failures.append("scheme_analysis_without_facts")
                    elif not any(cells[cid]["planid"] == item["planid"] for cid in support):
                        failures.append("scheme_analysis_without_own_policy")
                    if judgment.get("kind") != "analysis":
                        failures.append("scheme_judgment_misrepresented_as_literal_fact")
    verification = result.meta.get("quote_verification", {})
    if verification.get("approved", 0) < 1 or verification.get("unverified", 0):
        failures.append("quotes_not_verified")
    return sorted(set(failures))


class MissingBody:
    """只用于受控缺证场景：保留真实目录、检索与核验，屏蔽指定对象正文读取。"""
    def __init__(self, kb, planid):
        self.kb, self.planid = kb, planid

    def __getattr__(self, name):
        return getattr(self.kb, name)

    def read(self, planid, section_idx=None, max_chars=6000, *, chunk_id=None, offset=0,
             document_id=None, generation_id=None):
        if planid == self.planid and (section_idx is not None or chunk_id is not None):
            raise KBNoText(planid, "受控缺证：已屏蔽该对象正文读取")
        return self.kb.read(planid, section_idx, max_chars, chunk_id=chunk_id, offset=offset,
                            document_id=document_id, generation_id=generation_id)


async def run(args):
    if not args.env_file.is_file():
        raise ValueError("missing_local_model_configuration")
    settings = load_settings(args.env_file)
    if not settings.llm_base_url or not settings.llm_api_key:
        raise ValueError("missing_school_model_configuration")
    # Settings会把相对会话路径基于agent目录解析，先固定到实际验收目录。
    args.output_dir = args.output_dir.resolve()
    args.private_dir = args.private_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.private_dir.mkdir(parents=True, exist_ok=True)
    settings = replace(settings, kb_backend="rag", rag_mode="bm25",
                       rag_store_project=str(args.store_project.resolve()),
                       session_path=args.private_dir / "sessions.sqlite",
                       run_log=args.private_dir / "runs.jsonl", log_content=False)
    cases = [
        ("policy", "compare", ["37982", "38366"], ["发展目标", "政策工具", "适用对象"]),
        ("schemes", "compare_plans", ["37982", "38366"], ["适用对象", "支持措施", "申请或执行条件"]),
        ("cross_period", "compare", ["57", "37982"], ["人才培养", "人才激励"]),
        ("controlled_missing", "compare", ["37982", "38366"], ["发展目标", "政策工具"]),
    ]
    report, samples = [], {}
    try:
        await asyncio.to_thread(warm_up, settings)
        real_kb = get_kb(settings, require_warm=True)
        for name, task_type, targets, dimensions in cases:
            masked = targets[1] if name == "controlled_missing" else None
            backend = MissingBody(real_kb, masked) if masked else real_kb
            with patch("guance_agent.orchestrator.get_kb", return_value=backend):
                result = await submit(
                    "按指定维度比较这些人才政策，说明口径限制；"
                    "分析政策方案有原文依据的优势、限制和适用条件，不给绝对排名。",
                    task_type=task_type, targets=targets, dimensions=dimensions, settings=settings)
            failures = audit_result(result, targets, scheme=task_type == "compare_plans", missing=masked)
            if name == "cross_period" and result.structured and result.structured.get("comparability", {}).get("comparable"):
                failures.append("cross_period_not_explained")
            payload = result.model_dump(mode="json")
            (args.output_dir / f"{name}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            entry = {"case": name, "passed": not failures, "failures": failures,
                     "targets": targets, "elapsed_ms": result.meta.get("elapsed_ms"),
                     "verification_scope": "real_model_real_rag_structural_and_literal",
                     "controlled_mask": "second_policy_body_reads" if masked else None}
            report.append(entry)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
            if name in {"policy", "schemes"} and not failures:
                samples[name] = payload
        passed = all(entry["passed"] for entry in report)
        (args.output_dir / "acceptance.json").write_text(json.dumps(
            {"passed": passed, "cases": report}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if passed and args.sample_output:
            args.sample_output.write_text(json.dumps(
                {"provenance": "学校现有模型与真实RAG的实际输出；语义支持仍须逐项审阅",
                 "results": samples}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if passed else 1
    finally:
        close_kb()
        await aclose_client()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--store-project", type=Path, default=REPO / "GitHub排除/比较分析运行库-verified")
    parser.add_argument("--output-dir", type=Path, default=REPO / "agent/reports/compare/latest",
                        help="可纳入Git的正式报告目录")
    parser.add_argument("--private-dir", type=Path, default=REPO / "GitHub排除/比较分析验收/runtime",
                        help="本地会话数据库和运行日志目录")
    parser.add_argument("--sample-output", type=Path)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        # 不输出异常正文，避免把私有模型地址或认证信息打印到控制台。
        print(json.dumps({"passed": False, "error_type": type(exc).__name__,
                          "message": "真实验收未完成，请检查本机配置、网络和排除目录中的运行记录。"},
                         ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
