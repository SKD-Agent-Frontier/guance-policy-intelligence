"""每次运行独立的证据和工具调用记录。"""
from dataclasses import dataclass, field

from .config import Settings
from .kb import KnowledgeBase
from .schemas import EvidenceRef, Hit, Passage


def sanitize_error_detail(detail, settings) -> str:
    text = str(detail)
    for secret in (settings.llm_api_key, settings.llm_base_url):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:200]
def sanitize_error_fields(value, settings, key=None):
    if key == "error_detail" and value is not None:
        return sanitize_error_detail(value, settings)
    if isinstance(value, dict):
        return {name: sanitize_error_fields(item, settings, name) for name, item in value.items()}
    if isinstance(value, list):
        return [sanitize_error_fields(item, settings) for item in value]
    return value

class EvidenceRegistry:
    def __init__(self):
        self._refs: dict[str, EvidenceRef] = {}
        self.conflicts: list[str] = []

    def register(self, ref: EvidenceRef) -> None:
        existing = self._refs.get(ref.chunk_id)
        if existing and existing.planid != ref.planid:
            self.conflicts.append(ref.chunk_id)
            return
        self._refs[ref.chunk_id] = ref

    def get(self, chunk_id: str) -> EvidenceRef | None:
        return self._refs.get(chunk_id)

    def ids(self) -> list[str]:
        return list(self._refs)

    def add_result(self, data: list[Hit] | Passage, source: str):
        if isinstance(data, list):
            for hit in data:
                self.register(EvidenceRef(**hit.model_dump(exclude={"content", "score"}), source=source))
        elif data.section_idx is not None:
            metadata = data.model_dump(exclude={"content", "chunks", "sections", "truncated",
                                                "char_start", "char_end"})
            for chunk in data.chunks:
                self.register(EvidenceRef(**metadata, **chunk.model_dump(), source=source))

@dataclass
class RunContext:
    settings: Settings
    kb: KnowledgeBase
    run_id: str
    session_id: str
    evidence: EvidenceRegistry = field(default_factory=EvidenceRegistry)
    tool_calls: list[dict] = field(default_factory=list)
