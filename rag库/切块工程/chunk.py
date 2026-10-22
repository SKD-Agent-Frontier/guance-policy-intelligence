"""命令行入口：样例构建、全量/恢复构建，以及独立批次校验。"""
# 建议阅读顺序：本文件 main -> pipeline.build -> pipeline.process -> 各专项模块。
# build 会生成批次并校验；validate 直接读取已有批次，不重新划分父子块。
# 两种命令最终都以退出码表达程序校验结果：0 表示通过，1 表示未通过。
import argparse
import json
from pathlib import Path
from policy_chunker.pipeline import build, report
from policy_chunker.tokens import TokenCounter
from policy_chunker.validation import validate_run


def main():
    # argparse 将命令行文本转换成 args；这里只做命令路由，不实现切块规则。
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    scope = make.add_mutually_exclusive_group(required=True)
    # --sample 与 --all 必须且只能选一个，避免“不知道到底处理了哪些文件”。
    scope.add_argument("--sample", action="store_true")
    scope.add_argument("--all", action="store_true")
    make.add_argument("--resume", action="store_true")
    make.add_argument("--source", help="输入库根目录，默认只读 Agent标准引用库")
    make.add_argument("--run", help="独立输出批次目录")
    check = commands.add_parser("validate")
    check.add_argument("--run", required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parent
    # 根据脚本位置定位工程根，不依赖用户从哪个工作目录启动命令。
    if args.command == "build":
        return build(project, args)
    run = Path(args.run).resolve()
    # 校验旧批次时使用其冻结配置，而不是悄悄套用工程当前 config.json 的新参数。
    profile = json.loads((run / "run_config.json").read_text(encoding="utf-8"))
    config = profile["config"]
    counter = TokenCounter(project, config)
    if counter.profile != profile["model_profile"]:
        # 更换 tokenizer 后同一句话的 token 数可能不同，不能用不同配置验收原批次。
        raise ValueError("model profile differs from batch; restore the recorded tokenizer files")
    result = validate_run(run, Path(profile["source_root"]), counter, config)
    # 校验会重新读取持久化数据，并更新派生报告/可用清单；政策原文只读。
    report(run, result, json.loads((run / "build_stats.json").read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    # 作为脚本执行才启动；被测试代码 import 时不会自动运行一次构建。
    raise SystemExit(main())
