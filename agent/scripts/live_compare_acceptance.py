"""真实学校模型 + 真实 RAG 的受控比较验收入口。

复用生产 submit 路由、业务模块和核验流程，覆盖政策对比、多方案、跨期与单侧缺证。
正式报告写 output-dir，可纳入 Git；模型凭据从本机 env-file 读取，SQLite 和运行日志
另写 private-dir。脚本不把原始提示词或模型草稿加入正式报告，也不重建语料索引。
"""
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
    """返回失败标识列表；空列表表示该场景的结构与字面核验检查通过。

    正常场景至少两个维度有完整的双方事实/差异，方案场景还检查三类判断的自身依据。
    missing 指定人为屏蔽的政策，只检查其正确降级，不要求生成双方差异。
    这些断言验证的是契约、来源归属和依赖关系，不能代替政策分析的逐项语义审阅。
    """
    failures = []
    if result.status != "completed" or not result.structured:
        return [result.error.code if result.error else "missing_structured_result"]
    data = result.structured
    # 显式对象的身份与顺序必须贯穿路由、矩阵和方案输出，不能悄悄换成搜索前几名。
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
                # 不仅检查“有文字”，还要求矩阵单元格与进入基础层的 Claim 完全一致。
                # 这样能发现表格仍残留已失效内容、正文/引文分离或跨对象引用的问题。
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
        # 这是受控故障注入，不是声称现实政策没有该项条款。乙侧缺证后所有双方
        # 差异都应撤回，但甲侧可继续保留本轮已核验的原文事实。
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
    # 此处依赖生产基础层实际执行的核验遥测，不能用离线 mock 的结果冒充真实核验。
    if verification.get("approved", 0) < 1 or verification.get("unverified", 0):
        failures.append("quotes_not_verified")
    return sorted(set(failures))


class MissingBody:
    """真实 KB 的测试包装，仅让指定对象的正文读取抛出 KBNoText。

    目录仍返回真实身份；search、verify_quote 等由 __getattr__ 透传给真实后端。
    因而缺证场景只替换一个故障条件，其余证据链仍走生产流程，不是合成政策测试。
    """
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
    """预热一份真实运行库，顺序执行场景，保存每次实际 RunResult 与汇总状态。"""
    if not args.env_file.is_file():
        raise ValueError("missing_local_model_configuration")
    settings = load_settings(args.env_file)
    if not settings.llm_base_url or not settings.llm_api_key:
        raise ValueError("missing_school_model_configuration")
    # Settings 会把相对状态路径基于 agent/ 解析。先转绝对路径，并把可提交报告与
    # 私有状态分开，防止仅改变 output-dir 时把 SQLite/内容日志一起带入报告目录。
    args.output_dir = args.output_dir.resolve()
    args.private_dir = args.private_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.private_dir.mkdir(parents=True, exist_ok=True)
    settings = replace(settings, kb_backend="rag", rag_mode="bm25",
                       rag_store_project=str(args.store_project.resolve()),
                       session_path=args.private_dir / "sessions.sqlite",
                       run_log=args.private_dir / "runs.jsonl", log_content=False)
    # 这些是固定语料中的已知真实文献。跨期场景同时涉及不同文件类型，检查的是
    # 带口径限制的条款对照，不把国家总体规划与区级专项规划视为同层面数值对比。
    cases = [
        ("policy", "compare", ["37982", "38366"], ["发展目标", "政策工具", "适用对象"]),
        ("schemes", "compare_plans", ["37982", "38366"], ["适用对象", "支持措施", "申请或执行条件"]),
        ("cross_period", "compare", ["57", "37982"], ["人才培养", "人才激励"]),
        ("controlled_missing", "compare", ["37982", "38366"], ["发展目标", "政策工具"]),
    ]
    report, samples = [], {}
    try:
        # 同步 RAG 预热放在线程中，并在 submit 前完成；启动成本不混入各场景耗时。
        await asyncio.to_thread(warm_up, settings)
        real_kb = get_kb(settings, require_warm=True)
        for name, task_type, targets, dimensions in cases:
            masked = targets[1] if name == "controlled_missing" else None
            backend = MissingBody(real_kb, masked) if masked else real_kb
            # 只替换该场景的后端获取入口。业务、工具证据登记和基础层核验仍是生产实现。
            with patch("guance_agent.orchestrator.get_kb", return_value=backend):
                result = await submit(
                    "按指定维度比较这些人才政策，说明口径限制；"
                    "分析政策方案有原文依据的优势、限制和适用条件，不给绝对排名。",
                    task_type=task_type, targets=targets, dimensions=dimensions, settings=settings)
            failures = audit_result(result, targets, scheme=task_type == "compare_plans", missing=masked)
            if name == "cross_period" and result.structured and result.structured.get("comparability", {}).get("comparable"):
                failures.append("cross_period_not_explained")
            payload = result.model_dump(mode="json")
            # 无论通过或失败都保留完整场景结果，避免汇总只呈现成功的试运行。
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
            # 仅在这一次运行的全部场景通过后更新样例；不拼凑多次运行中的成功案例。
            args.sample_output.write_text(json.dumps(
                {"provenance": "学校现有模型与真实RAG的实际输出；语义支持仍须逐项审阅",
                 "results": samples}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if passed else 1
    finally:
        # 脚本负责进程级资源的生命周期；业务模块自身不能关闭共享客户端或运行库。
        close_kb()
        await aclose_client()


def main():
    """解析本机配置与输出位置，命令行仅打印脱敏状态；零表示本次全部场景通过。"""
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
