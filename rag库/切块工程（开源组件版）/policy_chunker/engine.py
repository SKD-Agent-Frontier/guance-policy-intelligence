"""实际调用 LlamaIndex，并把库节点的位置映射回未经改写的原文。

本模块不实现递归拆分或贪心拼块。SentenceSplitter 决定候选边界，
HierarchicalNodeParser 生成父子关系；这里负责配置、身份和位置证据。
"""
import importlib.metadata
import re
import inspect
import json
from pathlib import Path
from functools import lru_cache
from llama_index.core import Document
from llama_index.core.node_parser import HierarchicalNodeParser, SentenceSplitter
from llama_index.core.schema import NodeRelationship
from .source import stable_id, sha


class MappingError(ValueError):
    """库节点内部文字改变或顺序无法还原；不能用全文件第一次匹配掩盖。"""


def verify_installation(project, expected=None):
    """复核固定版本和实际执行的两份官方源码；不能只相信记录里的版本号。"""
    profile = expected or json.loads((Path(project)/"component_profile.json").read_text(encoding="utf-8"))
    if importlib.metadata.version("llama-index-core") != profile["version"]:
        raise ValueError("installed LlamaIndex version differs from batch")
    for cls in (HierarchicalNodeParser, SentenceSplitter):
        actual = sha(Path(inspect.getfile(cls)).read_bytes())
        if actual != profile["installed_source_files"][cls.__name__]["sha256"]:
            raise ValueError(f"upstream source hash changed: {cls.__name__}")
    return profile


class Engine:
    def __init__(self, source, counter, config, config_hash):
        self.source, self.counter = source, counter
        self.config, self.config_hash = config, config_hash
        self.nodes, self.calls = [], []
        self.version = importlib.metadata.version("llama-index-core")

    @lru_cache(maxsize=8192)
    def encode(self, text):
        """库内长度用同一 Qwen 裸文本分词，最终模板长度由 assemble 另算。"""
        return self.counter.tokenizer.encode(text, add_special_tokens=False, truncation=False)

    def run(self, start, end, parent_budget, child_budget, reason):
        """输入为全文件码点范围，返回原文映射后的官方父节点及子节点。

        每次调用都保存真实节点及官方关系。库内 start_char_idx 的基准可能
        相对当前层输入，因此另用有界顺序映射核对，不直接加一次偏移就相信。
        """
        call_id = stable_id("call", self.source.version, start, end, parent_budget,
                            child_budget, reason, len(self.calls), self.config_hash)
        pattern = self.config["sentence_pattern"]
        def sentence_candidates(text):
            # 这是公开参数的中文分隔配置，不负责组块；分隔结果必须能拼回输入。
            # 显式传入也避免初始化默认英文 NLTK 分句资源或隐式下载资源。
            pieces = re.findall(pattern, text)
            if "".join(pieces) != text:
                raise MappingError("sentence parameter did not preserve source")
            return pieces
        parsers = {}
        for level, budget in (("parent", parent_budget), ("child", child_budget)):
            def identity(index, doc, layer=level):
                return stable_id("node", call_id, layer, doc.id_, index)
            parsers[level] = SentenceSplitter(
                chunk_size=max(8, int(budget)), chunk_overlap=0,
                tokenizer=self.encode, chunking_tokenizer_fn=sentence_candidates,
                paragraph_separator="\r\n\r\n" if "\r\n" in self.source.text[start:end] else "\n\n",
                secondary_chunking_regex=pattern, include_metadata=False,
                include_prev_next_rel=True, id_func=identity)
        parser = HierarchicalNodeParser.from_defaults(
            node_parser_ids=["parent", "child"], node_parser_map=parsers,
            include_metadata=False, include_prev_next_rel=True)
        document = Document(text=self.source.text[start:end], id_=stable_id("input", call_id))
        # 核心调用：没有在其前后重新实现递归拆分和按长度贪心拼接。
        raw_nodes = parser.get_nodes_from_documents([document])
        call = {"id": call_id, "document_id": self.source.version, "start": start, "end": end,
                "reason": reason, "component": "HierarchicalNodeParser+SentenceSplitter",
                "package": "llama-index-core", "version": self.version,
                "parent_budget": parent_budget, "child_budget": child_budget,
                "chunk_overlap": 0, "sentence_pattern": pattern,
                "node_ids": [n.node_id for n in raw_nodes], "status": "mapped"}
        self.calls.append(call)
        roots = [n for n in raw_nodes if n.parent_node is None]
        children = {n.node_id: [] for n in roots}
        for node in raw_nodes:
            if node.parent_node is not None:
                children[node.parent_node.node_id].append(node)

        def map_sequence(nodes, begin, stop):
            # overlap=0 时，每个节点只能从上个节点之后的空白后开始；重复句也
            # 依靠顺序位置定位，不执行 source.find(text) 全文首次查找。
            cursor, mapped = begin, []
            for node in nodes:
                while cursor < stop and self.source.text[cursor].isspace():
                    cursor += 1
                value = node.get_content()
                finish = cursor + len(value)
                if finish > stop or self.source.text[cursor:finish] != value:
                    raise MappingError(f"node {node.node_id} is not the next exact source slice")
                mapped.append({"node": node, "raw_start": cursor, "raw_end": finish})
                cursor = finish
            if self.source.text[cursor:stop].strip():
                raise MappingError("unaccounted non-whitespace after final node")
            # 把节点间空白附回前块；最终证据直接切原文，库的 strip 不改写证据。
            for index, item in enumerate(mapped):
                item["start"] = begin if index == 0 else item["raw_start"]
                item["end"] = mapped[index+1]["raw_start"] if index+1 < len(mapped) else stop
            return mapped

        try:
            mapped_roots = map_sequence(roots, start, end)
            mapped_records = []
            for root in mapped_roots:
                root["children"] = map_sequence(children[root["node"].node_id], root["start"], root["end"])
                for item, level in [(root, "parent")] + [(c, "child") for c in root["children"]]:
                    node = item["node"]
                    mapped_records.append({"id": node.node_id, "document_id": self.source.version,
                        "call_id": call_id, "level": level,
                        "parent_node_id": node.parent_node.node_id if node.parent_node else None,
                        "child_node_ids": [n.node_id for n in node.child_nodes or []],
                        "raw_start": item["raw_start"], "raw_end": item["raw_end"],
                        "start": item["start"], "end": item["end"],
                        "text": node.get_content(), "text_hash": sha(node.get_content().encode()),
                        "library_start_char_idx": node.start_char_idx,
                        "library_end_char_idx": node.end_char_idx,
                        "mapping": "bounded_sequential_exact", "component_version": self.version})
            self.nodes.extend(mapped_records)
            return mapped_roots
        except MappingError:
            call["status"] = "mapping_failed"
            # 未能映射的真实库输出仍保留，不能因为有问题而丢失组件运行证据。
            for node in raw_nodes:
                self.nodes.append({"id": node.node_id, "document_id": self.source.version,
                    "call_id": call_id, "level": "child" if node.parent_node else "parent",
                    "parent_node_id": node.parent_node.node_id if node.parent_node else None,
                    "child_node_ids": [n.node_id for n in node.child_nodes or []],
                    "text": node.get_content(), "mapping": "failed",
                    "component_version": self.version})
            raise
