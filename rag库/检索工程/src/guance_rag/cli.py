"""命令行和常驻JSONL入口；不开放HTTP端口、不输出私密配置。"""
import argparse
import json
from pathlib import Path
import sys

from .config import DEFAULT_STORE, RuntimeOptions
from .errors import RetrievalError
from .runtime import RagKnowledgeBase


def serializable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [serializable(v) for v in value]
    return value


def respond(kb, request):
    if not isinstance(request, dict):
        return {"status": "invalid_argument", "message": "请求须为JSON对象", "data": None}
    operation = request.get("operation")
    if operation not in {"search", "read", "read_image", "get_chunk_source", "verify_quote", "verify_media_quote", "stats"}:
        return {"status": "invalid_argument", "message": "不支持的只读操作", "data": None}
    try:
        arguments = request.get("arguments", {})
        if not isinstance(arguments, dict):
            raise TypeError
        value = getattr(kb, operation)(**arguments)
        return {"status": "empty" if operation == "search" and not value else "ok", "message": "", "data": serializable(value)}
    except RetrievalError as exc:
        return {"status": exc.status, "message": str(exc), "data": None}
    except TypeError:
        return {"status": "invalid_argument", "message": "参数与操作签名不匹配", "data": None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-project", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--mode", choices=["bm25", "dense", "hybrid"], default="hybrid")
    commands = parser.add_subparsers(dest="command", required=True)
    query = commands.add_parser("search")
    query.add_argument("query")
    query.add_argument("--area")
    query.add_argument("--fyp")
    query.add_argument("--top-k", type=int, default=8)
    query.add_argument("--source-types", nargs="+", choices=["text_chunk", "visual_chunk", "image_occurrence"])
    read = commands.add_parser("read")
    read.add_argument("planid")
    read.add_argument("--section-idx", type=int)
    read.add_argument("--chunk-id")
    read.add_argument("--source-id")
    read.add_argument("--offset", type=int, default=0)
    read.add_argument("--max-chars", type=int, default=6000)
    read.add_argument("--document-id")
    read.add_argument("--generation-id")
    picture = commands.add_parser("read_image", help="按媒体来源ID返回经哈希核验的图片data URL")
    picture.add_argument("source_id")
    picture.add_argument("--variant", choices=["original", "region"], default="original")
    picture.add_argument("--document-id")
    picture.add_argument("--generation-id")
    commands.add_parser("stats")
    commands.add_parser("serve", help="一次启动加载，逐行读取JSON请求并输出JSON结果")
    args = parser.parse_args(argv)
    try:
        with RagKnowledgeBase(RuntimeOptions(store_project=args.store_project, mode=args.mode)) as kb:
            if args.command == "serve":
                print(json.dumps({"status": "ready", "data": kb.stats()}, ensure_ascii=False), flush=True)
                for line in sys.stdin:
                    try:
                        request = json.loads(line)
                        result = respond(kb, request)
                    except ValueError:
                        result = {"status": "invalid_argument", "message": "请求不是有效JSON", "data": None}
                    print(json.dumps(result, ensure_ascii=False), flush=True)
            else:
                arguments = vars(args).copy()
                for key in ("command", "store_project", "mode"):
                    arguments.pop(key)
                print(json.dumps(respond(kb, {"operation": args.command, "arguments": arguments}), ensure_ascii=False, indent=2))
    except RetrievalError as exc:
        print(json.dumps({"status": exc.status, "message": str(exc), "data": None}, ensure_ascii=False))
        return 1
    return 0
