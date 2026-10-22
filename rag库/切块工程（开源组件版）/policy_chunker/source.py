"""原始字节、Unicode 码点位置和原子文件写入；禁止在这里清洗正文。"""
# 本模块是整个流程的“原文坐标系”。后续模块可以判断结构和生成检索前缀，
# 但证据文字只能从 Source.text 截取，不能从 Markdown 解析器的输出反推。
# 要区分三个概念：raw 是磁盘字节；text 是完整解码字符串；segment 是其位置切片。
# 例如 text='甲😀乙' 时 [1:2] 取到一个 emoji；同一范围不是 UTF-8 字节偏移，
# 也不是浏览器 JavaScript 常用的 UTF-16 下标。跨语言调用时须明确转换。
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import yaml


def sha(data: bytes) -> str:
    # 对原始 bytes 求摘要，既检查内容，也能发现 CRLF/LF 等肉眼不明显的变化。
    # 此处不做“去空格后再哈希”，否则将无法证明原始文件未被改写。
    return hashlib.sha256(data).hexdigest()


def stable_id(kind, *parts):
    # kind 是对象类别，parts 是决定身份的业务信息，例如 planid、来源哈希和范围。
    # 字典键排序及固定 JSON 分隔符，使同一组信息不会因排版或键顺序而产生不同 ID。
    # 这里得到的是确定性标识，不是“文本相似”的判断：不同 planid 仍有独立身份。
    data = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # 新旧工程使用不同命名空间，防止同范围对象被误认为同一切分版本。
    return f"oss1_{kind}_{sha(data.encode('utf-8'))[:32]}"


def write_json(path: Path, value):
    """先写临时文件再替换；崩溃时旧检查点仍是完整 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    # 只有临时文件写完才替换正式文件，避免把半份 JSON 当成有效检查点。
    # 这是文件替换级的原子写入，不等于数据库事务，也未额外承诺断电后的落盘持久性。
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path, records):
    # records 可以是生成器：逐条写入，无需先把全库记录放进一个大列表。
    # 一条 JSON 独占一行；原文中的换行由 JSON 编码为转义字符，读回后仍保持原样。
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def read_jsonl(path):
    # yield 使调用者可以边读边校验。这里只忽略文件本身的空行，不清洗记录内的 text。
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


@dataclass
class Source:
    # path：本次读取的文件位置；对快照校验时也可以指向快照文件。
    # raw / text：同一份来源的字节与解码视图，后者包含 front-matter 和全部空白。
    # metadata：从 front-matter 解析出来供过滤/展示的字段，不能代替政策正文。
    # body_start：正文起点；提取元数据并不意味着把前面字符从 text 中删除。
    # offsets：每一行的起点，加上末尾哨兵 len(text)，供行号与字符范围互相映射。
    # source_hash：整份原始字节的摘要；version：planid 与该摘要共同决定的文档版本。
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
        # 输入是一个 <planid>.md 路径，返回完整 Source；解码或身份不一致时直接报错。
        # 上层 pipeline 负责将这种失败登记为 error，不能在这里用替换字符掩盖问题。
        path = Path(path)
        raw = path.read_bytes()
        # read_text() 默认会把 CRLF 转成 LF；必须先读 bytes 再 decode。
        text = raw.decode("utf-8", errors="strict")
        front = re.match(r"\ufeff?---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
        # 仅识别文件开头被 --- 包围的 YAML；BOM 如存在，仍留在完整字符串中。
        # safe_load 只解析数据，不执行 YAML 中可能携带的 Python 对象构造。
        metadata = yaml.safe_load(front.group(1)) if front else {}
        if not isinstance(metadata, dict):
            raise ValueError("front-matter must be a mapping")
        planid = str(metadata.get("planid", path.stem))
        # 文件名与元数据若指向不同政策，继续处理会造成错引，因此不能默默择一使用。
        if planid != path.stem:
            raise ValueError(f"planid {planid} differs from filename {path.stem}")
        offsets = [0]
        # 明确把 CRLF、单 CR、LF 当作换行；不能用通用 splitlines() 把正文中的
        # 某些 Unicode 控制字符也当成新行，否则会与 Markdown 的行坐标失配。
        for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text):
            offsets.append(offsets[-1] + len(line))
        return cls(path, raw, text, metadata, front.end() if front else 0,
                   offsets, sha(raw), stable_id("doc", planid, sha(raw)))

    @property
    def planid(self):
        # 属性统一返回字符串，避免元数据中的数字 123 与路径中的 '123' 比较不一致。
        return str(self.metadata.get("planid", self.path.stem))

    @property
    def lines(self):
        # 与 Markdown 的 CR/LF 行定义一致；Unicode 控制字符不能偷偷增加行号。
        return [self.text[a:b] for a, b in zip(self.offsets, self.offsets[1:])]

    def segment(self, start, end, role="body"):
        """所有位置都是 Python Unicode 码点，绝不是字节或 JS UTF-16 单元。"""
        if not 0 <= start <= end <= len(self.text):
            raise ValueError(f"invalid source range: {start}:{end}")
        # role 描述片段用途（正文、表头、必要条件等），不改变片段内容或坐标。
        # bisect_right 用行起点定位 1 起点行号；end 为排他边界，所以末行用 end-1。
        # text 是证据原文，行号只是辅助展示；逐字校验依赖 start/end 而非行号。
        return {"start": start, "end": end, "role": role, "text": self.text[start:end],
                "line_start": bisect_right(self.offsets, start),
                "line_end": bisect_right(self.offsets, max(start, end - 1))}


def inventory(root):
    """只扫描批准的两个分组，包含 media；用于运行前后逐文件对账。"""
    result = []
    # 排序保证清单顺序固定；比较前后清单既能发现内容变化，也能发现增删文件。
    # 只进入 corpus/edge_cases，避免把训练集、输出结果和其他文档混入政策输入。
    for group in ("corpus", "edge_cases"):
        for path in sorted((root / group).rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                result.append({"path": path.relative_to(root).as_posix(),
                               "bytes": len(data), "sha256": sha(data)})
    return result
