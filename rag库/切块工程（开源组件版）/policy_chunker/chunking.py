"""政策约束适配层：官方组件切分，项目只检查边界及保留完整保护单元。

regions 只组织同章节的结构输入，不按 token 拼块；Engine 真正生成父子候选。
完整性不满足时留痕重试或保留完整单元，不调用旧版 make_parents/make_chunks。
"""
import json
import re
from bisect import bisect_left, bisect_right
from .engine import Engine, MappingError
from .protection import EXCLUDED, source_quality, sentence_ranges, table_groups
from .source import stable_id, sha
from .tokens import retrieval_text


def marker(text):
    """只区分列举形式，帮助限定引导语作用域；不把金额/年份当作项目号。"""
    text = text.lstrip()
    if re.match(r"^[一二三四五六七八九十百]+是", text):
        return "shi"
    if re.match(r"^[（(]", text):
        return "paren"
    return "numeric"


def regions(source, units, structures, config_hash):
    """输出同结构的连续范围和保护类型，全部位置是完整文件 Unicode 码点。

    这里不按长度合并段落。列表引导语有自己的输入作用域；遇到下一组引导语
    或编号层次变化就结束，防止给后一个列表错误附上前一个列表的条件。
    """
    owners = {s["id"]: s for s in structures}
    result, i = [], 0
    while i < len(units):
        first = units[i]
        if first["kind"] in EXCLUDED:
            i += 1
            continue
        owner, stop, kind = owners[first["structure_id"]], i+1, "text"
        def next_content(index):
            while index < len(units) and units[index]["kind"] == "whitespace":
                index += 1
            return index
        if owner["kind"] == "article":
            kind = "article"
            while stop < len(units) and units[stop]["start"] < owner["end"]:
                stop += 1
        elif first["kind"] == "literal":
            kind = "literal"
        else:
            look = i
            if first["kind"] in ("caption", "note"):
                while look < len(units) and units[look]["kind"] in ("caption", "note", "whitespace"):
                    look += 1
            if look < len(units) and units[look]["kind"] == "table" and units[look]["structure_id"] == first["structure_id"]:
                kind, stop = "table", look+1
                while stop < len(units) and units[stop]["structure_id"] == first["structure_id"] and units[stop]["kind"] in ("note", "whitespace"):
                    stop += 1
            else:
                look = next_content(i+1)
                intro = first["text"].rstrip().endswith(("：", ":")) and look < len(units) and units[look]["kind"] == "list_item" and units[look]["structure_id"] == first["structure_id"]
                if first["kind"] == "list_item" or intro:
                    kind = "list"
                    family = marker(units[look]["text"]) if intro else marker(first["text"])
                    if intro:
                        # 明确标注引导语，供必要关联与独立校验识别，不能把它单独发布。
                        first["kind"] = "list_intro"
                        first["id"] = stable_id("unit", source.version, first["start"], first["end"], "list_intro", config_hash)
                    while stop < len(units):
                        following = units[stop]
                        if following["structure_id"] != first["structure_id"]:
                            break
                        if following["kind"] == "note":
                            # 列表后的附注归于当前组，并封闭其作用域，不能把之后
                            # 不相关的列表也吞进来或在保留完整项目时遗漏这段附注。
                            while stop < len(units) and units[stop]["structure_id"] == first["structure_id"] and units[stop]["kind"] in ("note","whitespace"):
                                stop += 1
                            break
                        if following["kind"] == "whitespace":
                            stop += 1
                            continue
                        if following["kind"] != "list_item" or marker(following["text"]) != family:
                            break
                        if following["text"].rstrip().endswith(("：", ":")):
                            break
                        stop += 1
                else:
                    while stop < len(units):
                        following = units[stop]
                        if following["structure_id"] != first["structure_id"] or following["kind"] not in ("paragraph", "whitespace", "note"):
                            break
                        look = next_content(stop+1)
                        if following["text"].rstrip().endswith(("：", ":")) and look < len(units) and units[look]["kind"] == "list_item":
                            break
                        stop += 1
        selected = units[i:stop]
        result.append({"start": selected[0]["start"], "end": selected[-1]["end"],
                       "kind": kind, "owner": owner, "units": selected})
        i = stop
    return result


