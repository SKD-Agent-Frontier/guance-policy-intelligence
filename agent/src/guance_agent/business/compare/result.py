"""将本轮证据与模型判断转换为业务结果。

数据方向固定为 EvidenceUnit → 原文单元格/Claim → 有依据的分析判断 → BusinessOutcome。
模型不能反向改写原文事实。这里执行编号、依赖与部分字面风险检查；基础层随后才做
已读范围内的逐字引文与数字核验。因此本模块的 found/supported 不是语义正确性保证。
"""
import re

from ...routing import parse_fyps
from ...schemas import Claim
from ..contract import BusinessOutcome
from .relevance import evidence_on_topic, explicit_comparison_topic

# 这些表达式是有限的字面拦截规则。它们不能覆盖所有换算、反事实、语义否定或
# 隐含条件；命中规则时保守撤回分析，未命中也不等于已经证明分析得到原文支持。
NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
ABSOLUTE_RANK = re.compile(r"最佳|最优|绝对优|全面优于|排名|评分|首选方案|无条件优|更推荐|推荐选择")
ABSENCE = re.compile(r"没有(?:规定|措施|支持|提供)|未规定|不支持|不存在|缺乏|缺少|未提及|未明确|未涉及|未见")
NEGATIVE_SOURCE = re.compile(r"不得|不予|不适用|不包含|不支持|禁止|未规定|没有|缺乏|缺少|未明确")
QUANTITATIVE_ORDER = re.compile(r"更高|高于|低于|多于|更大|更小|更强|更优|增加了|降低了")
PERSONAL_APPROVAL = re.compile(
    r"(?:人才|申请人|个人).{0,32}(?:需|须|必须|应当|依赖|取决于).{0,24}(?:审议|审批|批准|考核|评估)"
    r"|享受(?:支持|服务).{0,12}需.{0,24}(?:审议|审批|批准)"
    r"|(?:需|必须)通过.{0,16}申报流程")
PROGRESS_ASSERTION = re.compile(
    r"尚未(?:完全)?(?:落地|实施|兑现|完成|建成)|尚未到.{0,8}(?:考核|验收)"
    r"|已(?:经|全部|全面|完全)?(?:兑现|落地|完成|建成)")
LINKED_REQUIREMENT = re.compile(r"相挂钩|相绑定|共同资格|必须同时满足")
FUND_CREATION = re.compile(r"(?:设立|新设|新建).{0,24}基金")
EXPANDED_ROLE = re.compile(r"(?:项目|团队)(?:负责人|成员)")
TALENT_GROUP = re.compile(r"高层次人才|高技能人才|生产一线岗位")
SUPPORT_FEATURE = re.compile(r"支持|补贴|补助|资助|奖励|全周期|培训|培养|引进")
FULL_DOCUMENT_ASSERTION = re.compile(
    r"(?:整份政策|政策全文|所有条款|全部措施).{0,8}(?:仅|只|没有|均|都|完全|全部|覆盖)"
    r"|(?:唯一|全部)(?:适用对象|受益对象|支持措施)")
UNITS = re.compile(r"\d+(?:\.\d+)?\s*(亿元|万元|万人|千人|万家|元|人|家|项|个|%|％)")
ALIASES = "甲乙丙丁戊己庚辛"
GAP_MESSAGES = {
    "source_evidence_missing": "相关原文证据不足，不能据此判断政策没有规定",
    "analysis_not_generated": "本轮未生成可交付分析，不能据此推断政策存在缺陷",
    "invalid_dependencies": "分析引用了缺失或无效的事实单元格",
    "invalid_row_dependencies": "维度异同未完整引用本行各对象的事实",
    "missing_own_policy": "该方案分析缺少本方案自身的事实依据",
    "empty_analysis": "分析内容为空",
    "absolute_ranking": "分析包含无条件排名或推荐",
    "unsupported_number": "分析中的数字没有在所引用事实中出现",
    "unsupported_absence": "不能从局部摘录推断政策没有措施或规定",
    "unsupported_personal_condition": "不能把政府工作考核或部门安排转化为个人资格条件",
    "unsupported_progress": "分析中的实施进度未获原文支持",
    "unsupported_linking": "分析把不同条款绑定为原文未说明的共同条件",
    "unsupported_fund_creation": "用好或使用基金不等于新设基金，不能改写政策动作",
    "unsupported_personal_role": "不能把支持项目或团队扩写为原文未说明的个人身份",
    "unsupported_project_threshold": "一人一策的重大产业化项目门槛不能扩展到所有创业团队",
    "unsupported_mandatory_recommendation": "实行专家举荐制度不等于每位人才必须通过举荐",
    "unsupported_support_subject": "创业团队的全周期服务不能改写为所有创业项目的支持资格",
    "full_document_inference": "有限摘录不能支撑对整份政策或全部对象的排他性结论",
    "multiple_limit_scopes": "每项限制须依据本方案的一条完整条款，不合并不同单元格的条件",
    "foreign_applicability_scope": "适用性只核对本方案的原文条件",
    "unsupported_staffing_scope": "岗位安排不能改写为原文未说明的编制口径",
    "incomparable_quantitative_order": "口径受限时不能作数值优劣判断",
    "missing_counterpart": "相对比较缺少另一对象的事实依据",
}


