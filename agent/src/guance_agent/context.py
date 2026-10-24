"""每次运行独立的证据和工具调用记录。"""
from dataclasses import dataclass, field

from .config import Settings
from .kb import KnowledgeBase
from .schemas import EvidenceRef, Hit, Passage

# 段落级读时这些字段由 ChunkSpan 提供或只属于响应本身，不写进证据。
PASSAGE_ONLY = frozenset({"content", "chunks", "sections", "truncated", "is_index",
                          "read_scope", "next_offset", "char_start", "char_end",
                          "chunk_id", "chunk_char_start", "chunk_char_end"})
MIN_SECRET_LEN = 8   # 过短的"密钥"（如测试用单字符）全局替换会误伤无关字段


def sanitize_error_detail(detail, settings, question=None, limit=200) -> str:
    text = str(detail)
    # 密钥/地址一定是长串，短于此的不当作机密，避免把 "kb_search" 这类普通词打码。
    for item in (settings.llm_api_key, settings.llm_base_url):
        if item and len(item) >= MIN_SECRET_LEN:
            text = text.replace(item, "[REDACTED]")
    # 用户问题再短也是隐私，无条件替换。
    if question and not settings.log_content:
        text = text.replace(question, "[REDACTED]")
    return text if limit is None else text[:limit]
def sanitize_error_fields(value, settings, key=None, question=None):
    if key == "error_detail" and value is not None:
        return sanitize_error_detail(value, settings, question)
    if isinstance(value, dict):
        return {n: sanitize_error_fields(v, settings, n, question) for n, v in value.items()}
    if isinstance(value, list):
        return [sanitize_error_fields(v, settings, question=question) for v in value]
    return value

class EvidenceRegistry:
    def __init__(self):
        self._refs: dict[str, EvidenceRef] = {}
        self._conflicted: set[str] = set()
        self.conflicts: list[str] = []

    def register(self, ref: EvidenceRef) -> None:
        if ref.chunk_id in self._conflicted:
            return
        existing = self._refs.get(ref.chunk_id)
        if existing and existing.planid != ref.planid:
            # ponytail: ownership compares planid only; add document_version_id when refs carry it.
            self._refs.pop(ref.chunk_id, None)
            self._conflicted.add(ref.chunk_id)
            self.conflicts.append(ref.chunk_id)
            return
        self._refs[ref.chunk_id] = ref

    def get(self, chunk_id: str) -> EvidenceRef | None:
        return self._refs.get(chunk_id)

    def ids(self) -> list[str]:
        return list(self._refs)

    def add_result(self, data: list[Hit] | Passage, source: str):
        """把工具返回的来源登记为可引用证据；只保留 EvidenceRef 认识的字段。"""
        if isinstance(data, list):
            for hit in data:
                self.register(EvidenceRef(**hit.model_dump(exclude={"content", "score"}),
                                          source=source))
            return
        if data.section_idx is None:
            return
        metadata = data.model_dump(exclude=PASSAGE_ONLY)
        for chunk in data.chunks:
            # ponytail: complete 暂不参与登记；接入真实 RAG 时改为 observed_ranges 合并，
            # 只登记本页真正可见的交集。
            self.register(EvidenceRef(**metadata, **chunk.model_dump(exclude={"complete"}),
                                      source=source))

@dataclass
class RunContext:
    settings: Settings
    kb: KnowledgeBase
    run_id: str
    session_id: str
    evidence: EvidenceRegistry = field(default_factory=EvidenceRegistry)
    tool_calls: list[dict] = field(default_factory=list)
