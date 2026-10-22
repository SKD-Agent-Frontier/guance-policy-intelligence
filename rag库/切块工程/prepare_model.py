"""只下载官方 tokenizer/processor 配置，不下载模型权重，不上传语料。"""
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parent
MODEL = "Qwen/Qwen3-VL-Embedding-8B"
FILES = ["config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json",
         "merges.txt", "chat_template.jinja", "preprocessor_config.json"]


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main():
    target = ROOT / ".cache" / "qwen-tokenizer"
    target.mkdir(parents=True, exist_ok=True)
    # 首次解析 main 后立即固定 commit；后续所有文件都从同一修订下载。
    profile_path = ROOT / "model_profile.json"
    if profile_path.exists():
        revision = json.loads(profile_path.read_text(encoding="utf-8"))["revision"]
    else:
        revision = json.loads(fetch(f"https://huggingface.co/api/models/{MODEL}"))["sha"]
    hashes = {}
    for name in FILES:
        data = fetch(f"https://huggingface.co/{MODEL}/resolve/{revision}/{name}")
        (target / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
        print(f"downloaded {name}: {len(data)} bytes", flush=True)
    profile = {"model": MODEL, "revision": revision, "files": hashes,
               "instruction": "Represent the user's input.",
               "template_options": {"add_generation_prompt": True},
               "counting": "official tokenizer + official processor chat_template, text-only",
               "weights_downloaded": False, "max_input_tokens": 8192}
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Pinned {revision}")


if __name__ == "__main__":
    main()