def _numbers_supported(text, source):
    """检查阿拉伯数字 token 是否出现在当前依据内，不做单位换算或统计定义核验。

    数字两侧不能紧接其他数字，避免把“10”从“110”中宽松匹配出来；诸如人次、
    人年、人数之间的含义仍须依赖原文上下文和分析复核，不能由这个函数证明。
    """
    return all(re.search(rf"(?<!\d){re.escape(token)}(?!\d)", source)
               for token in NUMBER.findall(text))


def _reward_scope_supported(text, source):
    """保守拦截已复现的奖励条款群体合并，不推断整份政策的受益资格。

    原文的收入倾斜与单独制定奖励政策可能分属不同群体；即使引用同一单元格，
    也不能把前句的所有对象带入后句。这里只识别有限的显式群体和动作，不声称
    覆盖全部政策条件关系。分句后分别概括两条规则仍可保留。
    """
    action = "单独制定"
    source_clauses = [part for part in re.split(r"[。；]", source) if action in part]
    if action in text and not source_clauses:
        return False
    allowed = {group for part in source_clauses for group in TALENT_GROUP.findall(part)}
    for part in re.split(r"[。；]", text):
        if action not in part:
            continue
        before, after = part.split(action, 1)
        prefixes = before.split("，")
        local = prefixes[-1]
        if not TALENT_GROUP.search(local) and len(prefixes) > 1:
            local = prefixes[-2] + "，" + local
        # 动作后的新并列分句不继承这一动作。否则“高层次人才可单独制定政策，
        # 并规定高技能人才收入倾斜”会因后句的人群而被误撤回。
        local += action + after.split("，", 1)[0]
        if not set(TALENT_GROUP.findall(local)) <= allowed:
            return False
    return True


def _nature(obj):
    """从已取得的身份元数据判断粗粒度文件性质，只用于说明比较限制。"""
    title = obj.get("title") or ""
    if "政府工作报告" in title or obj.get("fyp") == "政府工作报告":
        return "政府工作报告"
    if "规划" in title or "计划" in title:
        return "规划计划"
    return obj.get("plantype_label")


def _stage(text):
    """识别原句中的规划、推进、完成措辞，允许一段话同时包含多个状态。

    这是文本口径提示，不根据当前日期推断某个规划目标是否已经实现，也不回写语料。
    """
    states = set()
    if re.search(r"已完成|已经|已建成|累计完成|实现了|新增了|过去.{0,6}年", text):
        states.add("完成情况")
    if re.search(r"目标|力争|预期|拟建|计划|到20\d{2}年|将.{0,12}(?:达到|建成)", text):
        states.add("规划安排")
    # “在建筑行业/在建立制度/在建成的平台上”等不是“在建”项目状态。
    # 只排除构成另一词语的常见后缀，保留“在建项目”“项目在建”等显式状态；
    # 这仍是措辞识别，无法仅凭命中断言项目在现实中已经进入施工阶段。
    if re.search(r"在建(?![筑材设立成言议模制党安])|正在|加快推进|持续推进", text):
        states.add("推进过程")
    return tuple(sorted(states))


