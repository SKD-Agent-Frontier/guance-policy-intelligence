"""从当前 RagKnowledgeBase API 导出 Agent 原生文本样例。"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parents[1]
sys.path.insert(0, str(ENGINE_ROOT / "src"))

from guance_rag import RagKnowledgeBase, RuntimeOptions  # noqa: E402


SAMPLES = REPO_ROOT / "rag库" / "设计方案2.0" / "Agent对接说明" / "samples"
HIT_QUERY = "养老服务"
HIT_PLANID = "20699"
LONG_PLANID = "48787"
LONG_CHUNK_ID = "chunk_594a17c56902e7fdac2a38662282ebda"
PAGE_CHARS = 6000


def write_sample(name: str, response: object) -> None:
    """只序列化 API 响应本身，不投影或补充字段。"""
    payload = response.model_dump(mode="json")
    (SAMPLES / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def export(store_project: Path) -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="guance-rag-agent-samples-") as cache:
        options = RuntimeOptions(
            store_project=store_project,
            cache_directory=Path(cache),
            mode="bm25",
        )
        with RagKnowledgeBase(options) as kb:
            hits = kb.search(
                HIT_QUERY,
                area="苏州",
                fyp="十四五",
                top_k=20,
                source_types=["text_chunk"],
            )
            hit = next((item for item in hits if item.planid == HIT_PLANID), None)
            if hit is None:
                raise RuntimeError(f"{HIT_QUERY!r}未检索到历史样例planid={HIT_PLANID}")

            passage = kb.read(
                hit.planid,
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                generation_id=hit.generation_id,
            )
            assert passage.chunk_id == hit.chunk_id
            assert passage.document_id == hit.document_id
            assert passage.generation_id == hit.generation_id
            assert passage.content == kb.get_chunk_source(
                hit.chunk_id,
                document_id=hit.document_id,
                generation_id=hit.generation_id,
            ).content

            chapter = kb.read(
                hit.planid,
                section_idx=hit.section_idx,
                max_chars=PAGE_CHARS,
                document_id=hit.document_id,
                generation_id=hit.generation_id,
            )
            assert chapter.read_scope == "section"
            assert chapter.section_idx == hit.section_idx
            assert len(chapter.content) == chapter.char_end - chapter.char_start

            first_page = kb.read(
                LONG_PLANID,
                chunk_id=LONG_CHUNK_ID,
                offset=0,
                max_chars=PAGE_CHARS,
            )
            assert first_page.next_offset is not None
            second_page = kb.read(
                LONG_PLANID,
                chunk_id=LONG_CHUNK_ID,
                offset=first_page.next_offset,
                max_chars=PAGE_CHARS,
                document_id=first_page.document_id,
                generation_id=first_page.generation_id,
            )
            full_chunk = kb.get_chunk_source(
                LONG_CHUNK_ID,
                document_id=first_page.document_id,
                generation_id=first_page.generation_id,
            )
            assert first_page.chunk_id == second_page.chunk_id == full_chunk.chunk_id == LONG_CHUNK_ID
            assert first_page.char_end == second_page.char_start
            assert second_page.next_offset is None
            assert first_page.content + second_page.content == full_chunk.content

            write_sample("Hit.json", hit)
            write_sample("Passage.json", passage)
            write_sample("章节Passage.json", chapter)
            write_sample("长块第1页.json", first_page)
            write_sample("长块第2页.json", second_page)

            print(f"query={HIT_QUERY!r}; hit_planid={hit.planid}; chunk_id={hit.chunk_id}")
            print(f"chapter_section_idx={chapter.section_idx}; chapter_chars={len(chapter.content)}")
            print(
                f"long_chunk_id={LONG_CHUNK_ID}; pages={len(first_page.content)}+"
                f"{len(second_page.content)}; concatenation=verified"
            )
            print("hit_to_chunk_read=verified")
            for name in (
                "Hit.json",
                "Passage.json",
                "章节Passage.json",
                "长块第1页.json",
                "长块第2页.json",
            ):
                print(f"wrote={SAMPLES / name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-project", type=Path, required=True, help="已恢复的本地存储工程目录")
    args = parser.parse_args()
    export(args.store_project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
