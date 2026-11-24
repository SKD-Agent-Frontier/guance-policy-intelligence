"""Offline agents.Model and audit fakes for the unified business entry point."""

import json
import re

from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall


def _state_from_instructions(instructions):
    match = re.search(r"当前状态：(\{.*\})\s*$", instructions, re.S)
    return json.loads(match.group(1)) if match else {}


class BusinessSDKModel(Model):
    """Drive the real SDK tools with deterministic, protocol-valid calls."""

    def __init__(self):
        self.calls = 0
        self.history = []

    async def get_response(self, system_instructions, input, model_settings, tools,
                           output_schema, handoffs, tracing, **kwargs):
        self.calls += 1
        self.history.append((system_instructions, input))
        state = _state_from_instructions(system_instructions)
        question = state.get("original_question", "政策研究")
        required = state.get("required_capability")
        if required and required not in state.get("capabilities_used", []):
            call = self._tool("run_capability", {"capability": required, "requirements": [question]})
        else:
            facts = state.get("quote_checked_claims", [])
            root = next((req["id"] for req in state["requirements"] if req["label"] == question),
                        state["requirements"][0]["id"])
            facts_per_block = 10
            blocks = [{"id": f"offline-facts-{index}", "kind": "fact", "title": "核验事实", "text": "",
                       "requirement_ids": [root], "evidence_ids": [f["fact_handle"] for f in facts[index:index+facts_per_block]]}
                      for index in range(0, len(facts), facts_per_block)]
            proposals = state.get("business_analysis_candidates", [])
            blocks += [
                {"id": item["id"], "kind": "analysis", "title": item["title"], "text": item["text"],
                 "requirement_ids": [root], "evidence_ids": item["evidence_ids"]}
                for item in proposals
            ]
            call = self._tool("submit_draft", {"evidence_candidates": [], "blocks": blocks,
                                                "finish_if_partial": True})
        return ModelResponse(output=[call], usage=Usage(requests=1),
                             response_id=f"offline-business-{self.calls}")

    def _tool(self, name, arguments):
        return ResponseFunctionToolCall(
            type="function_call", name=name,
            call_id=f"offline-{self.calls}-{name}", arguments=json.dumps(arguments, ensure_ascii=False),
        )

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError("offline business fake does not stream")
        yield  # pragma: no cover

def install_business_sdk(monkeypatch):
    from guance_agent import orchestrator

    monkeypatch.setattr(orchestrator, "chat_model", lambda *_: BusinessSDKModel())
    build = orchestrator.build_research_core_agent

    def build_with_auditor(*args, **kwargs):
        async def audit(state, _mapped):
            return json.dumps({
                "passed": True,
                "covered_requirement_ids": [item.id for item in state.requirements],
                "approved_block_ids": [item.id for item in state.draft_blocks],
                "gaps": [], "unsupported_claims": [],
            }, ensure_ascii=False)
        return build(*args, **{**kwargs, "audit": audit})

    monkeypatch.setattr(orchestrator, "build_research_core_agent", build_with_auditor)
