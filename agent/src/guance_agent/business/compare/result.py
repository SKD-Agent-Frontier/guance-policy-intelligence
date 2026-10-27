"""从有效事实组装矩阵；分析判断与原文事实分离，依赖缺失时保守降级。"""
import re

from ...routing import parse_fyps
from ...schemas import Claim
from ..contract import BusinessOutcome

NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
ABSOLUTE_RANK = re.compile(r"最佳|最优|绝对优|全面优于|排名|评分|首选方案|无条件优|更推荐|推荐选择")
ABSENCE = re.compile(r"没有(?:规定|措施|支持|提供)|未规定|不支持|不存在|缺乏|缺少|未提及|未明确|未涉及|未见")
NEGATIVE_SOURCE = re.compile(r"不得|不予|不适用|不包含|不支持|禁止|未规定|没有|缺乏|缺少|未明确")
QUANTITATIVE_ORDER = re.compile(r"更高|高于|低于|多于|更大|更小|更强|更优|增加了|降低了")
PERSONAL_APPROVAL = re.compile(
    r"(?:人才|申请人|个人).{0,24}(?:需|须|必须|应当).{0,24}(?:审议|审批|批准)"
    r"|享受(?:支持|服务).{0,12}需.{0,24}(?:审议|审批|批准)"
    r"|(?:需|必须)通过.{0,16}申报流程")
PROGRESS_ASSERTION = re.compile(
    r"尚未(?:完全)?(?:落地|实施|兑现|完成|建成)|尚未到.{0,8}(?:考核|验收)"
    r"|已(?:经|全部|全面|完全)?(?:兑现|落地|完成|建成)")
LINKED_REQUIREMENT = re.compile(r"相挂钩|相绑定|共同资格|必须同时满足")
UNITS = re.compile(r"\d+(?:\.\d+)?\s*(亿元|万元|万人|千人|万家|元|人|家|项|个|%|％)")
ALIASES = "甲乙丙丁戊己庚辛"


def _numbers_supported(text, source):
    return all(re.search(rf"(?<!\d){re.escape(token)}(?!\d)", source)
               for token in NUMBER.findall(text))


def _nature(obj):
    title = obj.get("title") or ""
    if "政府工作报告" in title or obj.get("fyp") == "政府工作报告":
        return "政府工作报告"
    if "规划" in title or "计划" in title:
        return "规划计划"
    return obj.get("plantype_label")


def _stage(text):
    states = set()
    if re.search(r"已完成|已经|已建成|累计完成|实现了|新增了|过去.{0,6}年", text):
        states.add("完成情况")
    if re.search(r"目标|力争|预期|拟建|计划|到20\d{2}年|将.{0,12}(?:达到|建成)", text):
        states.add("规划安排")
    if re.search(r"在建|正在|加快推进|持续推进", text):
        states.add("推进过程")
    return tuple(sorted(states))


def _comparability(objects, cells, generated):
    reasons = []
    if any(cell["status"] != "found" for cell in cells):
        reasons.append("部分对象未取得该维度的充分证据，无法作完整比较")
    natures = {_nature(obj) for obj in objects}
    if None in natures:
        reasons.append("部分文件性质尚不明确")
    if len(natures - {None}) > 1:
        reasons.append("文件性质不同，只作注明性质的内容对照，不视为同口径政策")
    types = {obj.get("plantype_label") for obj in objects} - {None}
    if len(types) > 1:
        reasons.append("规划或文件类型不同，只对照所列条款，不视为同一层面的政策安排")
    periods = {obj.get("fyp") for obj in objects}
    if None in periods:
        reasons.append("部分对象规划期或报告期缺失，不能确认相同时间口径")
    if len(periods - {None}) > 1:
        reasons.append("规划期或报告期不同，不作为同一年度目标或完成值比较")
    for obj in objects:
        title_periods = parse_fyps(obj.get("title") or "")
        if title_periods and obj.get("fyp") not in title_periods:
            reasons.append("标题中的规划期与元数据不一致，不自行纠正或视为相同口径")
    quote_sets = ["\n".join(cell.get("quotes", [])) for cell in cells]
    stages = {_stage(text) or (("规划安排",) if _nature(obj) == "规划计划" else ())
              for obj, text in zip(objects, quote_sets) if text}
    if len(stages) > 1 or any(len(stage) > 1 for stage in stages):
        reasons.append("规划安排、推进过程或实际完成情况存在差别，不能混为一谈")
    unit_sets = {tuple(sorted(set(UNITS.findall(text)))) for text in quote_sets if text}
    if len(unit_sets) > 1:
        reasons.append("数量单位或指标表现不同，未作换算或直接大小排序")
    if generated and not generated.comparable and not reasons:
        # 不把模型另写的经济基础、实施进展等说明当成已取证的口径事实。
        reasons.append("原文不足以确认统一统计口径，仅作所列条款的文字内容对照")
    numeric = any(NUMBER.search(cell.get("text", "")) for cell in cells)
    if numeric:
        # 数字出现只证明字面有据，不能证明统计定义相同。
        reasons.append("数值仅列示原文；未独立核实同一统计定义，不作数值优劣排序")
    return {"comparable": not reasons,
            "reason": "；".join(dict.fromkeys(part for reason in reasons
                                            for part in reason.split("；") if part))
            or "可在所列维度作有据的文字内容对照"}


