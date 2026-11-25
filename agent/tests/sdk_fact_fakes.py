"""Drive a single MockKB fact through the production SDK and final verifier."""
import json

import guance_agent.orchestrator as orchestrator
from guance_agent.kb import MockKB

from .business_sdk_fakes import _state_from_instructions
from .fake_model import FakeModel, tool

FACT = "完善农业灌溉设施"
SOURCE = "TEST-001__s01__p00"


def install_fact_model(monkeypatch, settings, *, borrowed_quote=False):
    kb = MockKB(settings.mock_kb_path)

    def verify(chunk_id, quote, observed_ranges, **_identity):
        for doc in kb.docs:
            for chunk in doc["chunks"]:
                if chunk["chunk_id"] == chunk_id:
                    return any(chunk["char_start"] <= start < end <= chunk["char_end"]
                               and quote in doc["body"][start:end] for start, end in observed_ranges)
        return False

    monkeypatch.setattr(kb, "verify_quote", verify, raising=False)
    monkeypatch.setattr(orchestrator, "get_kb", lambda _settings: kb)

    class FactModel(FakeModel):
        async def get_response(self, system_instructions, input, *args, **kwargs):
            state = _state_from_instructions(system_instructions)
            ids = [item["id"] for item in state["requirements"]]
            if self.calls == 0:
                call = tool("search", query="农业", requirements=[FACT], top_k=8)
            elif self.calls == 1:
                items = [{"planid": "TEST-001", "chunk_id": SOURCE}]
                if borrowed_quote:
                    items.append({"planid": "TEST-002", "chunk_id": "TEST-002__s00__p00"})
                call = tool("read_batch", items=items)
            elif self.calls == 2 and borrowed_quote:
                call = tool("record_evidence_batch", items=[{
                    "candidate_id": "wrong", "claim": FACT, "quote": FACT,
                    "chunk_id": "TEST-002__s00__p00", "requirement_ids": ids,
                }])
            else:
                call = tool("submit_draft", evidence_candidates=[{
                    "candidate_id": "right", "claim": FACT, "quote": FACT, "chunk_id": SOURCE,
                    "requirement_ids": ids,
                }], blocks=[{
                    "id": "verified_fact", "kind": "fact", "title": "农业措施", "text": "",
                    "requirement_ids": ids, "evidence_ids": ["right"],
                }], finish_if_partial=True)
            self.script.append([call])
            return await super().get_response(system_instructions, input, *args, **kwargs)

    model = FactModel([])
    monkeypatch.setattr(orchestrator, "chat_model", lambda *_args: model)

    async def audit(_rt, messages, **_kwargs):
        request = json.loads(messages[1]["content"])
        return json.dumps({
            "passed": True,
            "covered_requirement_ids": [req["id"] for req in request["frozen_requirements"]],
            "approved_block_ids": [block["id"] for block in request["blocks"]],
            "gaps": [], "unsupported_claims": [],
        })

    monkeypatch.setattr(orchestrator.Runtime, "complete_text", audit)
    return model
