"""每次运行独立的证据和工具调用记录。"""
from dataclasses import dataclass, field

from .config import Settings
from .kb import KnowledgeBase
from .schemas import EvidenceRef, Hit, Passage

# 段落级读时这些字段由 ChunkSpan 提供或只属于响应本身，不写进证据。
COORD_BASES = frozenset({"body_first_non_whitespace"})
COORD_UNITS = frozenset({"unicode_codepoint"})
VERSION_FIELDS = ("document_version_id", "generation_id", "source_hash",
                  "offset_basis", "offset_unit")


def merge_ranges(ranges):
    """合并重叠或相邻的码点区间，结果按起点升序且互不相交。"""
    out: list[list[int]] = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [tuple(r) for r in out]


def check_ref(ref) -> str | None:
    """确定性校验证据坐标；返回违规原因，None 表示通过。"""
    if ref.chunk_char_start is None or ref.chunk_char_end is None:
        return None                      # mock 后端未提供完整块坐标，跳过块级检查
    if ref.chunk_char_start >= ref.chunk_char_end:
        return "完整块范围为空或倒置"
    if not (ref.chunk_char_start <= ref.char_start and ref.char_end <= ref.chunk_char_end):
        return "本次范围越出完整块"
    if ref.section_char_start is not None and ref.section_char_end is not None:
        if not (ref.section_char_start <= ref.chunk_char_start
                and ref.chunk_char_end <= ref.section_char_end):
            return "块越出所属章节范围"
    if ref.offset_basis is not None and ref.offset_basis not in COORD_BASES:
        return f"不支持的坐标基准：{ref.offset_basis}"
    if ref.offset_unit is not None and ref.offset_unit not in COORD_UNITS:
        return f"不支持的坐标单位：{ref.offset_unit}"
    return None


def same_source(a, b) -> bool:
    """同一 chunk_id 的两条登记是否来自同一版本；双方都给的字段必须相等。"""
    if a.planid != b.planid:
        return False
    for name in VERSION_FIELDS:
        left, right = getattr(a, name), getattr(b, name)
        if left is not None and right is not None and left != right:
            return False
    return True


def covers(ref) -> bool:
    """已见范围是否覆盖整个块。"""
    if ref.chunk_char_start is None or ref.chunk_char_end is None:
        return False
    return any(start <= ref.chunk_char_start and ref.chunk_char_end <= end
               for start, end in ref.observed_ranges)


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
        self.rejected: list[dict] = []

    def register(self, ref: EvidenceRef) -> None:
        if ref.chunk_id in self._conflicted:
            return
        reason = check_ref(ref)
        if reason:
            self.rejected.append({"chunk_id": ref.chunk_id, "reason": reason})
            return
        ref = ref.model_copy(update={
            "observed_ranges": merge_ranges([(ref.char_start, ref.char_end)]),
            "complete": False,
        })
        ref = ref.model_copy(update={"complete": covers(ref)})
        existing = self._refs.get(ref.chunk_id)
        if existing is None:
            self._refs[ref.chunk_id] = ref
        elif not same_source(existing, ref):
            # 同一块出现不同版本，无法判断谁是真源：整条作废，避免引用指错政策。
            self._refs.pop(ref.chunk_id, None)
            self._conflicted.add(ref.chunk_id)
            self.conflicts.append(ref.chunk_id)
        else:
            self._refs[ref.chunk_id] = self._merge(existing, ref)

    @staticmethod
    def _merge(old: EvidenceRef, new: EvidenceRef) -> EvidenceRef:
        """分页多次读取同一块时合并已见范围，不用后一页覆盖前一页。"""
        merged = old.model_copy(update={
            "observed_ranges": merge_ranges(old.observed_ranges + new.observed_ranges),
            "char_start": min(old.char_start, new.char_start),
            "char_end": max(old.char_end, new.char_end),
            "source": new.source,
        })
        return merged.model_copy(update={"complete": covers(merged)})

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
