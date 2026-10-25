"""Evaluate retrieval IDs from JSON qrels (source IDs or chunk IDs)."""

import argparse
import json
from pathlib import Path
import sys


CUTOFFS = (5, 8, 20)


class QrelsError(ValueError):
    """Raised when the qrels input is empty or does not match the expected schema."""


def load_qrels(path):
    """Load and validate qrels before any knowledge-base search is performed."""
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QrelsError("cannot read qrels JSON: " + str(exc)) from None

    if isinstance(data, dict):
        data = data.get("queries")
    if not isinstance(data, list) or not data:
        raise QrelsError("qrels must contain a non-empty array of queries")

    validated = []
    for index, row in enumerate(data):
        label = "qrels entry " + str(index)
        if not isinstance(row, dict):
            raise QrelsError(label + " must be an object")
        query = row.get("query")
        if not isinstance(query, str) or not query.strip():
            raise QrelsError(label + " requires a non-empty string query")
        filters = row.get("filters", {})
        if not isinstance(filters, dict) or any(not isinstance(key, str) for key in filters):
            raise QrelsError(label + " filters must be an object with string keys")
        if "query" in filters or "top_k" in filters:
            raise QrelsError(label + " filters cannot override query or top_k")
        id_fields = [key for key in ("relevant_source_ids", "relevant_chunk_ids") if key in row]
        if len(id_fields) != 1:
            raise QrelsError(label + " requires exactly one of relevant_source_ids or relevant_chunk_ids")
        id_field = id_fields[0]
        relevant = row[id_field]
        if (not isinstance(relevant, list) or not relevant
                or any(not isinstance(identifier, str) or not identifier for identifier in relevant)):
            raise QrelsError(label + " requires a non-empty array of non-empty string " + id_field)
        validated.append({
            "query": query,
            "filters": dict(filters),
            "id_field": id_field,
            "relevant_ids": list(dict.fromkeys(relevant)),
        })
    return validated


def _result_id(hit, id_field):
    if isinstance(hit, dict):
        get = hit.get
    else:
        get = lambda name, default=None: getattr(hit, name, default)
    preferred = "chunk_id" if id_field == "relevant_chunk_ids" else "source_id"
    fallback = "source_id" if preferred == "chunk_id" else "chunk_id"
    identifier = get(preferred)
    if not isinstance(identifier, str) or not identifier:
        identifier = get(fallback)
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("search result is missing source_id and chunk_id")
    return identifier


def evaluate_mode(qrels, knowledge_base, mode):
    """Evaluate one retrieval mode through a search-compatible KB instance."""
    if not qrels:
        raise QrelsError("qrels must not be empty")
    per_query = []
    metric_totals = {"recall@" + str(k): 0.0 for k in CUTOFFS}
    metric_totals["mrr"] = 0.0

    for row in qrels:
        hits = knowledge_base.search(row["query"], top_k=max(CUTOFFS), **row["filters"])
        if hits is None:
            raise ValueError("search must return an iterable of hits")
        ranked_ids = [_result_id(hit, row["id_field"]) for hit in hits]
        relevant = set(row["relevant_ids"])
        reciprocal_rank = next(
            (1.0 / rank for rank, source_id in enumerate(ranked_ids, 1) if source_id in relevant),
            0.0,
        )
        metrics = {}
        for cutoff in CUTOFFS:
            name = "recall@" + str(cutoff)
            recalled = len(set(ranked_ids[:cutoff]) & relevant) / len(relevant)
            metrics[name] = recalled
            metric_totals[name] += recalled
        metrics["mrr"] = reciprocal_rank
        metric_totals["mrr"] += reciprocal_rank
        per_query.append({
            "query": row["query"],
            "identifier_field": row["id_field"],
            "relevant_ids": list(row["relevant_ids"]),
            "retrieved_ids": ranked_ids,
            "metrics": metrics,
        })

    count = len(per_query)
    return {
        "mode": mode,
        "query_count": count,
        "macro_metrics": {name: value / count for name, value in metric_totals.items()},
        "queries": per_query,
    }


def evaluate_modes(qrels, modes, kb_factory):
    """Evaluate each requested mode with a separately constructed KB instance."""
    if not qrels:
        raise QrelsError("qrels must not be empty")
    results = []
    for mode in modes:
        knowledge_base = kb_factory(mode)
        try:
            results.append(evaluate_mode(qrels, knowledge_base, mode))
        finally:
            close = getattr(knowledge_base, "close", None)
            if close is not None:
                close()
    return {"qrels_count": len(qrels), "modes": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qrels", type=Path,
                        help="JSON qrels; use relevant_source_ids or relevant_chunk_ids per query")
    parser.add_argument("--mode", action="append", choices=("bm25", "hybrid", "dense"), required=True,
                        help="retrieval mode to evaluate; repeat to evaluate multiple modes")
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    parser.add_argument("--store-project", type=Path,
                        help="runtime store project directory (defaults to RuntimeOptions.DEFAULT_STORE)")
    args = parser.parse_args(argv)

    try:
        qrels = load_qrels(args.qrels)
        # Delay project imports so injected users and isolated tests need only Python's standard library.
        engine_root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(engine_root / "src"))
        from guance_rag import RagKnowledgeBase, RuntimeOptions

        def create_knowledge_base(mode):
            options = {"mode": mode}
            if args.store_project is not None:
                options["store_project"] = args.store_project
            return RagKnowledgeBase(RuntimeOptions(**options))

        result = evaluate_modes(qrels, args.mode, create_knowledge_base)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (QrelsError, OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
