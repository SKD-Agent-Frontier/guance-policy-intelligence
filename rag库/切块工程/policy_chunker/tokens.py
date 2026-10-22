"""固定官方 tokenizer 与模板的纯文本分支；只计数，不载入模型权重。"""
from functools import lru_cache
from pathlib import Path
import json
from transformers import AutoTokenizer
from .source import sha, stable_id


class TokenCounter:
    def __init__(self, project, config):
        self.config = config
        self.profile = json.loads((project / "model_profile.json").read_text(encoding="utf-8"))
        directory = project / ".cache" / "qwen-tokenizer"
        for name, expected in self.profile["files"].items():
            if sha((directory / name).read_bytes()) != expected:
                raise ValueError(f"tokenizer file hash mismatch: {name}")
        self.tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        # Qwen 的聊天模板保存在 processor 文件中，显式复用它而不是自造 token 开销。
        self.tokenizer.chat_template = (directory / "chat_template.jinja").read_text(encoding="utf-8")
        self.profile_hash = stable_id("profile", self.profile, config)

    def render(self, text):
        conversation = [
            {"role": "system", "content": [{"type": "text", "text": self.config["instruction"]}]},
            {"role": "user", "content": [{"type": "text", "text": text}]}]
        return self.tokenizer.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)

    @lru_cache(maxsize=1024)
    def count(self, text):
        # 禁止传 max_length/truncation=True；真实长度超过上限也必须返回真实计数。
        rendered = self.render(text)
        return len(self.tokenizer.encode(rendered, add_special_tokens=False, truncation=False))


def retrieval_text(source, section_path, body, contexts):
    """增强字段只供检索；可引用原句只来自独立保存的 source_segments。"""
    meta = source.metadata
    prefix = [f"篇名：{meta.get('title') or '未知'}", f"地区：{meta.get('area_name') or '未知'}",
              f"时期：{meta.get('fyp_name') or '未知'}", f"planid：{source.planid}"]
    if section_path:
        prefix.append("章节：" + " / ".join(section_path))
    for item in contexts:
        prefix.append(f"必要上下文（{item['role']}，原文[{item['start']}:{item['end']}]）：\n{item['text']}")
    for item in body:
        prefix.append(f"正文（原文[{item['start']}:{item['end']}]）：\n{item['text']}")
    return "\n".join(prefix)