def _comparability(objects, cells, generated):
    """返回本维度的可比状态及保守说明，objects 与 cells 必须按同一对象顺序排列。

    comparable=False 表示不能视为已确认的同口径比较；仍允许有据的文字异同。
    检查身份、规划期、文件类型、实施措辞和计量单位；有数字时始终提示未独立核实
    统计定义，防止“数字能回贴”被误解为“可以比大小”。模型只能增加保守限制。
    """
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
        # 标题与元数据冲突只提示，不自行选择一方修复源数据。
        title_periods = parse_fyps(obj.get("title") or "")
        if title_periods and obj.get("fyp") not in title_periods:
            reasons.append("标题中的规划期与元数据不一致，不自行纠正或视为相同口径")
    quote_sets = ["\n".join(cell.get("quotes", [])) for cell in cells]
    # 规划中的未标记条款沿用规划安排语境，避免把“未命中状态词”误称为实际完成值。
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


def _judgment(judgment, cells, *, comparable=True, rejected=None):
    """校验一项分析，返回可交付字典；触发约束或缺少依赖时返回 None。

    依赖检查是硬约束：任何一个单元格不存在或非 found，整项判断撤回，不偷偷
    删去部分引用后保留原判断。字面规则再拦截绝对排名、无据数字、部分无据否定、
    进度和条件绑定措辞。剩余语义仍需模型复核及业务审阅，不能标记为逐字事实。
    """
    def reject(reason):
        # 只记录原因，不把已拒绝的判断文本混入可交付结构。
        if rejected is not None:
            rejected.append(reason)
        return None

    ids = list(dict.fromkeys(judgment.supporting_cells))
    if not ids or any(cid not in cells or cells[cid]["status"] != "found" for cid in ids):
        return reject("invalid_dependencies")
    text = judgment.text.strip()
    # 只能在这项判断自己声明的依据内找数字和措辞，不能借全矩阵其他事实来背书。
    source = "\n".join(cells[cid]["text"] + "\n" + "\n".join(cells[cid]["quotes"])
                       for cid in ids)
    fact_text = "\n".join(cells[cid]["text"] for cid in ids)
    if not text:
        return reject("empty_analysis")
    if ABSOLUTE_RANK.search(text):
        return reject("absolute_ranking")
    if FULL_DOCUMENT_ASSERTION.search(text):
        return reject("full_document_inference")
    if "编制" in text and "编制" not in source:
        return reject("unsupported_staffing_scope")
    if not _numbers_supported(text, fact_text):
        return reject("unsupported_number")
    if ABSENCE.search(text) and not NEGATIVE_SOURCE.search(source):
        return reject("unsupported_absence")
    # 人才工作年度考核考的是部门工作，并不自动约束每位受益人。若原句另有明确
    # 个人考核条款则不因出现部门用语一并否定；具体条件关系仍交复核和业务审阅。
    government_process = re.search(r"各地各部门|各部门各单位|责任单位|人才工作考核", source)
    personal_process = re.search(
        r"(?:申请人|个人|受助人才|入选人才|获评人才).{0,16}(?:需|须|必须|应当).{0,20}(?:考核|评估|审批|审议)", source)
    if (PERSONAL_APPROVAL.search(text) and government_process and not personal_process
            and ("人才工作考核" in source or not re.search(r"申请|申报", source))):
        return reject("unsupported_personal_condition")
    for match in PROGRESS_ASSERTION.finditer(text):
        # “不能认定已完成”是在说明证据边界；“尚未完成”则仍是实际进度断言。
        # 仅豁免紧邻进度词的认识限制，不用整句含“不/未”就放过后续实际进度。
        prefix = text[:match.start()].rstrip('“"‘ ')
        caution = re.search(
            r"(?:(?:不能|不可|无法|不应|不得|不足以)(?:据此|因此)?(?:直接)?"
            r"(?:认定|视为|证明|说明|判断|声称|推断|宣称)|不代表|不等于)$", prefix)
        if match.group() not in source and not caution:
            return reject("unsupported_progress")
    if any(match.group() not in source for match in LINKED_REQUIREMENT.finditer(text)):
        return reject("unsupported_linking")
    if not _reward_scope_supported(text, source):
        return reject("unsupported_linking")
    if FUND_CREATION.search(text) and not FUND_CREATION.search(source):
        return reject("unsupported_fund_creation")
    if any(match.group() not in source for match in EXPANDED_ROLE.finditer(text)):
        return reject("unsupported_personal_role")
    if "一人一策" in text and "重大产业化" in source:
        if any("重大" not in part for part in re.split(r"[。；]", text) if "一人一策" in part):
            return reject("unsupported_project_threshold")
    mandatory_recommendation = r"(?<!无)(?:需|须|必须).{0,8}(?:专家)?(?:举荐|推荐)"
    if re.search(mandatory_recommendation, text) and not re.search(mandatory_recommendation, source):
        return reject("unsupported_mandatory_recommendation")
    if ("创业团队" in source and "全周期" in source
            and re.search(r"(?:为|对)[^。；，]{0,32}项目[^。；，]{0,16}(?:提供|给予)[^。；]*全周期", text)):
        return reject("unsupported_support_subject")
    if not comparable and QUANTITATIVE_ORDER.search(text):
        return reject("incomparable_quantitative_order")
    # “更适合/相比”等相对结论要有至少两份政策的依据；只引本方案不能比较对方。
    if (re.search(r"相比|相较|优于|高于|低于|更适合|更有利|更具", text)
            and len({cells[cid]["planid"] for cid in ids}) < 2):
        return reject("missing_counterpart")
    # 片段分析的范围由程序写进交付文字与机器字段，不能只寄希望于模型遵守提示。
    # 这只缩小“侧重/仅涉及”等描述的覆盖范围，不豁免任何主体、条件或动作校验。
    prefix = "就本次所列条款而言，"
    return {"text": text if text.startswith(prefix) else prefix + text,
            "supporting_cells": ids, "kind": "analysis", "scope": "supporting_cells_only"}


