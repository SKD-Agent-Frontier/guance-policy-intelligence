"""命令行入口：样例构建、全量/恢复构建，以及独立批次校验。"""
import argparse
import json
from pathlib import Path
from policy_chunker.pipeline import build, report
from policy_chunker.tokens import TokenCounter
from policy_chunker.validation import validate_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    scope = make.add_mutually_exclusive_group(required=True)
    scope.add_argument("--sample", action="store_true")
    scope.add_argument("--all", action="store_true")
    make.add_argument("--resume", action="store_true")
    make.add_argument("--source", help="输入库根目录，默认只读 Agent标准引用库")
    make.add_argument("--run", help="独立输出批次目录")
    check = commands.add_parser("validate")
    check.add_argument("--run", required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parent
    if args.command == "build":
        return build(project, args)
    run = Path(args.run).resolve()
    profile = json.loads((run / "run_config.json").read_text(encoding="utf-8"))
    config = profile["config"]
    counter = TokenCounter(project, config)
    if counter.profile != profile["model_profile"]:
        raise ValueError("model profile differs from batch; restore the recorded tokenizer files")
    result = validate_run(run, Path(profile["source_root"]), counter, config)
    report(run, result, json.loads((run / "build_stats.json").read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
