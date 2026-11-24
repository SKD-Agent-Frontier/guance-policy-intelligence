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
        if self.calls == 1:
            call = self._tool("search", {
                "query": question[:200], "top_k": 12,
                "requirements": [question],
            })
        elif self.calls == 2 and required:
            call = self._tool("run_capability", {"capability": required})
        else:
            claims = state.get("quote_checked_claims", [])
            requirements = [item["id"] for item in state.get("requirements", [])]
            candidates = [
                {"candidate_id": f"offline-{index}", "claim": item["text"],
                 "evidence_handle": item["fact_handle"],
                 "requirement_ids": requirements}
                for index, item in enumerate(claims)
            ]
            block_ids = [item["candidate_id"] for item in candidates]
            block = [{"id": "offline-result", "title": "核验事实", "kind": "fact",
                      "text": "", "requirement_ids": requirements,
                      "evidence_ids": block_ids}]
            call = self._tool("submit_draft", {
                "evidence_candidates": candidates, "blocks": block,
                "finish_if_partial": True,
            })
        return ModelResponse(output=[call], usage=Usage(requests=1),
                             response_id=f"offline-business-{self.calls}")

    @staticmethod
    def _tool(name, arguments):
        return ResponseFunctionToolCall(
            type="function_call", name=name,
            call_id=f"offline-{name}", arguments=json.dumps(arguments, ensure_ascii=False),
        )

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError("offline business fake does not stream")
        yield  # pragma: no cover
