"""E: GPT Researcher as a separate local-documents research engine over frozen RAG hits."""
import argparse
import asyncio
import json
import os
import time

from guance_agent.config import load_settings
from guance_agent.kb import close_kb, warm_up
from common import collect_passages

def configure_model(settings):
    if not settings.llm_base_url or not settings.llm_api_key:
        raise ValueError("E 实验需要本地 LLM_BASE_URL / LLM_API_KEY")
    # All roles use the same local Qwen; do not fall back to OpenAI defaults.
    os.environ["OPENAI_API_KEY"] = settings.llm_api_key
    os.environ["OPENAI_BASE_URL"] = settings.llm_base_url
    os.environ["OPENAI_API_BASE"] = settings.llm_base_url
    for key in ("FAST_LLM", "SMART_LLM", "STRATEGIC_LLM"):
        os.environ[key] = "openai:" + settings.llm_model

async def execute(question: str, *, prepare_only=False):
    settings = load_settings()
    started = time.monotonic()
    try:
        await asyncio.to_thread(warm_up,settings)
        evidence, retrieval = await asyncio.to_thread(collect_passages,question,settings)
        output = {"variant":"E","question":question,
                  "evidence_planids":sorted({r["planid"] for r in evidence}),
                  "evidence_status":"unreviewed","metrics":retrieval}
        if prepare_only:
            output.update(status="prepared",answer="",document_count=len(evidence),
                          elapsed_ms=round((time.monotonic()-started)*1000))
            return output
        if not evidence:
            output.update(status="insufficient",answer="未能读取政策原文。",
                          elapsed_ms=round((time.monotonic()-started)*1000))
            return output
        configure_model(settings)
        from gpt_researcher import GPTResearcher
        from langchain_core.documents import Document

        documents = [
            Document(
                page_content=("来源 "+x["id"]+"：《"+x["title"]+"》；"
                              "planid="+x["planid"]+"；章节="+x["section"]+
                              "；chunk_id="+x["chunk_id"]+"\n"+x["content"]),
                metadata={"source":x["chunk_id"],"planid":x["planid"],
                          "title":x["title"],"section":x["section"]})
            for x in evidence
        ]
        # B-E share identical retrieved inputs; GPT Researcher performs its own
        # research/report calls (not comparable to a single QA model completion).
        researcher = GPTResearcher(
            query=question, report_type="research_report",
            report_source="langchain_documents", documents=documents,
            verbose=False)
        async def research():
            await researcher.conduct_research()
            return await researcher.write_report()
        report = await asyncio.wait_for(research(),timeout=settings.run_timeout)
        output.update(status="completed" if report and report.strip() else "empty_answer",
                      answer=str(report or ""),
                      document_count=len(documents),
                      elapsed_ms=round((time.monotonic()-started)*1000))
        return output
    finally:
        close_kb()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--question",required=True)
    parser.add_argument("--prepare-only",action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(execute(args.question,
                                             prepare_only=args.prepare_only)),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"variant":"E","status":"failed","error_type":type(exc).__name__},
                         ensure_ascii=False))
        raise SystemExit(1)