def _judgment(judgment, cells, *, comparable=True):
    """校验判断依赖，拒绝新增无据数字、绝对排名和由检索缺失推出的否定事实。"""
    ids = list(dict.fromkeys(judgment.supporting_cells))
    if not ids or any(cid not in cells or cells[cid]["status"] != "found" for cid in ids):
        return None
    text = judgment.text.strip()
    source = "\n".join(cells[cid]["text"] + "\n" + "\n".join(cells[cid]["quotes"])
                       for cid in ids)
    fact_text = "\n".join(cells[cid]["text"] for cid in ids)
    if not text or ABSOLUTE_RANK.search(text) or not _numbers_supported(text, fact_text):
        return None
    if ABSENCE.search(text) and not NEGATIVE_SOURCE.search(source):
        return None
    if (PERSONAL_APPROVAL.search(text) and re.search(r"各地各部门|各部门各单位|责任单位", source)
            and not re.search(r"申请|申报", source)):
        return None
    if any(match.group() not in source for match in PROGRESS_ASSERTION.finditer(text)):
        return None
    if any(match.group() not in source for match in LINKED_REQUIREMENT.finditer(text)):
        return None
    if not comparable and QUANTITATIVE_ORDER.search(text):
        return None
    if (re.search(r"相比|相较|优于|高于|低于|更适合|更有利|更具", text)
            and len({cells[cid]["planid"] for cid in ids}) < 2):
        return None
    return {"text": text, "supporting_cells": ids, "kind": "analysis"}


def _fact_cells(task, plan, collection, generated):
    units = {unit.id: unit for unit in collection.units}
    facts, claims, cells = {}, [], {}
    for fact in generated.facts:
        if fact.planid not in task.targets or fact.dimension not in plan.dimensions:
            continue
        refs = list(dict.fromkeys(fact.evidence_ids))
        if not refs or any(ref not in units or units[ref].planid != fact.planid for ref in refs):
            continue
        historical_allowed = bool(re.match(r"历史|回顾|完成情况|成效|发展基础", fact.dimension))
        if any(units[ref].historical for ref in refs) and not historical_allowed:
            if "历史背景原文未作为本期政策事实使用。" not in collection.notes:
                collection.notes.append("历史背景原文未作为本期政策事实使用。")
            continue
        # 事实正文只由所选原句生成。模型即使另写text，也不进入事实、引文或最终回答。
        for ref in refs:
            facts.setdefault((fact.dimension, fact.planid), []).append(
                (fact.model_copy(update={"text": units[ref].text}), [ref]))
    for index, dimension in enumerate(plan.dimensions):
        for pid in task.targets:
            cid = f"d{index}:{pid}"
            accepted = facts.get((dimension, pid), [])
            cell = {"cell_id": cid, "planid": pid, "status": "not_retrieved"}
            if accepted:
                refs = list(dict.fromkeys(ref for _, values in accepted for ref in values))
                cell.update(status="found", text="；".join(dict.fromkeys(
                    fact.text.strip() for fact, _ in accepted)),
                    chunk_ids=list(dict.fromkeys(units[ref].chunk_id for ref in refs)),
                    quotes=list(dict.fromkeys(fact.text for fact, _ in accepted)))
                cell["source_contexts"] = list(dict.fromkeys(units[ref].section_path
                                                             for ref in refs if units[ref].section_path))
                claims.append(Claim(text=cell["text"], chunk_ids=cell["chunk_ids"],
                                    quotes=cell["quotes"], dimension=dimension))
            elif any(unit.planid == pid for unit in collection.units):
                cell.update(status="insufficient", reason="已读取部分原文，但未形成该维度的充分证据")
            else:
                cell["reason"] = "本轮未取得相关正文，不能推断政策没有该项规定"
            cells[cid] = cell
    return cells, claims


