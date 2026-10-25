"""要素提取：检索与读取由程序完成，模型只做一次语义提取。

为什么不是让模型自己调工具：
校内端点高峰单次模型调用约 50 秒，而赛题要求端到端 180 秒。
Agent 多轮往返（检索→读目录→逐节读→出结果）轻松超过 3 次调用就必然超时。
把确定性步骤放进程序，模型只保留唯一必须它做的那一步。
"""
import json

from .context import RunContext
from .schemas import DIMENSIONS
from .tools import _call

# 提示词越大模型越慢：校内端点实测 18000 字输入要 180 秒以上。
# 两块 × 3000 字约 3500 token，是能在 180 秒预算内稳定出结果的上限。
MAX_BLOCKS = 2
BLOCK_CHARS = 3000
MAX_TOTAL_CHARS = 6000


def _clip(text: str, budget: int) -> str:
    return text if len(text) <= budget else text[:budget] + "…"


async def retrieve(ctx: RunContext, question: str, top_k: int) -> tuple[list[dict], dict]:
    """程序侧检索并读回原文；返回可直接塞进提示词的片段列表。"""
    found = json.loads(await _call(ctx, "kb_search", ctx.kb.search,
                                   question, None, None, top_k))
    if found["status"] != "ok":
        return [], found
    blocks = []
    for hit in found["data"][:MAX_BLOCKS]:
        got = json.loads(await _call(ctx, "kb_read", ctx.kb.read, hit["planid"], None,
                                     BLOCK_CHARS, chunk_id=hit["chunk_id"]))
        if got["status"] == "ok":
            blocks.append({"hit": hit, "passage": got["data"]})
    return blocks, found


def build_messages(question: str, dimensions: list[str], blocks: list[dict]) -> list[dict]:
    """把检索到的原文拼成一次性的用户消息；模型不再需要调用任何工具。"""
    dims = dimensions or list(DIMENSIONS)
    parts = [f"任务：{question}", f"维度：{'、'.join(dims)}", "",
             "以下是本次检索到的政策原文。**只能依据这些内容提取，不得使用其他知识。**", ""]
    budget = MAX_TOTAL_CHARS
    for index, block in enumerate(blocks, 1):
        hit, passage = block["hit"], block["passage"]
        text = _clip(passage["content"], min(BLOCK_CHARS, budget))
        if not text:
            break
        budget -= len(text)
        parts.append(f"【片段{index}】planid={hit['planid']} chunk_id={hit['chunk_id']} "
                     f"篇名=《{hit['title']}》")
        parts.append(text)
        parts.append("")
    parts += ["请按维度提取要素，只输出一个 JSON 对象，结构：",
              '{"answer":"一句话概括覆盖情况","claims":[{"dimension":"维度名",'
              '"text":"要素内容","chunk_ids":["片段里的 chunk_id"],'
              '"quotes":["原文逐字片段"]}]}',
              "",
              "硬性约束：chunk_ids 只能填上面出现过的 chunk_id；quotes 必须逐字摘自原文；",
              "原文没写到的维度不要输出 claims，也不要猜。"]
    return [{"role": "user", "content": "\n".join(parts)}]
