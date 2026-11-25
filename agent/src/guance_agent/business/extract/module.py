"""Adapt the existing one-shot extraction flow to the shared business contract."""

from guance_agent.business import BusinessError, BusinessOutcome
from guance_agent.format_recovery import OutputParseError, parse_agent_output
from guance_agent.output import insufficient_message
from guance_agent.schemas import DIMENSIONS, Hit, Passage

from ...extract import BLOCK_CHARS, MAX_BLOCKS, MAX_TOTAL_CHARS, build_messages, extraction_query
from ...verify import NUMBER, SENTENCE


class ExtractModule:
    """Extract requested facets from a bounded set of retrieved policy chunks."""

    name = "extract"
    requires_targets = False

    async def __call__(self, task, ctx, services) -> BusinessOutcome:
        scoped = bool(task.targets or getattr(ctx, "scope", None))
        query = extraction_query(task.question, task.dimensions, scoped=scoped)
        found = await services.search(query)
        if found.status != "ok" or not isinstance(found.data, list):
            return _insufficient(ctx, task, [found.status])

        blocks = []
        read_statuses = []
        for hit in found.data[:MAX_BLOCKS]:
            if not isinstance(hit, Hit):
                continue
            reply = await services.read(hit.planid, chunk_id=hit.chunk_id)
            if reply.status == "ok" and isinstance(reply.data, Passage):
                blocks.append({"hit": hit.model_dump(mode="json"),
                               "passage": reply.data.model_dump(mode="json")})
            else:
                read_statuses.append(reply.status)

        if not blocks:
            return _insufficient(ctx, task, read_statuses)

        messages = build_messages(task.question, task.dimensions, blocks)
        try:
            output = parse_agent_output(await services.call_model(messages))
        except OutputParseError as exc:
            raise BusinessError("OUTPUT_PARSE") from exc

        _keep_source_quotes(output.claims, blocks)
        dimensions = task.dimensions or list(DIMENSIONS)
        covered = {claim.dimension for claim in output.claims if claim.dimension}
        missing = [{"dimension": dimension, "reason": "not_retrieved"}
                   for dimension in dimensions if dimension not in covered]
        hits = {block["hit"]["chunk_id"]: block["hit"] for block in blocks}
        documents, seen = [], set()
        for claim in output.claims:
            for chunk_id in claim.chunk_ids:
                hit = hits.get(chunk_id)
                if hit and hit["planid"] not in seen:
                    seen.add(hit["planid"])
                    documents.append({key: hit.get(key) for key in
                                      ("planid", "title", "area", "fyp")})

        return BusinessOutcome(
            answer=output.answer,
            claims=output.claims,
            structured={
                "dimensions": dimensions,
                "documents": documents,
                "elements": [claim.model_dump(mode="json") for claim in output.claims],
                "missing": missing,
            },
            insufficient=output.insufficient,
        )


def _keep_source_quotes(claims, blocks) -> None:
    """Bind model quotes or extraction anchors to the exact displayed sentences."""
    sources = {}
    budget = MAX_TOTAL_CHARS
    for block in blocks:
        passage = block["passage"]
        content = passage["content"]
        visible = content[:min(BLOCK_CHARS, max(0, budget))]
        sources[passage["chunk_id"]] = (visible, len(visible) < len(content) or passage["truncated"])
        budget -= len(visible) + (len(visible) < len(content))
    for claim in claims:
        if len(claim.chunk_ids) != 1 or claim.chunk_ids[0] not in sources:
            claim.quotes = []
            continue
        visible, truncated = sources[claim.chunk_ids[0]]
        if claim.quotes:
            claim.quotes = [q for q in claim.quotes if q and q in visible]
            continue
        numbers = set(NUMBER.findall(claim.text))
        for match in SENTENCE.finditer(visible):
            sentence = match.group()
            if truncated and match.end() == len(visible) and sentence[-1] not in "。；;！？!?":
                continue
            if any(len(a) >= 2 and a in sentence for a in claim.anchors) and numbers <= set(NUMBER.findall(sentence)):
                claim.quotes = [sentence]
                break



def _insufficient(ctx, task, statuses=()) -> BusinessOutcome:
    dimensions = task.dimensions or list(DIMENSIONS)
    reason = ("retrieval_unavailable" if {"timeout", "backend_error"} & set(statuses)
              else "not_retrieved")
    return BusinessOutcome(
        answer=insufficient_message(ctx.tool_calls),
        structured={
            "dimensions": dimensions,
            "documents": [],
            "elements": [],
            "missing": [{"dimension": name, "reason": reason}
                        for name in dimensions],
        },
        insufficient=True,
    )
