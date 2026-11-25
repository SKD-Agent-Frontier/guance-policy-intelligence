"""Link comparison judgments to the exact fact handles of their supporting cells."""


def analysis_candidates(state, capability):
    if capability not in {"compare", "compare_plans"} or capability not in state.builtin_capabilities:
        return []
    candidate = state.capability_structured.get(capability, {})
    cells = {}
    facts = {
        (ref.planid, claim.dimension, claim.text, tuple(claim.chunk_ids), tuple(claim.quotes)):
            state.fact_handles[ids[0]]
        for claim, ids in zip(state.claims, state.claim_fact_ids, strict=True)
        if ids and claim.chunk_ids and (ref := state.run.evidence.get(claim.chunk_ids[0]))
    }
    for row in candidate.get("matrix", ()):
        for cell in row["cells"]:
            if cell["status"] != "found":
                continue
            # Each quote was emitted as its own producer-bound fact. An entire
            # cell dependency fails if any constituent quote was lost.
            handles = []
            for quote in cell["quotes"]:
                matches = [facts[key] for cid in cell["chunk_ids"] if
                           (key := (cell["planid"], row["dimension"], quote, (cid,), (quote,))) in facts]
                if len(matches) != 1:
                    break
                handles.extend(matches)
            else:
                cells[cell["cell_id"]] = handles

    judgments = [(f"row-{i}", row["dimension"], row["difference"])
                 for i, row in enumerate(candidate.get("matrix", ()))]
    for i, scheme in enumerate(candidate.get("scheme_analysis", ())):
        for field in ("advantages", "limitations", "applicability"):
            judgments.extend((f"scheme-{i}-{field}-{j}", {"advantages": "支持特征", "limitations": "限制条件",
                                     "applicability": "适用范围"}[field], judgment)
                             for j, judgment in enumerate(scheme[field]))
    proposals = []
    for block_id, title, judgment in judgments:
        dependencies = judgment.get("supporting_cells", [])
        if not dependencies or any(cid not in cells for cid in dependencies):
            continue
        handles = list(dict.fromkeys(handle for cid in dependencies for handle in cells[cid]))
        if not handles or (len(handles) < 2 and not block_id.startswith("scheme-")) or len(judgment["text"]) > 1200:
            continue
        proposals.append(dict(id=f"{capability}-{block_id}", title=title,
                              evidence_ids=handles, text=judgment["text"]))
    return proposals

def single_clause_proposal(state, block):
    """A scheme-specific interpretation may have exactly one reviewed source clause."""
    if block.kind != "analysis" or len(block.evidence_ids) != 1:
        return False
    handles = [state.fact_handles.get(block.evidence_ids[0])]
    return any(proposal["id"] == block.id and proposal["title"] == block.title
               and proposal["text"] == block.text and proposal["evidence_ids"] == handles
               for proposal in analysis_candidates(state, "compare_plans")
               if proposal["id"].startswith("compare_plans-scheme-"))
