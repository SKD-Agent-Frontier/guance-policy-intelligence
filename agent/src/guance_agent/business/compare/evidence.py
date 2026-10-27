"""通过注入的服务均衡取证；不改ctx.scope，不直接访问RAG或构造客户端。"""
import re
import time
from dataclasses import dataclass, field

from ...schemas import Hit, Passage
from ..documents import group_by_document

META_FIELDS = ("planid", "title", "area", "fyp", "plantype_label")
MAX_CONTEXT_CHARS = 12000
MAX_READS_PER_OBJECT = 3
HISTORICAL_SECTION = re.compile(r"发展背景|发展基础|现实基础|发展现状|形势背景|工作回顾|历史回顾|现状分析|发展回顾")


@dataclass
class EvidenceUnit:
    id: str
    planid: str
    chunk_id: str
    text: str
    section_path: str = ""

    @property
    def historical(self):
        return bool(HISTORICAL_SECTION.search(self.section_path))

    def to_prompt(self):
        return {"id": self.id, "planid": self.planid, "chunk_id": self.chunk_id,
                "text": self.text, "section_path": self.section_path,
                "context_stage": "historical_or_background" if self.historical else "policy_content"}


@dataclass
class Collection:
    objects: list[dict] = field(default_factory=list)
    units: list[EvidenceUnit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    failed_objects: set[str] = field(default_factory=set)


def _segments(text, terms, budget, *, start_complete=True):
    """按完整句选取原文，保留跨行文本；不截断长句或把PDF换行当句界。"""
    sentences = [m for m in re.finditer(r"[^。；;！？!?]+[。；;！？!?]", text)
                 if m.group().strip() and (m.start() > 0 or start_complete)]
    ranked = sorted(enumerate(sentences), key=lambda item: (
        -sum(term in item[1].group() for term in terms), item[0]))
    chosen, remaining = [], budget
    for index, match in ranked:
        if remaining <= 0:
            break
        content = match.group().strip()
        if re.match(r"(?:在)?培养期内", content):
            if not index:
                continue
            # 培养期安排需保留上一原句中的对象，不能泛化到整份规划的所有人才。
            content = text[sentences[index - 1].start():match.end()].strip()
        if len(content) > remaining or len(content) < 6:
            continue
        chosen.append(content)
        remaining -= len(content)
        if len(chosen) == 4:
            break
    return chosen


async def collect(task, ctx, services, plan):
    """先登记各对象身份，再共同检索、轮询回读；上下文与调用次数均有界。"""
    result = Collection()
    metadata, indexes, hits, query_results = {}, {}, [], []
    used = 0
    # 留出模型生成及基础层核验时间；不足时不继续触发底座预算错误。
    def can_call():
        return (used < ctx.settings.max_tool_calls
                and services.deadline - time.monotonic() > 8)

    for pid in task.targets:
        if not can_call():
            result.notes.append("运行预算不足，部分对象身份或正文尚未读取。")
            break
        used += 1
        reply = await services.read(pid)
        if reply.status == "ok" and isinstance(reply.data, Passage):
            data = reply.data
            if data.planid != pid:
                result.failed_objects.add(pid)
                result.notes.append("对象读取返回了不一致的身份，已排除其证据。")
                continue
            metadata[pid] = {key: getattr(data, key) for key in META_FIELDS}
            indexes[pid] = data.sections or []
        else:
            result.failed_objects.add(pid)
            result.notes.append(f"对象{pid}目录读取状态：{reply.status}；不据此推断政策内容。")

    # 预留每个对象至少一次回读，检索不占满预算。
    search_limit = max(0, ctx.settings.max_tool_calls - used - len(task.targets))
    for query in plan.queries[:search_limit]:
        if not can_call():
            break
        used += 1
        reply = await services.search(query)
        if reply.status == "ok" and isinstance(reply.data, list):
            selected = [h for h in reply.data if isinstance(h, Hit)
                        and h.planid in task.targets and h.planid not in result.failed_objects]
            hits.extend(selected)
            query_results.append(selected)
        else:
            result.notes.append(f"主题检索状态：{reply.status}；未检索到不代表政策没有规定。")
    for document in group_by_document(hits):
        # 有命中时严格采用共享分组函数的首条元数据，不能用目录或后条填补None。
        metadata[document.planid] = {key: getattr(document, key) for key in META_FIELDS}
    result.objects = [metadata.get(pid, dict.fromkeys(META_FIELDS, None)) | {"planid": pid}
                      for pid in task.targets]
    candidates = {pid: [] for pid in task.targets}
    seen = set()
    # 每个检索词先为每个对象贡献一个候选，再补剩余命中，避免首个主题淹没其他维度。
    diverse = []
    for query_hits in query_results:
        query_objects = set()
        for hit in sorted(query_hits, key=lambda h: bool(HISTORICAL_SECTION.search(h.section_path))):
            key = (hit.planid, hit.chunk_id)
            if hit.planid not in query_objects and key not in seen:
                diverse.append(hit)
                seen.add(key)
                query_objects.add(hit.planid)
    seen.clear()
    for hit in diverse + sorted(hits, key=lambda h: bool(HISTORICAL_SECTION.search(h.section_path))):
        key = (hit.planid, hit.chunk_id)
        if key not in seen:
            seen.add(key)
            candidates[hit.planid].append(("chunk", hit.chunk_id))
    terms = list(dict.fromkeys(plan.dimensions + [word for query in plan.queries
                                               for word in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)]))
    objective = any("目标" in dimension for dimension in plan.dimensions)
    implementation = any(re.search(r"执行|实施|责任|保障", dimension) for dimension in plan.dimensions)
    if objective:
        terms.extend(["目标", "到20", "力争", "预期", "达到"])
    if implementation:
        terms.extend(["组织", "实施", "保障", "经费", "考核"])
    for pid in task.targets:
        sections = sorted(indexes.get(pid, []), key=lambda section: -sum(
            term in str(section.get("path", "")) for term in terms))
        preferred = []
        for pattern in ([r"主要目标|总体目标|发展目标|预期目标"] if objective else []) + (
                [r"实施保障|组织实施|保障措施|组织保障"] if implementation else []):
            matches = [section for section in sections
                       if re.search(pattern, section.get("path", "").split(" / ")[-1])
                       and not HISTORICAL_SECTION.search(section.get("path", ""))
                       and isinstance(section.get("idx"), int)]
            if matches:
                # 目标优先专门的主要目标小节；保障优先完整父节以保留执行安排。
                selected = min(matches, key=lambda section: (
                    0 if "主要目标" in section["path"].split(" / ")[-1] else 1,
                    section["path"].count(" / ")))
                preferred.append(("section", selected["idx"]))
        candidates[pid] = list(dict.fromkeys(preferred + candidates[pid]))
        candidates[pid].extend(("section", s["idx"]) for s in sections
                               if isinstance(s.get("idx"), int))
    allowance = MAX_CONTEXT_CHARS // max(1, len(task.targets))
    consumed = dict.fromkeys(task.targets, 0)
    read_requests = set()
    # 先让每个对象得到一个正文片段，再补充下一轮，避免跨对象覆盖失衡。
    for _ in range(MAX_READS_PER_OBJECT):
        for pid in task.targets:
            if (pid in result.failed_objects or not candidates[pid] or not can_call()
                    or consumed[pid] >= allowance):
                continue
            fresh, deferred = [], []
            read_chunk_ids = {unit.chunk_id for unit in result.units if unit.planid == pid}
            for candidate in candidates[pid]:
                kind, value = candidate
                if (pid, kind, value) in read_requests:
                    continue
                ref = ctx.evidence.get(value) if kind == "chunk" else None
                if kind == "chunk" and value in read_chunk_ids and ref and ref.complete:
                    continue
                (deferred if kind == "chunk" and value in read_chunk_ids else fresh).append(candidate)
            candidates[pid] = fresh + deferred
            if not candidates[pid]:
                continue
            kind, value = candidates[pid].pop(0)
            read_requests.add((pid, kind, value))
            used += 1
            reply = await services.read(pid, **({"chunk_id": value} if kind == "chunk"
                                              else {"section_idx": value}))
            if reply.status != "ok" or not isinstance(reply.data, Passage):
                result.notes.append(f"对象{pid}正文读取状态：{reply.status}。")
                continue
            passage = reply.data
            if passage.planid != pid or passage.is_index:
                continue
            for span in passage.chunks:
                if passage.char_start is None:
                    continue
                start = max(span.char_start, passage.char_start) - passage.char_start
                end = min(span.char_end, passage.char_end) - passage.char_start
                if start >= end or ctx.evidence.get(span.chunk_id) is None:
                    continue
                text = passage.content[start:end]
                complete_start = (passage.char_start + start == 0
                                  or (start > 0 and passage.content[start - 1] in "。；;！？!?")
                                  or (start == 0 and passage.read_scope == "section"
                                      and passage.char_start == passage.section_char_start))
                for segment in _segments(text, terms, min(1800, allowance - consumed[pid]),
                                         start_complete=complete_start):
                    if any(u.planid == pid and u.chunk_id == span.chunk_id
                           and u.text == segment for u in result.units):
                        continue
                    result.units.append(EvidenceUnit(f"e{len(result.units)}", pid,
                                                     span.chunk_id, segment,
                                                     span.section_path or passage.section_path))
                    consumed[pid] += len(segment)
    if any(not any(unit.planid == pid for unit in result.units) for pid in task.targets):
        result.notes.append("部分政策尚未取得正文证据，不作缺失条款或优劣判断。")
    if used >= ctx.settings.max_tool_calls:
        result.notes.append("已用完本次取证预算，未声称覆盖政策全文。")
    return result