def _fact_cells(task, plan, collection, generated):
    """生成所有“维度 × 对象”单元格，并为 found 单元格创建共享 Claim。

    返回 (cells, claims)，cells 以 d{维度下标}:{planid} 为键。证据选择响应中的
    text 始终被忽略，只有属于当前政策、当前 Collection 的原句编号可产生事实。
    这保证矩阵显示文字、Claim 正文、quotes 和 chunk_ids 来自同一次组装过程。
    """
    units = {unit.id: unit for unit in collection.units}
    topic = explicit_comparison_topic(task.question)
    titles = {obj.get("planid"): obj.get("title") or "" for obj in collection.objects}
    facts, claims, cells = {}, [], {}
    for fact in generated.facts:
        if fact.planid not in task.targets or fact.dimension not in plan.dimensions:
            continue
        refs = list(dict.fromkeys(fact.evidence_ids))
        # 一项选择夹带未知或跨对象编号时整体拒绝，避免留下丢失关键上下文的半项事实。
        if not refs or any(ref not in units or units[ref].planid != fact.planid for ref in refs):
            continue
        # 只依据维度本身判断是否允许历史事实；不扫描整句问题，防止“不要把历史成绩
        # 当目标”这样的否定要求反而因含“历史”二字而放开背景引证。
        historical_allowed = bool(re.match(r"历史|回顾|完成情况|成效|发展基础", fact.dimension))
        if any(units[ref].historical for ref in refs) and not historical_allowed:
            if "历史背景原文未作为本期政策事实使用。" not in collection.notes:
                collection.notes.append("历史背景原文未作为本期政策事实使用。")
            continue
        # 一格只保留一条完整语义单元。培养期句已在取证时与其对象合并为同一单元，
        # 不拆掉必要上下文；不同单元的对象和条件不再先拼到同一事实格后让模型重组。
        if (fact.dimension, fact.planid) in facts:
            continue
        refs = refs[:1]
        # 默认“适用对象”指条款的受益或受规制对象，部门工作分解并不能回答
        # 谁可获得支持。仅取得纯行政分工时保留缺证；“执行主体”维度不受此限制。
        selected = "\n".join(units[ref].text for ref in refs)
        if topic and not all(evidence_on_topic(
            units[ref], fact.dimension, topic, titles.get(fact.planid, "")
        ) for ref in refs):
            collection.notes.append(
                f"对象{fact.planid}的「{fact.dimension}」候选句未通过研究主题与维度检查，已排除。"
            )
            continue
        if (fact.dimension == "适用对象"
                and re.search(r"任务清单|工作台账|牵头抓总|人才工作考核", selected)
                and not re.search(r"支持|给予|补贴|资助|引进|遴选|评选|培养|企业|群众|居民", selected)):
            continue
        # 事实正文只由所选原句生成。模型即使另写text，也不进入事实、引文或最终回答。
        for ref in refs:
            facts.setdefault((fact.dimension, fact.planid), []).append(
                (fact.model_copy(update={"text": units[ref].text}), [ref]))
    # 所有请求位置都要保留，缺证时不能删行/删对象，让消费者误以为已完成完整比较。
    for index, dimension in enumerate(plan.dimensions):
        for pid in task.targets:
            cid = f"d{index}:{pid}"
            accepted = facts.get((dimension, pid), [])
            cell = {"cell_id": cid, "planid": pid, "status": "not_retrieved"}
            if accepted:
                # 去重保持首次出现顺序。一个 Claim 对应一个单元格，多个原句以
                # 分号连接；并不把不同原句拼成一条伪造的原文 quote。
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
                # 此对象读到过正文，但没有通过本维度检查的事实，和“没读到正文”分开。
                cell.update(status="insufficient", reason="已读取部分原文，但未形成该维度的充分证据")
            else:
                cell["reason"] = "本轮未取得相关正文，不能推断政策没有该项规定"
            cells[cid] = cell
    return cells, claims