def _render(task, plan, matrix, schemes):
    """正文使用对象别名；完整文献身份由出处层展示，避免身份数字冒充政策结论。"""
    allowed = "\n".join(cell.get("text", "") for row in matrix for cell in row["cells"])
    allowed += "\n" + task.question
    headers = ["比较维度"] + [f"政策{ALIASES[i]}" for i in range(len(task.targets))] + ["主要异同"]
    lines = ["各政策按输入顺序排列，完整篇名和原文见引用出处。", "",
             "| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in matrix:
        label = row["dimension"] if _numbers_supported(row["dimension"], allowed) else "用户指定维度"
        # 表格分隔符和换行必须转义，不能让政策文本破坏矩阵布局。
        values = [label] + [cell.get("text") or "证据不足，无法判断" for cell in row["cells"]]
        values.append(row["difference"]["text"])
        lines.append("| " + " | ".join(value.replace("|", "\\|").replace("\n", " ")
                                       for value in values) + " |")
        if not row["comparability"]["comparable"]:
            lines.append("\n口径说明：" + row["comparability"]["reason"])
    for analysis in schemes:
        alias = ALIASES[task.targets.index(analysis["planid"])]
        lines.append(f"\n政策{alias}的方案分析（分析判断）：")
        for field, label in (("advantages", "优势"), ("limitations", "限制"),
                             ("applicability", "适用条件")):
            items = analysis[field]
            lines.append(label + "：" + ("；".join(item["text"] for item in items)
                                        if items else "现有证据不足以形成有依据的判断"))
    return "\n".join(lines)


def build_outcome(task, plan, collection, generated, *, scheme):
    cells, claims = _fact_cells(task, plan, collection, generated)
    differences = {item.dimension: item for item in generated.differences}
    matrix = []
    for index, dimension in enumerate(plan.dimensions):
        row_cells = [cells[f"d{index}:{pid}"] for pid in task.targets]
        proposed = differences.get(dimension)
        comparable = _comparability(collection.objects, row_cells, proposed)
        judgment = _judgment(proposed, cells, comparable=comparable["comparable"]) if proposed else None
        # 行级差异必须覆盖每个对象、且只能引用本维度单元格。
        required = {cell["cell_id"] for cell in row_cells}
        if judgment and set(judgment["supporting_cells"]) != required:
            judgment = None
        difference = (judgment | {"status": "supported"} if judgment else {
            "text": "该维度证据或分析依据不足，无法判断主要异同。",
            "supporting_cells": [], "kind": "analysis", "status": "insufficient"})
        matrix.append({"dimension": dimension, "cells": row_cells,
                       "difference": difference, "comparability": comparable})
    schemes = []
    proposals = {item.planid: item for item in generated.scheme_analysis}
    if scheme:
        for pid in task.targets:
            analysis = {"planid": pid}
            proposal = proposals.get(pid)
            for field in ("advantages", "limitations", "applicability"):
                accepted = []
                for item in getattr(proposal, field, []) if proposal else []:
                    judgment = _judgment(item, cells, comparable=all(
                        row["comparability"]["comparable"] for row in matrix))
                    if judgment and any(cells[cid]["planid"] == pid
                                        for cid in judgment["supporting_cells"]):
                        accepted.append(judgment)
                analysis[field] = accepted
            schemes.append(analysis)
    incomplete = any(cell["status"] != "found" for cell in cells.values())
    incomplete |= any(row["difference"]["status"] != "supported" for row in matrix)
    if scheme:
        incomplete |= any(not analysis[field] for analysis in schemes
                          for field in ("advantages", "limitations", "applicability"))
    structured = {"objects": collection.objects, "dimensions": plan.dimensions,
                  "matrix": matrix, "comparability": {
                      "comparable": all(row["comparability"]["comparable"] for row in matrix),
                      "reason": "；".join(dict.fromkeys(row["comparability"]["reason"]
                                                       for row in matrix))},
                  "coverage_notes": collection.notes}
    if scheme:
        structured.update(scheme_analysis=schemes,
                          evaluation_criteria=plan.evaluation_criteria)
    return BusinessOutcome(answer=_render(task, plan, matrix, schemes), claims=claims,
                           structured=structured, insufficient=bool(incomplete))
