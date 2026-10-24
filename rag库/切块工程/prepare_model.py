"""只下载官方 tokenizer/processor 配置，不下载模型权重，不上传语料。"""
# 这是环境准备工具，只有显式运行本脚本才下载；chunk.py 的处理链路本身是离线的。
# FILES 是下载白名单，未包含 *.safetensors 等参数文件；总量约 16 MB。
# 下载得到的词表负责“如何分词”，聊天模板负责“模型实际接收的输入长什么样”。
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parent
MODEL = "Qwen/Qwen3-VL-Embedding-8B"
FILES = ["config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json",
         "merges.txt", "chat_template.jinja", "preprocessor_config.json"]


def fetch(url):
    # 只取 URL 响应字节，60 秒为单次请求超时；没有携带任何政策正文或训练集内容。
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main():
    target = ROOT / ".cache" / "qwen-tokenizer"
    target.mkdir(parents=True, exist_ok=True)
    # 首次解析 main 后立即固定 commit；后续所有文件都从同一修订下载。
    profile_path = ROOT / "model_profile.json"
    if profile_path.exists():
        # 已有 profile 时使用其中 commit，不因官方 main 更新而改变原工程的分词规则。
        revision = json.loads(profile_path.read_text(encoding="utf-8"))["revision"]
    else:
        # 首次才查询当前修订；随后每个 resolve URL 均使用该修订而不是浮动 main。
        revision = json.loads(fetch(f"https://huggingface.co/api/models/{MODEL}"))["sha"]
    hashes = {}
    for name in FILES:
        data = fetch(f"https://huggingface.co/{MODEL}/resolve/{revision}/{name}")
        (target / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
        # profile 记录实际下载字节摘要，TokenCounter 初始化时会逐文件重新核对。
        print(f"downloaded {name}: {len(data)} bytes", flush=True)
    profile = {"model": MODEL, "revision": revision, "files": hashes,
               "instruction": "Represent the user's input.",
               "template_options": {"add_generation_prompt": True},
               "counting": "official tokenizer + official processor chat_template, text-only",
               "weights_downloaded": False, "max_input_tokens": 8192}
    # 这里只描述纯文本计数所需配置。8192 是本工程输入上限，不是“已运行模型”的证明。
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Pinned {revision}")


if __name__ == "__main__":
    main()
