"""仅解析明确规则关联；歧义保持未解决，不调用模型或相似度猜测。"""
from collections import defaultdict
import re
from .source import stable_id, sha
from .structure import HAN, number
from .chunking import new_chunk, sentence_ranges
from .tokens import retrieval_text

REFERENCE = re.compile(rf"第([{HAN}]+)条|前条|前款")
CONDITION = re.compile(r"^(?:符合下列|具备下列|适用范围|本(?:办法|规定|规划|条例).{0,20}适用于|凡.{0,25}[，,]|申请.{0,30}条件|对下列)")
DEFINITION = re.compile(r"^(?:本.{0,25}所称|.{2,20}是指)")
EXCEPTION = re.compile(r"^(?:但[，,]|但是|除.{1,35}外|不适用于|以下情形除外)")


def legal_reference(text, match):
    """“第二条生产线”里的条是量词；只有条文语境才登记依赖。"""
    if match.group(1) is None:
        return True
    before = text[max(0, match.start() - 20):match.start()].rstrip()
    after = text[match.end():match.end() + 20].lstrip()
    legal_before = re.search(r"(?:依照|依据|根据|按照|参照|违反|符合|见|适用|执行|按|本|该|上述|》的?|至|、|及|与|和)\s*$", before)
    legal_after = re.match(rf"(?:规定|所[述列称指]|要求|执行|第[{HAN}]+[款项]|至第|之[{HAN}]+|[、，。；;）)]|和第|及第|与第|的规定|$)", after)
    return bool(legal_before or legal_after)


def build_relations(source, units, structures, parents, config_hash):
    relations = []
    unit_map = {u["id"]: u for u in units}
    parent_by_unit = {uid: p for p in parents for uid in p["unit_ids"]}
    by_number = defaultdict(list)
    articles = [s for s in structures if s["kind"] == "article"]
    structure_map = {s["id"]: s for s in structures}
    for article in articles:
        by_number[article["number"]].append(article)

    def add(kind, parent, trigger, target, target_kind, rule, resolved=True):
        record = {"id": stable_id("relation", source.version, kind, parent["id"], trigger["start"], trigger["end"], target, config_hash),
                  "document_id": source.version, "from_parent_id": parent["id"], "kind": kind,
                  "trigger": trigger, "target_id": target, "target_kind": target_kind,
                  "rule": rule, "status": "resolved" if resolved else "unresolved", "required": True}
        relations.append(record)

    for parent in parents:
        content = [unit_map[uid] for uid in parent["unit_ids"] if unit_map[uid]["kind"] not in ("metadata", "whitespace", "toc", "page_marker", "image", "section")]
        for index, unit in enumerate(content):
            plain = re.sub(r"^\s*#{1,6}\s+", "", unit["text"]).strip()
            # 列表引导语适用于该列表父块的所有项目；其原文作为独立上下文保存。
            kind = None
            if unit["kind"] in ("caption", "note") and any(u["kind"] == "table" for u in content):
                kind = "table_context"
            elif parent["kind"] == "list" and index == 0 and unit["kind"] != "list_item" and plain.endswith(("：", ":")):
                kind = "condition"
            elif CONDITION.search(plain):
                kind = "condition"
            elif DEFINITION.search(plain):
                kind = "definition"
            elif EXCEPTION.search(plain):
                kind = "exception"
            if kind:
                add(kind, parent, source.segment(unit["start"], unit["end"], kind), unit["id"], "unit", "explicit_adjacent_" + kind)
            for match in REFERENCE.finditer(unit["text"]):
                if not legal_reference(unit["text"], match):
                    continue
                absolute = unit["start"] + match.start()
                # 条文自己的首个条号是标签，不是对自身的引用。
                if unit["kind"] == "article" and not unit["text"][:match.start()].strip("# \t\r\n"):
                    continue
                trigger = source.segment(absolute, unit["start"] + match.end(), "reference")
                structure = structure_map[unit["structure_id"]]
                target, target_kind, rule = None, "parent", "unresolved_reference"
                if match.group(1):
                    candidates = by_number[number(match.group(1))]
                    scoped = [s for s in candidates if s["scope_id"] == structure["scope_id"]]
                    if scoped:
                        candidates = scoped
                    # 《外部文件》第八条不能误接到本文件的第八条。
                    prefix = unit["text"][max(0, match.start() - 80):match.start()]
                    external = bool(re.search(r"》(?:的)?\s*$", prefix))
                    if len(candidates) == 1 and not external:
                        target = parent_by_unit.get(candidates[0]["unit_id"], {}).get("id")
                        rule = "unique_article_in_source_scope"
                    elif external:
                        rule = "external_document_reference"
                    else:
                        rule = "missing_or_ambiguous_article"
                elif match.group() == "前条":
                    preceding = [s for s in articles if s["start"] < structure["start"] and s["scope_id"] == structure["scope_id"]]
                    if structure["kind"] == "article" and preceding:
                        target = parent_by_unit.get(preceding[-1]["unit_id"], {}).get("id")
                        rule = "previous_article_same_scope"
                elif match.group() == "前款":
                    # 款对应可确认的自然段；不把“前一句”或章节标题冒充前款。
                    preceding = [u for u in content[:index] if u["kind"] == "paragraph"]
                    if structure["kind"] == "article" and preceding:
                        target, target_kind = preceding[-1]["id"], "unit"
                        rule = "previous_paragraph_same_article"
                add("reference", parent, trigger, target, target_kind, rule, target is not None)
    return relations


