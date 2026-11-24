"""脚本化假模型，仅用于离线测试，不代表真实模型能力。"""
import asyncio
import copy
import json

from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)


def tool(name, **arguments):
    return ResponseFunctionToolCall(type="function_call", name=name,
                                    call_id="call_" + name, arguments=json.dumps(arguments))


def answer(text="你好", claims=None, insufficient=False):
    return json.dumps(dict(answer=text, claims=claims or [], insufficient=insufficient), ensure_ascii=False)


class FakeModel(Model):
    def __init__(self, script, delay=0):
        self.script, self.delay, self.inputs, self.calls = script, delay, [], 0

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema,
                           handoffs, tracing, **kwargs):
        self.inputs.append(copy.deepcopy(input))
        self.calls += 1
        await asyncio.sleep(self.delay)
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if callable(step):
            step = step(system_instructions)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, str):
            output = [ResponseOutputMessage(id=f"msg_{self.calls}", type="message", role="assistant",
                        status="completed", content=[ResponseOutputText(type="output_text", text=step, annotations=[])])]
        else:
            output = [item.model_copy(update={"call_id": f"call_{self.calls}_{i}"})
                      for i, item in enumerate(step)]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=f"fake_{self.calls}")

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError("offline fake does not stream")
        yield  # pragma: no cover


def sdk_search(query="农业"):
    return tool("search", query=query, top_k=8, requirements=[query])


def sdk_read(hit):
    return tool("read_batch", items=[{"planid": hit.planid, "chunk_id": hit.chunk_id}])


def fact_submission(hit, *, text=None, quote=None):
    """Link a fact to the actual requirements frozen during this SDK invocation."""
    from .business_sdk_fakes import _state_from_instructions

    def step(instructions):
        state = _state_from_instructions(instructions)
        requirements = [req["id"] for req in state["requirements"]]
        source_text = quote or hit.content[:24].strip()
        return [tool("submit_draft", evidence_candidates=[{
            "candidate_id": "source", "chunk_id": hit.chunk_id, "quote": source_text,
            "claim": text or source_text, "requirement_ids": requirements,
        }], blocks=[{"id": "fact", "kind": "fact", "title": "政策原文", "text": "",
                     "requirement_ids": requirements, "evidence_ids": ["source"]}],
                     finish_if_partial=True)]
    return step


def install_audit(monkeypatch, *, approve=True):
    from guance_agent.runtime import Runtime

    requests = []

    async def review(self, messages, *, max_tokens, timeout, **kwargs):
        payload = json.loads(messages[1]["content"])
        requests.append((payload, max_tokens, timeout))
        if isinstance(approve, Exception):
            raise approve
        return json.dumps({
            "passed": approve,
            "covered_requirement_ids": [r["id"] for r in payload["frozen_requirements"]] if approve else [],
            "approved_block_ids": [b["id"] for b in payload["blocks"]] if approve else [],
            "gaps": [], "unsupported_claims": [],
        })
    monkeypatch.setattr(Runtime, "complete_text", review)
    return requests


def seed_read_page(state, chunk_id, text):
    """Seed the same bounded passage snapshot as the SDK reader."""
    from guance_agent.research_core import _cache_passage
    from guance_agent.schemas import ChunkSpan, Passage

    ref = state.run.evidence.get(chunk_id)
    span = ChunkSpan(chunk_id=chunk_id, char_start=ref.char_start,
                     char_end=ref.char_end, complete=ref.complete)
    passage = Passage(**ref.model_dump(), content=text, chunks=[span])
    _cache_passage(state, passage)


def versioned_kb(path, generation="generation-test"):
    from guance_agent.kb import MockKB

    identity = {"document_id": "doc-test", "source_hash": "source-hash-test",
                "generation_id": generation}

    class VersionedMockKB(MockKB):
        def search(self, *args, **kwargs):
            return [hit.model_copy(update=identity) for hit in super().search(*args, **kwargs)]

        def read(self, *args, **kwargs):
            return super().read(*args, **kwargs).model_copy(update=identity)

    return VersionedMockKB(path)
