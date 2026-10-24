"""固定官方 tokenizer 与模板的纯文本分支；只计数，不载入模型权重。"""
# 输入链路：证据原文 + 必要上下文 -> retrieval_text -> 官方聊天模板 -> token 数。
# “768 token 的子块”不是 768 个汉字，标题、模板和上下文也会消耗长度。
# 本模块只加载分词器，不实例化 Qwen 模型，不计算向量，也不发送网络请求。
from functools import lru_cache
from pathlib import Path
import json
from transformers import AutoTokenizer
from .source import sha, stable_id


class TokenCounter:
    # 一个构建/校验过程复用一个计数器，避免每个子块都重新加载分词词表。
    def __init__(self, project, config):
        self.config = config
        self.profile = json.loads((project / "model_profile.json").read_text(encoding="utf-8"))
        directory = project / ".cache" / "qwen-tokenizer"
        # profile 固定模型修订与各文件摘要。即使目录名称相同，文件被换过也会报错，
        # 防止构建和校验实际使用了不同的分词规则却仍声称长度一致。
        for name, expected in self.profile["files"].items():
            if sha((directory / name).read_bytes()) != expected:
                raise ValueError(f"tokenizer file hash mismatch: {name}")
        self.tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        # local_files_only 禁止隐式下载；trust_remote_code=False 禁止加载远程自定义代码。
        # tokenizer 与模型权重是两回事：此处无需下载或运行 8B 参数模型。
        # Qwen 的聊天模板保存在 processor 文件中，显式复用它而不是自造 token 开销。
        self.tokenizer.chat_template = (directory / "chat_template.jinja").read_text(encoding="utf-8")
        self.profile_hash = stable_id("profile", self.profile, config)
        # 记录的不只是模型名字，还包括固定修订、文件摘要与实际输入配置。

    def render(self, text):
        # 按官方纯文本输入形式放入 system 指令和 user 内容，再加入生成提示。
        # 返回包含特殊标记的完整字符串，后续既用它计数，也对它求输入哈希。
        conversation = [
            {"role": "system", "content": [{"type": "text", "text": self.config["instruction"]}]},
            {"role": "user", "content": [{"type": "text", "text": text}]}]
        return self.tokenizer.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)

    @lru_cache(maxsize=1024)
    def count(self, text):
        # 相同输入常在候选装配、定稿和校验时多次出现，LRU 缓存只减少重复分词。
        # 缓存不修改文本，也不以字符数估算 token；缓存未命中仍调用真实 tokenizer。
        # 禁止传 max_length/truncation=True；真实长度超过上限也必须返回真实计数。
        rendered = self.render(text)
        # 模板已含所需特殊标记，add_special_tokens=False 避免再额外添加一遍。
        return len(self.tokenizer.encode(rendered, add_special_tokens=False, truncation=False))


def retrieval_text(source, section_path, body, contexts):
    """增强字段只供检索；可引用原句只来自独立保存的 source_segments。"""
    meta = source.metadata
    # body/contexts 都是 segment 列表，因此不连续的表头、行组和附注不会丢失各自位置。
    # 未知元数据只写“未知”，不根据邻文或模型补造地区和时期。
    prefix = [f"篇名：{meta.get('title') or '未知'}", f"地区：{meta.get('area_name') or '未知'}",
              f"时期：{meta.get('fyp_name') or '未知'}", f"planid：{source.planid}"]
    if section_path:
        prefix.append("章节：" + " / ".join(section_path))
    for item in contexts:
        # 必要上下文与正文分区标注，说明它们来自原文的哪些范围，而非伪装为连续引句。
        prefix.append(f"必要上下文（{item['role']}，原文[{item['start']}:{item['end']}]）：\n{item['text']}")
    for item in body:
        prefix.append(f"正文（原文[{item['start']}:{item['end']}]）：\n{item['text']}")
    return "\n".join(prefix)
    # 返回值是派生检索表示。生成引用时应回到 source_segments/context_segments，
    # 不能把“篇名：”“必要上下文”等程序增加的标签当成政策原句。