def assemble(source, parents, units, chunks, relations, counter, config, config_hash):
    """装配必要上下文后重新计数；明确依赖可递归展开，但不推理新的政策关系。"""
    parents_by_id = {p["id"]: p for p in parents}
    units_by_id = {u["id"]: u for u in units}
    by_parent = defaultdict(list)
    for rel in relations:
        by_parent[rel["from_parent_id"]].append(rel)

    def dependencies(parent_id, visiting):
        if parent_id in visiting:
            return [], [], ["cyclic_required_dependency"]
        contexts, ids, errors = [], [], []
        visiting = visiting | {parent_id}
        for rel in by_parent[parent_id]:
            ids.append(rel["id"])
            if rel["status"] != "resolved":
                errors.append("unresolved_required_dependency"); continue
            if rel["target_kind"] == "unit":
                target = units_by_id[rel["target_id"]]
                contexts.append(source.segment(target["start"], target["end"], rel["kind"]))
            else:
                target = parents_by_id[rel["target_id"]]
                contexts.append(source.segment(target["start"], target["end"], "referenced_article"))
                more, more_ids, more_errors = dependencies(target["id"], visiting)
                contexts.extend(more); ids.extend(more_ids); errors.extend(more_errors)
        return contexts, ids, errors

    resolved = {p["id"]: dependencies(p["id"], set()) for p in parents}
    output = []
    queue = list(chunks)
    while queue:
        chunk = queue.pop(0)
        extra, required, errors = resolved[chunk["parent_id"]]
        contexts = []
        seen = set()
        for item in chunk["context_segments"] + extra:
            key = (item["start"], item["end"])
            if key in seen or any(s["start"] <= item["start"] and s["end"] >= item["end"] for s in chunk["source_segments"]):
                continue
            seen.add(key); contexts.append(item)
        chunk["context_segments"] = contexts
        chunk["required_relation_ids"] = sorted(set(required))
        chunk["reasons"] = sorted(set(chunk["reasons"] + errors))
        value = retrieval_text(source, chunk["section_path"], chunk["source_segments"], contexts)
        length = counter.count(value)
        if length > config["max_input_tokens"] and chunk["kind"] not in ("table", "literal") and not chunk.get("split_for_context") and len(chunk["source_segments"]) == 1:
            segment = chunk["source_segments"][0]
            safe = sentence_ranges(source, segment["start"], segment["end"])
            if len(safe) > 1:
                for item in safe:
                    child = new_chunk(source, parents_by_id[chunk["parent_id"]], [item], chunk["context_segments"], config_hash)
                    child["split_for_context"] = True
                    queue.append(child)
                continue
        if length > config["max_input_tokens"]:
            chunk["reasons"].append("input_over_limit")
        if chunk["reasons"]:
            chunk["status"] = "pending"
        chunk["retrieval_text"] = value
        chunk["embedding_tokens"] = length
        chunk["embedding_input_hash"] = sha(counter.render(value).encode("utf-8"))
        chunk["tokenizer_profile"] = counter.profile_hash
        output.append(chunk)
    return sorted(output, key=lambda c: (c["source_segments"][0]["start"], c["id"]))
