"""Opt-in real-provider QA acceptance; credentials remain in local configuration."""
import argparse
import asyncio
import hashlib
import json
import sys
import time
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(args):
    source = args.source.resolve()
    sys.path[:0] = [str(source / "agent/src"), str(source / "web/backend")]
    import httpx
    from app.main import create_app
    from app.policy_catalog import PolicyCatalogService, load_published_catalog

    from guance_agent.client import aclose_client, get_client
    from guance_agent.config import load_settings

    settings = load_settings(args.env_file)
    catalog_project = settings.rag_store_project
    settings = replace(settings, rag_store_project=str(args.store_project.resolve()),
                       session_path=args.output / "private/sessions.sqlite",
                       run_log=args.output / "private/runs.jsonl")
    if settings.kb_backend != "rag":
        raise ValueError("Acceptance requires the real RAG backend")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    cases_hash = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    code_files = [*sorted((source / "agent/src").rglob("*.py")),
                  *sorted((source / "web/backend/app").rglob("*.py"))]
    code_hash = hashlib.sha256("".join(
        str(path.relative_to(source)) + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in code_files).encode()).hexdigest()
    config_path = args.output / "configuration.json"
    if config_path.exists() and (args.output / "results").exists():
        previous = json.loads(config_path.read_text(encoding="utf-8"))
        if previous.get("code_sha256") != code_hash:
            raise ValueError("Acceptance output belongs to another code snapshot; choose a new output directory")
        if (previous.get("cases_sha256") != cases_hash
                or previous.get("concurrency") != args.concurrency):
            raise ValueError("Acceptance cases or concurrency changed; choose a new output directory")
    write(config_path, {
        "source": str(source), "model": settings.llm_model, "rag_mode": settings.rag_mode,
        "code_sha256": code_hash,
        "cases_sha256": cases_hash, "concurrency": args.concurrency,
        "temperature": settings.llm_temperature, "max_output_tokens": settings.llm_max_output_tokens,
        "run_timeout": settings.run_timeout, "repeats": args.repeats,
        "note": "Real model and frozen corpus; independent sessions; no production settings changed",
    })
    active_case = ContextVar("acceptance_case", default=None)
    provider = get_client(settings)
    original_create = provider.chat.completions.create

    async def captured_create(**kwargs):
        started = time.monotonic()
        response = await original_create(**kwargs)
        active = active_case.get()
        if active is None:
            raise RuntimeError("Model call outside an acceptance case")
        active.setdefault("model_calls", []).append({
            "input": {key: value for key, value in kwargs.items()
                      if key in {"messages", "model", "temperature", "max_tokens", "extra_body"}},
            "output": response.model_dump(mode="json"),
            "elapsed_seconds": time.monotonic() - started,
        })
        return response

    provider.chat.completions.create = captured_create
    app = create_app(settings=settings)
    try:
        async with app.router.lifespan_context(app):
            if not app.state.ready:
                raise RuntimeError("Real RAG warm-up failed")
            app.state.policy_catalog = PolicyCatalogService(
                await asyncio.to_thread(load_published_catalog, catalog_project))
            write(args.output / "dataset.json", app.state.policy_catalog.dataset())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://acceptance") as client:
                semaphore = asyncio.Semaphore(args.concurrency)

                async def run_case(case, repeat):
                    async with semaphore:
                        path = args.output / "results" / f"{case['id']}-{repeat}.json"
                        if path.exists():
                            return
                        active = {"case": case, "repeat": repeat}
                        token = active_case.set(active)
                        payload = {"question": case["question"], "task_type": "qa"}
                        if case.get("targets"):
                            payload["targets"] = case["targets"]
                        start = time.monotonic()
                        try:
                            if case.get("seed"):
                                response = await client.post("/api/runs", json={
                                    "question": case["seed"], "task_type": "qa"})
                                active["seed"] = response.json()
                                payload["session_id"] = active["seed"]["session_id"]
                            response = await client.post("/api/runs", json=payload)
                            active.update(request=payload, result=response.json(), http_status=response.status_code)
                        except Exception as error:
                            active["exception_type"] = type(error).__name__
                        finally:
                            active_case.reset(token)
                        active["elapsed_seconds"] = round(time.monotonic() - start, 3)
                        write(path, active)
                        result = active.get("result", {})
                        print(json.dumps({"case": case["id"], "repeat": repeat,
                            "seconds": active["elapsed_seconds"], "status": result.get("status"),
                            "evidence": result.get("evidence_status"),
                            "claims": len(result.get("claims", [])),
                            "error": active.get("exception_type")}, ensure_ascii=False), flush=True)
                for repeat in range(1, args.repeats + 1):
                    await asyncio.gather(*(run_case(case, repeat) for case in cases))
    finally:
        await aclose_client()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--store-project", type=Path, required=True,
                        help="A verified independent snapshot, not an actively locked production store")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--concurrency", type=int, default=1, choices=range(1, 6),
                        help="Use 1 for comparable per-case latency; higher values measure in-process load")
    args = parser.parse_args()
    args.output = args.output.resolve()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
