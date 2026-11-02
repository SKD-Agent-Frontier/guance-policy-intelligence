"""Non-factual policy-agent boundaries: do not force every prompt into RAG."""
from __future__ import annotations

import re

from .routing import current_question


def boundary_reply(question: str) -> str | None:
    """Answer only unambiguous non-retrieval requests; never claim authority."""
    q = current_question(question).strip()
    if not q:
        return None

    # A request for the assistant's capabilities is not a request for a policy
    # quote. Explain scope instead of claiming no evidence was retrieved.
    if (re.search(r"(?:你|系统|助手).{0,8}(?:能做什么|能力范围|有什么能力|职责边界)", q)
            or re.search(r"(?:介绍|说明).{0,5}(?:你的|系统的|助手的)(?:能力|边界)", q)):
        return (
            "我可以检索政策文献、核对原文出处、提取指标、梳理政策部署，"
            "以及在证据充分时进行多文献比较与分析。"
            "未收录、无法核验的信息会明确说明；我不能替代主管部门"
            "出具正式审核意见，也不能保证所有问题都有足够材料。"
        )

    # Do not impersonate a decision-maker or manufacture an official approval.
    # Leave ordinary source-grounded evaluations to the normal research route.
    attestation = re.search(
        r"经评审认定|已经达到行业领先|已达到行业领先|建议予以通过|"
        r"建议通过|正式结论|出具.*(?:认定|意见)", q
    )
    institution = re.search(
        r"以我单位名义|代表我单位|以.*(?:部门|单位|政府)名义|"
        r"领导要求|公章|后续手续|不受限制|直接.*(?:认定|结论)", q
    )
    if attestation and institution:
        return (
            "我不能在缺少真实评审依据和授权的情况下，"
            "代替单位或审批机关认定项目已达到行业领先水平或建议通过。"
            "可以根据已核验的材料起草事实说明，明确待评审事项，"
            "但正式结论须由有权限的机构完成审查后作出。"
        )

    if re.search(r"(?:扮演|假装你是|以.*身份).{0,24}(?:科长|局长|官员|领导)", q):
        if not re.search(r"规划|政策|依据|材料|数据|指标|项目|具体", q):
            return ("可以采用更正式的政策分析口吻交流，"
                    "但不会冒充真实公职人员或替主管部门作出正式决定。"
                    "具体政策事实仍以可核验的文献为准。")

    if (re.search(r"(?:作为|假如你是|以).{0,8}(?:科长|局长|官员|领导)", q)
            and re.search(r"下一步|重点抓|应该抓|怎么决策|给个决定", q)
            and not re.search(r"规划|政策|原文|依据|材料|数据|指标", q)):
        return ("我不能以主管部门身份直接决定工作重点。"
                "如果明确地区、时期和相关规划，我可以依据原文梳理"
                "候选方向、政策依据与需要核实的条件，供研判参考。")
    return None
