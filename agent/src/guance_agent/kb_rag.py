"""真实 RAG 后端适配：把 RagKnowledgeBase 的输出转成 Agent 自己的类型。

RagKnowledgeBase 自己管理专用库线程和有界队列，本层只做类型转换与来源限定。
首轮只接原生文本，OCR 与图片留到第二阶段。
"""
from .schemas import Hit, Passage

SOURCE_TYPES = ("text_chunk",)


class RagKB:
    name = "rag-local"

    def __init__(self, store_project: str = "", mode: str = "bm25"):
        from guance_rag import RagKnowledgeBase, RuntimeOptions

        options = RuntimeOptions(mode=mode, store_project=store_project) if store_project \
            else RuntimeOptions(mode=mode)
        self._kb = RagKnowledgeBase(options)

    def search(self, query, area, fyp, top_k):
        raws = self._kb.search(query, area, fyp, top_k, source_types=list(SOURCE_TYPES))
        return [Hit(**raw.model_dump()) for raw in raws]

    def read(self, planid, section_idx, max_chars, *, chunk_id=None, offset=0,
             document_id=None, generation_id=None):
        raw = self._kb.read(planid, section_idx, max_chars, chunk_id=chunk_id, offset=offset,
                            document_id=document_id, generation_id=generation_id)
        return Passage(**raw.model_dump())

    def verify_quote(self, chunk_id, quote, observed_ranges, *,
                     document_id=None, generation_id=None):
        """引句只能来自本次实际提供的已见范围。"""
        return self._kb.verify_quote(chunk_id, quote, observed_ranges,
                                     document_id=document_id, generation_id=generation_id)

    def close(self):
        self._kb.close()