def _render(task, plan, matrix, schemes, gaps):
    """把同一份已组装矩阵渲染成可读答案，不再调用模型或引入新的事实。

    对象按输入顺序使用甲乙等别名；完整篇名和来源仍在 structured/出处层。
    这样标题年份、planid 等身份数字不会混入答案成为未绑定政策结论的数字。
    """
    allowed = "\n".join(cell.get("text", "") for row in matrix for cell in row["cells"])
    allowed += "\n" + task.question
    headers = ["比较维度"] + [f"政策{ALIASES[i]}" for i in range(len(task.targets))] + ["主要异同"]
    lines = ["各政策按输入顺序排列，完整篇名和原文见引用出处。", "",
             "| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in matrix:
        # 显式维度仍原样保存在 structured；这里只避免一个未绑定的标签数字
        # 触发基础层答案数字核验，导致整份业务结构被清空。
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
            reasons = list(dict.fromkeys(gap["message"] for gap in gaps
                                         if gap.get("planid") == analysis["planid"]
                                         and gap["field"] == field))
            lines.append(label + "：" + ("；".join(item["text"] for item in items)
                                        if items else "；".join(reasons)
                                        or "现有证据不足以形成有依据的判断"))
    return "\n".join(lines)


def build_outcome(task, plan, collection, generated, *, scheme):
    """在事实和判断之间建立依赖，再返回基础层能统一核验的 BusinessOutcome。

    先确定每格事实，再检查逐行异同，最后检查每方案的三类分析。比较口径受限
    不等于证据不足：只有事实/判断缺失或被撤回才设置 insufficient。
    不构造 RunResult 或 EvidenceRef；基础层用 Claim 的 chunk_ids 查本轮证据登记。
    """
    cells, claims = _fact_cells(task, plan, collection, generated)
    gaps = []

    def gap(field, reason, **identity):
        entry = {"field": field, "reason": reason, "message": GAP_MESSAGES[reason], **identity}
        if entry not in gaps:
            gaps.append(entry)

    differences = {item.dimension: item for item in generated.differences}
    matrix = []
    for index, dimension in enumerate(plan.dimensions):
        row_cells = [cells[f"d{index}:{pid}"] for pid in task.targets]
        proposed = differences.get(dimension)
        comparable = _comparability(collection.objects, row_cells, proposed)
        rejected = []
        judgment = _judgment(proposed, cells, comparable=comparable["comparable"],
                             rejected=rejected) if proposed else None
        # 行级差异必须覆盖每个对象、且只能引用本维度单元格。
        # 即使其他维度有充分证据，也不能拿来填补本维度缺失的一侧。
        required = {cell["cell_id"] for cell in row_cells}
        if judgment and set(judgment["supporting_cells"]) != required:
            judgment = None
            rejected.append("invalid_row_dependencies")
        if not judgment:
            reason = ("source_evidence_missing" if any(cell["status"] != "found" for cell in row_cells)
                      else rejected[0] if rejected else "analysis_not_generated")
            gap("difference", reason, dimension=dimension)
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
                    rejected = []
                    judgment = _judgment(item, cells, comparable=all(
                        row["comparability"]["comparable"] for row in matrix), rejected=rejected)
                    # 别人的事实不能单独支撑本方案的优势/限制；相对判断的另一侧
                    # 依据则由 _judgment 额外要求，二者同时成立才保留。
                    if judgment and any(cells[cid]["planid"] == pid
                                        for cid in judgment["supporting_cells"]):
                        ids = judgment["supporting_cells"]
                        if field == "limitations" and (len(ids) != 1 or cells[ids[0]]["planid"] != pid):
                            rejected.append("multiple_limit_scopes")
                        elif field == "applicability" and any(cells[cid]["planid"] != pid for cid in ids):
                            rejected.append("foreign_applicability_scope")
                        else:
                            if field == "applicability":
                                # 个人适用条件不交给模型自由改写。按各自原句分别核对，
                                # 既保留首次购房/在区工作等限定，也不合并为共同资格。
                                quotes = list(dict.fromkeys(q for cid in ids for q in cells[cid]["quotes"]))
                                judgment = judgment | {
                                    "text": "就本次所列条款而言，适用对象及条件须分别按以下原文核对，不合并为共同资格："
                                            + "；".join("“" + q + "”" for q in quotes),
                                    "derivation": "extractive_conditions"}
                            accepted.append(judgment)
                    elif judgment:
                        rejected.append("missing_own_policy")
                    for reason in rejected:
                        gap(field, reason, planid=pid)
                if not accepted:
                    if not any(c["planid"] == pid and c["status"] == "found" for c in cells.values()):
                        gap(field, "source_evidence_missing", planid=pid)
                    elif not any(g.get("planid") == pid and g["field"] == field for g in gaps):
                        gap(field, "analysis_not_generated", planid=pid)
                # 优势的自由概括遗漏或混淆服务主体时，可保留已明确列出的支持特征。
                # 这不是填造优越性：只复制一个本方案的有据支持条款，不推断效果、
                # 兑现或相对排名。缺正文、无明确支持内容或其他风险判断仍不补填。
                reasons = {g["reason"] for g in gaps if g.get("planid") == pid and g["field"] == field}
                if (field == "advantages" and not accepted
                        and reasons <= {"analysis_not_generated", "unsupported_support_subject"}):
                    preferred = sorted((cid for cid, cell in cells.items()
                                        if cell["planid"] == pid and cell["status"] == "found"
                                        and SUPPORT_FEATURE.search(cell["text"])),
                                       key=lambda cid: (plan.dimensions[int(cid.split(":", 1)[0][1:])]
                                                        != "支持措施", cid))
                    if preferred:
                        cid = preferred[0]
                        accepted.append({"text": "就本次所列条款而言，可供评估的支持特征如下，具体对象和条件保留原文："
                                         + "；".join("“" + q + "”" for q in cells[cid]["quotes"]),
                                         "supporting_cells": [cid], "kind": "analysis",
                                         "scope": "supporting_cells_only", "derivation": "extractive_support_features"})
                analysis[field] = accepted
            schemes.append(analysis)
    # “运行 completed”与“证据完整”分开；缺一格、缺一项必要分析均标注不足。
    # 结构仍保留已形成的事实，最终是否因引文核验而整体清空由基础层决定。
    incomplete = any(cell["status"] != "found" for cell in cells.values())
    incomplete |= any(row["difference"]["status"] != "supported" for row in matrix)
    if scheme:
        incomplete |= any(not analysis[field] for analysis in schemes
                          for field in ("advantages", "limitations", "applicability"))
    supported_dimensions = [row["dimension"] for row in matrix
                            if row["difference"]["status"] == "supported"]
    task_completion = {
        "status": "complete" if not incomplete else "partial",
        "requested_dimensions": plan.dimensions,
        "supported_dimensions": supported_dimensions,
        "unsupported_dimensions": [row["dimension"] for row in matrix
                                   if row["dimension"] not in supported_dimensions],
    }
    structured = {"objects": collection.objects, "dimensions": plan.dimensions,
                  "task_completion": task_completion,
                  "matrix": matrix, "comparability": {
                      "comparable": all(row["comparability"]["comparable"] for row in matrix),
                      "reason": "；".join(dict.fromkeys(row["comparability"]["reason"]
                                                       for row in matrix))},
                  "coverage_notes": collection.notes, "analysis_gaps": gaps}
    if scheme:
        structured.update(scheme_analysis=schemes,
                          evaluation_criteria=plan.evaluation_criteria)
    return BusinessOutcome(answer=_render(task, plan, matrix, schemes, gaps), claims=claims,
                           structured=structured, insufficient=bool(incomplete))
