"""Run the frozen mock suite and emit JSONL + summary (no implicit secrets logging)."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=190)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true",
                        help="D variant only: enable all skill/MCP capabilities")
    args = parser.parse_args()
    if args.limit < 1 or args.timeout < 1:
        parser.error("limit and timeout must be positive")
    suite = json.loads((ROOT / "questions.json").read_text(encoding="utf-8"))[:args.limit]
    out_dir = ROOT / "results"
    if not args.dry_run:
        out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = []
    for case in suite:
        command = [sys.executable,str(ROOT / "run.py"),"--question",case["question"]]
        if args.all:
            command.append("--all")
        if args.dry_run:
            print(json.dumps({"id":case["id"],"command":command},ensure_ascii=False))
            continue
        try:
            proc = subprocess.run(command,capture_output=True,text=True,
                                  encoding="utf-8",timeout=args.timeout,
                                  cwd=ROOT.parent,check=False)
            raw = proc.stdout.strip().splitlines()
            record = json.loads(raw[-1]) if raw else {"status":"no_stdout"}
            if not isinstance(record, dict):
                record = {"status":"invalid_stdout"}
            record["exit_code"] = proc.returncode
            if proc.returncode and "error_type" not in record:
                record["error_type"] = "process_exit_nonzero"
        except subprocess.TimeoutExpired:
            record = {"status":"timeout","error_type":"harness_timeout"}
        except (ValueError,TypeError,UnicodeError):
            record = {"status":"invalid_stdout","error_type":"parse_error"}
        record["case_id"] = case["id"]
        expected = set(case["expected_planids"])
        observed = set(record.get("evidence_planids") or [])
        record["expected_source_recall"] = (
            expected.issubset(observed) if expected else None)
        rows.append(record)
        print(json.dumps({"case_id":case["id"],"status":record.get("status"),
                          "elapsed_ms":record.get("elapsed_ms"),
                          "source_recall":record["expected_source_recall"]},
                         ensure_ascii=False))
    if args.dry_run:
        return
    output = out_dir / (stamp + ".jsonl")
    output.write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in rows),
                      encoding="utf-8")
    summary = {
        "cases":len(rows),"statuses":dict(Counter(x.get("status") for x in rows)),
        "source_recall_passes":sum(x.get("expected_source_recall") is True for x in rows),
        "source_recall_checked":sum(x.get("expected_source_recall") is not None for x in rows),
        "nonempty_answers":sum(bool(str(x.get("answer") or "").strip()) for x in rows),
        "total_elapsed_ms":sum(x.get("elapsed_ms") or 0 for x in rows),
        "answer_correctness":"not automatically evaluated",
        "results_file":str(output),
    }
    (out_dir / (stamp + ".summary.json")).write_text(
        json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

if __name__ == "__main__":
    main()
