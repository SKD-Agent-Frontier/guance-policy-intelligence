"""原始字节、Unicode 码点位置和原子文件写入；禁止在这里清洗正文。"""
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import yaml


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(kind, *parts):
    data = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{kind}_{sha(data.encode('utf-8'))[:32]}"


def write_json(path: Path, value):
    """先写临时文件再替换；崩溃时旧检查点仍是完整 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def read_jsonl(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


@dataclass
class Source:
    path: Path
    raw: bytes
    text: str
    metadata: dict
    body_start: int
    offsets: list
    source_hash: str
    version: str

    @classmethod
    def load(cls, path):
        path = Path(path)
        raw = path.read_bytes()
        # read_text() 默认会把 CRLF 转成 LF；必须先读 bytes 再 decode。
        text = raw.decode("utf-8", errors="strict")
        front = re.match(r"\ufeff?---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
        metadata = yaml.safe_load(front.group(1)) if front else {}
        if not isinstance(metadata, dict):
            raise ValueError("front-matter must be a mapping")
        planid = str(metadata.get("planid", path.stem))
        if planid != path.stem:
            raise ValueError(f"planid {planid} differs from filename {path.stem}")
        offsets = [0]
        for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text):
            offsets.append(offsets[-1] + len(line))
        return cls(path, raw, text, metadata, front.end() if front else 0,
                   offsets, sha(raw), stable_id("doc", planid, sha(raw)))

    @property
    def planid(self):
        return str(self.metadata.get("planid", self.path.stem))

    @property
    def lines(self):
        # 与 Markdown 的 CR/LF 行定义一致；Unicode 控制字符不能偷偷增加行号。
        return [self.text[a:b] for a, b in zip(self.offsets, self.offsets[1:])]

    def segment(self, start, end, role="body"):
        """所有位置都是 Python Unicode 码点，绝不是字节或 JS UTF-16 单元。"""
        if not 0 <= start <= end <= len(self.text):
            raise ValueError(f"invalid source range: {start}:{end}")
        return {"start": start, "end": end, "role": role, "text": self.text[start:end],
                "line_start": bisect_right(self.offsets, start),
                "line_end": bisect_right(self.offsets, max(start, end - 1))}


def inventory(root):
    """只扫描批准的两个分组，包含 media；用于运行前后逐文件对账。"""
    result = []
    for group in ("corpus", "edge_cases"):
        for path in sorted((root / group).rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                result.append({"path": path.relative_to(root).as_posix(),
                               "bytes": len(data), "sha256": sha(data)})
    return result
