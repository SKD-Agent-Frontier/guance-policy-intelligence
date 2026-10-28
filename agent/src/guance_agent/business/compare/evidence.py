"""为完整比较对象集合收集有限、可回贴的原文证据。

流程为目录身份读取 → 同一 scope 内主题检索 → 按对象轮流回读正文 → 原句选择。
目录和命中元数据用于展示身份与定位，只有真正读到、且已由工具层登记的原句
才会进入 EvidenceUnit。本模块不创建 RAG/客户端，也不修改 ctx.scope。
"""
import re
import time
from dataclasses import dataclass, field

from ...schemas import Hit, Passage
from ..documents import group_by_document

META_FIELDS = ("planid", "title", "area", "fyp", "plantype_label")
# 这是送入模型的原文字符预算，不是语料全文长度或检索命中总数。
# 按对象均分并轮流读取，避免一个长文档耗尽其他政策的比较机会。
MAX_CONTEXT_CHARS = 12000
MAX_READS_PER_OBJECT = 3
# 章节名仅提供保守的时期线索，不做原文改写，也不承诺识别全部历史段落。
HISTORICAL_SECTION = re.compile(r"发展背景|发展基础|现实基础|发展现状|形势背景|工作回顾|历史回顾|现状分析|发展回顾")


@dataclass
class EvidenceUnit:
    """本轮可供模型选择的一段原句，以及它所属的唯一政策和原始 chunk。

    id（例如 e0）只在本轮 Collection 内有效；chunk_id 才用于共享证据注册表。
    保留 section_path 是为了让相同词语在“历史背景”和“本期目标”中可被区分。
    """

    id: str
    planid: str
    chunk_id: str
    text: str
    section_path: str = ""

    @property
    def historical(self):
        """章节名称含历史/背景线索时标记，结果层据此限制本期事实引用。"""
        return bool(HISTORICAL_SECTION.search(self.section_path))

    def to_prompt(self):
        """只序列化模型选择证据所需字段；不向模型提供本机路径或客户端配置。"""
        return {"id": self.id, "planid": self.planid, "chunk_id": self.chunk_id,
                "text": self.text, "section_path": self.section_path,
                "context_stage": "historical_or_background" if self.historical else "policy_content"}


