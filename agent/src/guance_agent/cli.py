"""命令行参数、交互与输出。"""
import argparse
import asyncio
import runpy
import sys

from .config import ROOT
from .main import submit
from .output import to_markdown


def exit_code(result):
    if result.error and result.error.code == "INVALID_INPUT":
        return 2
    return {"completed": 0, "failed": 1, "limit_reached": 3}[result.status]


async def _interact(args):
    session, code = args.session, 0
    while True:
        try:
            question = args.question if args.command == "ask" else input("你：")
        except EOFError:
            break
        if args.command == "chat" and not question.strip():
            break
        result = await submit(question, session_id=session)
        session, code = result.session_id, exit_code(result)
        print(result.model_dump_json() if getattr(args, "json", False) else to_markdown(result))
        print(f"session_id={session}", file=sys.stderr)
        if args.command == "ask":
            break
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description="管策智研政策原文检索助手")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="提交一个问题")
    ask.add_argument("question")
    ask.add_argument("--session")
    ask.add_argument("--json", action="store_true")
    chat = commands.add_parser("chat", help="多轮对话，空行退出")
    chat.add_argument("--session")
    commands.add_parser("probe", help="实测模型接口")
    args = parser.parse_args(argv)
    if args.command == "probe":
        runpy.run_path(str(ROOT / "scripts/probe_model.py"), run_name="__main__")
        return 0
    try:
        return asyncio.run(_interact(args))
    except KeyboardInterrupt:
        return 1
