"""Adapt the existing one-shot extraction flow to the shared business contract."""

from guance_agent.business import BusinessError, BusinessOutcome
from guance_agent.format_recovery import OutputParseError, parse_agent_output
from guance_agent.output import insufficient_message
from guance_agent.schemas import DIMENSIONS, Hit, Passage

from ...extract import MAX_BLOCKS, build_messages


class ExtractModule:
    """Extract requested facets from a bounded set of retrieved policy chunks."""

    name = "extract"
    requires_targets = False

    async def __call__(self, task, ctx, services) -> BusinessOutcome:
        found = await services.search(task.question)
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
    """Keep only model quotes that occur verbatim in a cited, read passage.

    Missing or paraphrased quotes stay as unquoted candidates. The shared
    verifier may locate an exact sentence from anchors, then must approve it;
    this module never manufactures a quote.
    """
    sources = {block["passage"]["chunk_id"]: block["passage"]["content"]
               for block in blocks}
    for claim in claims:
        claim.quotes = [quote for quote in claim.quotes
                        if any(quote in sources[chunk_id]
                               for chunk_id in claim.chunk_ids if chunk_id in sources)]


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