@dataclass
class Collection:
    """一次有限取证的中间产物，不是“全文已检查”的证明。

    objects 按用户 targets 顺序排列；units 仅含实际回读原句；notes 是覆盖状态，
    不可当成政策事实。failed_objects 记录目录失败/身份异常，防止继续错误借证。
    """

    objects: list[dict] = field(default_factory=list)
    units: list[EvidenceUnit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    failed_objects: set[str] = field(default_factory=set)


def _segments(text, terms, budget, *, start_complete=True):
    """在一个已读 chunk 范围内选取至多四段、合计不超过 budget 字的原文。

    用句末标点而非 PDF 换行判断句界，按主题词的字面命中数排序，分数相同时
    保留原文先后。返回值均为连续原文切片，不拼接省略号或补写缺字。
    start_complete=False 表示窗口可能从半句开始，应丢弃第一个不确定的句子；
    最后没有结束标点的残句同样不进入候选。超长原句直接跳过，不截短伪造句界。
    """
    sentences = [m for m in re.finditer(r"[^。；;！？!?]+[。；;！？!?]", text)
                 if m.group().strip() and (m.start() > 0 or start_complete)]
    # 这是有限窗口内的相关度启发式，不能代替上游检索，也不能证明语义相关。
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
            # “培养期内”通常承接上一句的人才/项目对象。只回贴相邻完整原句，
            # 若其前文不在当前有效窗口内则放弃，不能将范围扩大到整份规划。
            content = text[sentences[index - 1].start():match.end()].strip()
        if len(content) > remaining or len(content) < 6:
            continue
        chosen.append(content)
        remaining -= len(content)
        if len(chosen) == 4:
            break
    return chosen


async def collect(task, ctx, services, plan):
    """通过共享服务收集证据并返回 Collection，保留每个对象的独立身份。

    task.targets 已由路由确定，所有主题查询均使用整个对象集合。逐对象回读只为
    提升覆盖，不能把 ctx.scope 临时缩成单个对象后再拼接成一个“全量比较”。
    本地计数用于安排取证；服务层仍独立执行真正的总预算、范围和截止时间校验。
    """
    result = Collection()
    metadata, indexes, hits, query_results = {}, {}, [], []
    used = 0
    # 取证接近截止时间时主动停止。八秒只是停止继续取证的保守阈值，不能保证
    # 后续模型一定能完成；实际模型调用仍受共享 deadline 约束并可能返回超时。
    def can_call():
        return (used < ctx.settings.max_tool_calls
                and services.deadline - time.monotonic() > 8)

    # 1. 先读目录，获取真实身份和可定位章节。目录本身不生成原文事实。
    for pid in task.targets:
        if not can_call():
            result.notes.append("运行预算不足，部分对象身份或正文尚未读取。")
            break
        used += 1
        reply = await services.read(pid)
        if reply.status == "ok" and isinstance(reply.data, Passage):
            data = reply.data
            if data.planid != pid:
                # 返回身份与请求对象不符时整侧排除，不能把甲的原文登记成乙。
                result.failed_objects.add(pid)
                result.notes.append("对象读取返回了不一致的身份，已排除其证据。")
                continue
            metadata[pid] = {key: getattr(data, key) for key in META_FIELDS}
            indexes[pid] = data.sections or []
        else:
            result.failed_objects.add(pid)
            result.notes.append(f"对象{pid}目录读取状态：{reply.status}；不据此推断政策内容。")

    # 2. 共同主题检索。先留出每个对象一次正文读取预算，再决定能执行多少查询。
    # search 不传额外地区/时期过滤，避免把跨地区或跨期的显式 targets 截成单侧。
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
    # 无命中时可沿用已读目录身份；有命中时首个 Hit 的 None 也保留为未知，
    # 不把后条命中或目录的信息拼成一个看似完整、实则来源混杂的身份对象。
    result.objects = [metadata.get(pid, dict.fromkeys(META_FIELDS, None)) | {"planid": pid}
                      for pid in task.targets]
    candidates = {pid: [] for pid in task.targets}
    seen = set()
    # 3. 为回读安排候选：每个查询先给每个对象一个候选，再补剩余命中。
    # 去重键含 planid，不能因不同对象出现相同局部编号就把另一侧候选删掉。
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
    # 下列词只影响原句/章节的有限候选排序，不改变检索服务的查询语义或权限范围。
    terms = list(dict.fromkeys(plan.dimensions + [word for query in plan.queries
                                               for word in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)]))
    objective = any("目标" in dimension for dimension in plan.dimensions)
    implementation = any(re.search(r"执行|实施|责任|保障", dimension) for dimension in plan.dimensions)
    if objective:
        terms.extend(["目标", "到20", "力争", "预期", "达到"])
    if implementation:
        terms.extend(["组织", "实施", "保障", "经费", "考核"])
    # 对“目标”“执行保障”等明确维度优先读对应目录位置，再读主题检索片段。
    # 优先级只决定先看哪里，目录标题自身仍不能作为事实证据输出。
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
    # 4. 外层轮次、内层对象：先让每个对象获得一次读取机会，再进入下一轮。
    # 三轮是每对象读取位置上限；总预算更小时，最后一轮可以只有部分对象得到读取。
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
                    # 同一 chunk 已在章节读取中完整观察时不重复读；只读过部分的
                    # chunk 暂移到 deferred，让其他未见片段先得到机会。
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
            # 章节可能跨多个 chunk。原句必须限制在“本次 Passage”和该 chunk
            # 的坐标交集内，才能让最终 Claim 引文与已登记观察范围相符。
            for span in passage.chunks:
                if passage.char_start is None:
                    continue
                start = max(span.char_start, passage.char_start) - passage.char_start
                end = min(span.char_end, passage.char_end) - passage.char_start
                if start >= end or ctx.evidence.get(span.chunk_id) is None:
                    continue
                text = passage.content[start:end]
                # 只有文首、明确的上一句结束位置、或章节完整起点才相信首句完整。
                # 不确定时宁可少用半句，也不把一个缺少主语/条件的残片当成事实。
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
    # 覆盖失败只记录“本轮没取得什么”，不能推断政策全文没有相应措施或资格。
    if any(not any(unit.planid == pid for unit in result.units) for pid in task.targets):
        result.notes.append("部分政策尚未取得正文证据，不作缺失条款或优劣判断。")
    if used >= ctx.settings.max_tool_calls:
        result.notes.append("已用完本次取证预算，未声称覆盖政策全文。")
    return result
