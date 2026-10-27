"""通过注入的服务均衡取证；不改ctx.scope，不直接访问RAG或构造客户端。"""
import re
import time
from dataclasses import dataclass, field

from ...schemas import Hit, Passage
from ..documents import group_by_document

META_FIELDS = ("planid", "title", "area", "fyp", "plantype_label")
MAX_CONTEXT_CHARS = 12000
MAX_READS_PER_OBJECT = 3


@dataclass
class EvidenceUnit:
    id: str
    planid: str
    chunk_id: str
    text: str

    def to_prompt(self):
        return {"id": self.id, "planid": self.planid, "chunk_id": self.chunk_id,
                "text": self.text}


@dataclass
class Collection:
    objects: list[dict] = field(default_factory=list)
    units: list[EvidenceUnit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    failed_objects: set[str] = field(default_factory=set)


def _segments(text, terms, budget):
    """选取相关原文窗口，返回精确切片；不使用省略号拼接成伪造引句。"""
    paragraphs = [m for m in re.finditer(r"[^\n]+", text) if m.group().strip()]
    ranked = sorted(enumerate(paragraphs), key=lambda item: (
        -sum(term in item[1].group() for term in terms), item[0]))
    chosen, remaining = [], budget
    for _, match in ranked:
        if remaining <= 0:
            break
        content = match.group()
        # 长段落围绕首个主题词选窗；保留原始字符，不修改标点或空白。
        if len(content) > min(700, remaining):
            at = next((content.find(term) for term in terms if term in content), 0)
            start = max(0, at - 100)
            content = content[start:start + min(700, remaining)]
        if len(content.strip()) < 6:
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
        for hit in query_hits:
            key = (hit.planid, hit.chunk_id)
            if hit.planid not in query_objects and key not in seen:
                diverse.append(hit)
                seen.add(key)
                query_objects.add(hit.planid)
    seen.clear()
    for hit in diverse + hits:
        key = (hit.planid, hit.chunk_id)
        if key not in seen:
            seen.add(key)
            candidates[hit.planid].append(("chunk", hit.chunk_id))
    terms = list(dict.fromkeys(plan.dimensions + [word for query in plan.queries
                                               for word in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)]))
    for pid in task.targets:
        sections = sorted(indexes.get(pid, []), key=lambda section: -sum(
            term in str(section.get("path", "")) for term in terms))
        candidates[pid].extend(("section", s["idx"]) for s in sections
                               if isinstance(s.get("idx"), int))
    allowance = MAX_CONTEXT_CHARS // max(1, len(task.targets))
    consumed = dict.fromkeys(task.targets, 0)
    # 先让每个对象得到一个正文片段，再补充下一轮，避免跨对象覆盖失衡。
    for _ in range(MAX_READS_PER_OBJECT):
        for pid in task.targets:
            if (pid in result.failed_objects or not candidates[pid] or not can_call()
                    or consumed[pid] >= allowance):
                continue
            kind, value = candidates[pid].pop(0)
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
                for segment in _segments(text, terms, min(1800, allowance - consumed[pid])):
                    if any(u.planid == pid and u.chunk_id == span.chunk_id
                           and u.text == segment for u in result.units):
                        continue
                    result.units.append(EvidenceUnit(f"e{len(result.units)}", pid,
                                                     span.chunk_id, segment))
                    consumed[pid] += len(segment)
    if any(not any(unit.planid == pid for unit in result.units) for pid in task.targets):
        result.notes.append("部分政策尚未取得正文证据，不作缺失条款或优劣判断。")
    if used >= ctx.settings.max_tool_calls:
        result.notes.append("已用完本次取证预算，未声称覆盖政策全文。")
    return result
