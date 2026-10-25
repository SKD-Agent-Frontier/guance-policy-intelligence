import os
from pathlib import Path
import subprocess
import sys
from typing import Literal
import unittest

from pydantic import ValidationError

from guance_rag import HitV03, MediaHitV04, MediaPassageV04, PassageV03
from guance_rag.media_models import OCRRange


def source_fields():
    return {
        "planid": "p-1",
        "title": "类型契约",
        "area": None,
        "fyp": None,
        "admin_level": None,
        "plantype_label": None,
        "document_id": "doc-1",
        "document_version_id": None,
        "source_hash": "sha256:source",
        "generation_id": "gen-1",
        "source_path": "source.md",
        "snapshot": "snapshot.md",
        "body_start": 0,
        "body_origin": 0,
        "section_id": None,
        "section_idx": None,
        "section_path": "",
    }


class ContractTypeTests(unittest.TestCase):
    def test_contract_exports_import_without_runtime_module(self):
        engine_root = Path(__file__).resolve().parents[1]
        script = """
import importlib.abc
import sys

class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'guance_rag.runtime':
            raise ModuleNotFoundError('runtime import blocked for CI contract check')

sys.meta_path.insert(0, BlockRuntime())
from guance_rag import HitV03, MediaHitV04, PassageV03, RuntimeOptions
assert HitV03 and MediaHitV04 and PassageV03 and RuntimeOptions
assert 'guance_rag.runtime' not in sys.modules
"""
        environment = dict(os.environ, PYTHONPATH=str(engine_root / "src"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=engine_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_contract_models_expose_versioned_literal_types(self):
        self.assertEqual(HitV03.model_fields["contract_version"].annotation, Literal["proposal-v0.3"])
        self.assertEqual(PassageV03.model_fields["read_scope"].annotation, Literal["index", "section", "chunk"])
        self.assertEqual(MediaHitV04.model_fields["contract_version"].annotation, Literal["proposal-v0.4"])
        self.assertEqual(MediaPassageV04.model_fields["read_scope"].annotation, Literal["media"])

    def test_text_hit_accepts_unicode_codepoint_ranges_and_rejects_mismatched_content(self):
        hit_data = source_fields() | {
            "chunk_id": "chunk-1",
            "parent_id": "parent-1",
            "representation_id": "rep-1",
            "chunk_char_start": 0,
            "chunk_char_end": 2,
            "char_start": 0,
            "char_end": 2,
            "content": "😀文",
            "score": 0.5,
            "truncated": False,
        }

        hit = HitV03.model_validate(hit_data)

        self.assertEqual(hit.content, "😀文")
        with self.assertRaises(ValidationError):
            HitV03.model_validate(hit_data | {"content": "😀"})

    def test_ocr_range_rejects_offsets_outside_the_transcript(self):
        with self.assertRaises(ValidationError):
            OCRRange(
                transcript_id="transcript-1",
                transcript_hash="sha256:transcript",
                full_char_start=4,
                full_char_end=8,
                char_start=3,
                char_end=6,
            )


if __name__ == "__main__":
    unittest.main()