def new_chunk(source, parent, ranges, contexts, config_hash, node_ids=(), generation="component"):
    """正文与共享上下文分范围存储；最终检索文本和 token 在 assemble 中定稿。"""
    segments = [source.segment(a, b) for a, b in ranges]
    return {"id": stable_id("chunk", source.version, parent["id"], ranges, config_hash),
        "document_id": source.version, "parent_id": parent["id"], "kind": parent["kind"],
        "section_path": parent["section_path"], "source_segments": segments,
        "context_segments": list(contexts), "required_relation_ids": [], "status": "ready", "reasons": [],
        "content_hash": sha(json.dumps([s["text"] for s in segments], ensure_ascii=False, separators=(",", ":")).encode()),
        "component_node_ids": list(node_ids), "generation": generation}


def split_document(source, units, structures, counter, config, config_hash):
    """返回父块、子块、细化后的基础单元，以及真实组件调用与节点记录。

    若开源父块在普通段落内部结束，基础单元按该真实边界细化，仍完整覆盖原文；
    original_unit_id 保留原单元身份。这不改变库的切分结果，也不重排文字。
    """
    engine = Engine(source, counter, config, config_hash)
    parents, chunks = [], []
    for region in regions(source, units, structures, config_hash):
        start, end, kind = region["start"], region["end"], region["kind"]
        members, owner = region["units"], region["owner"]
        whole_budget = len(engine.encode(source.text[start:end])) + 32
        protected = kind in ("article", "list", "table", "literal")
        parent_budget = max(config["parent_target_tokens"], whole_budget) if protected else config["parent_target_tokens"]
        prefix_cost = counter.count(retrieval_text(source, owner["path"], [], []))
        child_budget = max(32, config["child_target_tokens"]-prefix_cost)
        if kind in ("table", "literal"):
            child_budget = whole_budget

        def canonical(position):
            while position > start and source.text[position-1].isspace():
                position -= 1
            return position
        # 这个集合仅审核组件候选是否在完整句群/段落边界，不负责组织或拼接块。
        safe_edges = {canonical(a) for u in members for a in (u["start"], u["end"])}
        safe_edges.update(canonical(b) for a, b in sentence_ranges(source, start, end))
        def safe(items):
            return all(canonical(item["end"]) in safe_edges for item in items[:-1])
        try:
            candidates = engine.run(start, end, parent_budget, child_budget, "protected_input" if protected else "ordinary_input")
            if not safe(candidates):
                # 官方普通父候选切穿句子/引号时，重新调用同一组件保留完整输入，
                # 而不是自己写“把几个候选拼回来”的替代切分算法。
                candidates = engine.run(start, end, whole_budget, child_budget, "parent_integrity_retry")
        except (MappingError, ValueError) as error:
            # 失败范围有真实原文、有原因；不能伪装成开源成功结果。
            candidates = [{"start": start, "end": end, "node": None, "children": [], "failure": str(error)}]

        for candidate in candidates:
            a, b = candidate["start"], candidate["end"]
            node = candidate["node"]
            parent = {"id": stable_id("parent", source.version, a, b, config_hash),
                "document_id": source.version, "structure_id": owner["id"], "kind": kind,
                "start": a, "end": b, "source_segments": [source.segment(a, b)],
                "unit_ids": [], "section_path": owner["path"], "status": "ready",
                "component_node_ids": [node.node_id] if node else [],
                "generation": "component_protected_input" if protected else "component",
                "protected_kind": kind if protected else None}
            parents.append(parent)
            raw_children = candidate["children"]
            def ids(begin, finish):
                return [c["node"].node_id for c in raw_children if c["start"] < finish and begin < c["end"]]
            def emit(begin, finish, contexts=(), generation="component", reasons=(), node_ids=None):
                chunk = new_chunk(source, parent, [(begin, finish)], contexts, config_hash,
                                  ids(begin, finish) if node_ids is None else node_ids, generation)
                if reasons:
                    chunk["status"], chunk["reasons"] = "pending", list(reasons)
                chunks.append(chunk)
            if "failure" in candidate:
                parent["generation"] = "mapping_or_component_failed"
                emit(a, b, generation="failed_range_preserved", reasons=["component_or_mapping_failed"])
                continue
            if kind == "table":
                complete = counter.count(retrieval_text(source, owner["path"], [source.segment(a,b)], []))
                if complete <= config["max_input_tokens"]:
                    emit(a, b)
                    continue
                table = next((u for u in members if u["kind"] == "table"), None)
                grouped = table_groups(source, table) if table else None
                if not grouped:
                    emit(a,b,generation="unsafe_table_preserved",reasons=["unsafe_oversize_table"])
                    continue
                row_groups, headers = grouped
                contexts = headers+[source.segment(u["start"],u["end"],u["kind"]) for u in members if u["kind"] not in ("table","whitespace")]
                for row_start, row_end in row_groups:
                    # 行组已由网格约束确定。每组仍交给官方组件保留完整范围；
                    # 不让通用分句器拆穿合并单元格，也不自写按 token 拼行算法。
                    budget = len(engine.encode(source.text[row_start:row_end]))+32
                    row_nodes = engine.run(row_start,row_end,budget,budget,"protected_table_row_group")
                    row_ids = [c["node"].node_id for p in row_nodes for c in p["children"]]
                    emit(row_start,row_end,contexts,"protected_table_row_group",node_ids=row_ids)
                continue
            if kind == "list":
                items = [u for u in members if u["kind"] == "list_item"]
                intro_units = [u for u in members if u["kind"] == "list_intro"]
                cut_item = any(u["start"] < edge < u["end"] and source.text[u["start"]:edge].strip() and source.text[edge:u["end"]].strip()
                               for c in raw_children[:-1] for edge in [c["end"]] for u in items)
                intro_alone = any(not any(u["start"] < c["end"] and c["start"] < u["end"] for u in items) for c in raw_children)
                if cut_item or intro_alone:
                    if not items:
                        # 嵌套引导语/编号层次无法在当前范围确认时，仍保存完整
                        # 待处理子块，不能只留下一个没有子块的孤立父块。
                        emit(a,b,generation="unresolved_list_scope_preserved",reasons=["unresolved_list_scope"])
                        continue
                    contexts = [source.segment(u["start"],u["end"],"condition") for u in intro_units]
                    for item in items:
                        emit(item["start"],item["end"],contexts,"preserved_complete_list_item")
                    continue
            if not safe(raw_children):
                emit(a,b,generation="preserved_complete_unit")
                continue
            for child in raw_children:
                emit(child["start"],child["end"])

    # 父块边界细化全文单元，使校验能逐个核对包含关系；不丢掉任何空白字符。
    boundaries = sorted({x for p in parents for x in (p["start"],p["end"])})
    refined = []
    for unit in units:
        cuts = [unit["start"]]+boundaries[bisect_right(boundaries,unit["start"]):bisect_left(boundaries,unit["end"])]+[unit["end"]]
        for left,right in zip(cuts,cuts[1:]):
            if len(cuts) == 2:
                refined.append(unit)
            else:
                refined.append({**unit,**source.segment(left,right),
                    "id":stable_id("unit",source.version,left,right,unit["kind"],config_hash),
                    "original_unit_id":unit["id"],"split_basis":"component_parent_boundary"})
    unit_starts = [u["start"] for u in refined]
    for parent in parents:
        # 父块互不交叉，利用已排序坐标找成员，避免对大量断行输入反复全表扫描。
        left = bisect_left(unit_starts,parent["start"])
        right = bisect_left(unit_starts,parent["end"])
        parent["unit_ids"] = [u["id"] for u in refined[left:right] if u["end"] <= parent["end"]]
    return parents, chunks, refined, engine.nodes, engine.calls
