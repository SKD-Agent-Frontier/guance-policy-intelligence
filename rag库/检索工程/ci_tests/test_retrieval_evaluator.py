import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_retrieval.py"
SPEC = importlib.util.spec_from_file_location("evaluate_retrieval", SCRIPT)
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


class FakeKnowledgeBase:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.closed = False

    def search(self, query, top_k, **filters):
        self.calls.append((query, top_k, filters))
        return self.responses[query]

    def close(self):
        self.closed = True


class RetrievalEvaluatorTests(unittest.TestCase):
    def write_qrels(self, payload):
        temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        with temporary:
            json.dump(payload, temporary)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        return temporary.name

    def test_empty_and_malformed_qrels_fail_before_search(self):
        for payload in ([], {}, [{"query": "q", "relevant_source_ids": []}],
                        [{"query": " ", "relevant_source_ids": ["s"]}],
                        [{"query": "q", "filters": [], "relevant_source_ids": ["s"]}],
                        [{"query": "q", "relevant_source_ids": ["s"], "relevant_chunk_ids": ["c"]}]):
            with self.subTest(payload=payload):
                with self.assertRaises(evaluator.QrelsError):
                    evaluator.load_qrels(self.write_qrels(payload))

    def test_accepts_wrapped_qrels_and_preserves_source_ids_and_filters(self):
        qrels = evaluator.load_qrels(self.write_qrels({"queries": [{
            "query": "Q", "filters": {"area": "苏州"}, "relevant_source_ids": ["src-01"]
        }]}))
        kb = FakeKnowledgeBase({"Q": [{"source_id": "src-01"}]})
        result = evaluator.evaluate_mode(qrels, kb, "bm25")
        self.assertEqual(kb.calls, [("Q", 20, {"area": "苏州"})])
        self.assertEqual(result["queries"][0]["relevant_ids"], ["src-01"])
        self.assertEqual(result["queries"][0]["retrieved_ids"], ["src-01"])
        self.assertEqual(result["macro_metrics"], {
            "recall@5": 1.0, "recall@8": 1.0, "recall@20": 1.0, "mrr": 1.0
        })

    def test_hit_without_source_id_matches_relevant_chunk_ids(self):
        qrels = evaluator.load_qrels(self.write_qrels([{
            "query": "Q", "relevant_chunk_ids": ["chunk-01"]
        }]))
        kb = FakeKnowledgeBase({"Q": [{"chunk_id": "chunk-01"}]})
        result = evaluator.evaluate_mode(qrels, kb, "bm25")
        self.assertEqual(result["queries"][0]["identifier_field"], "relevant_chunk_ids")
        self.assertEqual(result["queries"][0]["retrieved_ids"], ["chunk-01"])
        self.assertEqual(result["macro_metrics"]["mrr"], 1.0)

    def test_chunk_id_attribute_is_used_when_hit_has_no_source_id(self):
        hit = types.SimpleNamespace(chunk_id="chunk-02")
        self.assertEqual(evaluator._result_id(hit, "relevant_source_ids"), "chunk-02")

    def test_cli_passes_store_project_to_runtime_options(self):
        qrels_path = self.write_qrels([{
            "query": "Q", "relevant_chunk_ids": ["chunk-01"]
        }])
        store_project = Path(tempfile.gettempdir()) / "restored-runtime-store"
        captured = {}

        class RuntimeOptions:
            def __init__(self, **kwargs):
                captured["options"] = kwargs
                self.store_project = kwargs.get("store_project")

        class KnowledgeBase(FakeKnowledgeBase):
            def __init__(self, options):
                captured["store_project"] = options.store_project
                super().__init__({"Q": [{"chunk_id": "chunk-01"}]})

        fake_package = types.ModuleType("guance_rag")
        fake_package.RuntimeOptions = RuntimeOptions
        fake_package.RagKnowledgeBase = KnowledgeBase
        output = io.StringIO()
        with patch.dict(sys.modules, {"guance_rag": fake_package}):
            with redirect_stdout(output):
                evaluator.main([qrels_path, "--mode", "bm25", "--store-project", str(store_project)])
        self.assertEqual(captured["options"], {"mode": "bm25", "store_project": store_project})
        self.assertEqual(captured["store_project"], store_project)
        self.assertIn('"relevant_chunk_ids"', output.getvalue())

    def test_macro_metrics_are_computed_per_query_and_per_mode(self):
        qrels = [
            {"query": "first", "filters": {}, "id_field": "relevant_source_ids", "relevant_ids": ["a", "b"]},
            {"query": "second", "filters": {}, "id_field": "relevant_source_ids", "relevant_ids": ["z"]},
        ]
        instances = {}

        def factory(mode):
            responses = {
                "first": [{"source_id": "x"}, {"source_id": "a"}],
                "second": [{"source_id": "z"}],
            } if mode == "bm25" else {
                "first": [{"source_id": "b"}, {"source_id": "a"}],
                "second": [],
            }
            instances[mode] = FakeKnowledgeBase(responses)
            return instances[mode]

        results = evaluator.evaluate_modes(qrels, ["bm25", "hybrid"], factory)
        bm25, hybrid = results["modes"]
        self.assertEqual(bm25["macro_metrics"]["recall@5"], 0.75)
        self.assertEqual(bm25["macro_metrics"]["mrr"], 0.75)
        self.assertEqual(hybrid["macro_metrics"]["recall@5"], 0.5)
        self.assertEqual(hybrid["macro_metrics"]["mrr"], 0.5)
        self.assertTrue(all(kb.closed for kb in instances.values()))


if __name__ == "__main__":
    unittest.main()
