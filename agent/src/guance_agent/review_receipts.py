"""In-memory approvals issued at the semantic reviewer boundary.

Indexes can change when another fact is rejected. Bind an approved block to the
contents and source identities of its dependencies instead of their positions.
"""
import json
from dataclasses import dataclass, field


def _signature(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fact_signature(claim, refs):
    sources = [refs.get(cid) for cid in claim.chunk_ids]
    if not sources or any(ref is None for ref in sources):
        return None
    return _signature([claim.model_dump(mode="json"),
                       [ref.model_dump(mode="json") for ref in sources]])


@dataclass
class ReviewReceipts:
    facts: set[str] = field(default_factory=set)
    blocks: set[str] = field(default_factory=set)

    def block_signature(self, block, dependencies, refs):
        signatures = [fact_signature(claim, refs) for claim in dependencies]
        if not signatures or None in signatures:
            return None
        fields = {key: value for key, value in block.items()
                  if key != "supporting_claim_indexes"}
        return _signature([fields, signatures])

    def approve(self, block, dependencies, refs):
        signature = self.block_signature(block, dependencies, refs)
        if signature is not None:
            self.blocks.add(signature)
            self.facts.update(fact_signature(claim, refs) for claim in dependencies)
