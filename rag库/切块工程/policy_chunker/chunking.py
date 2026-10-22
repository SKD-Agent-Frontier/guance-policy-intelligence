"""父块保留结构上下文，子块仅沿完整段落、句群或安全表格行组切分。"""
from collections import Counter
from html.parser import HTMLParser
import re
from .source import sha, stable_id
from .tokens import retrieval_text

EXCLUDED = {"metadata", "whitespace", "toc", "page_marker", "section", "image"}


def source_quality(source):
    body = source.text[source.body_start:]
    if not body.strip():
        return "no_body", {}
    without_images = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    if not without_images.strip():
        return "media_only", {}
    # 仅标题不能成为政策事实。这里只建立比较视图，原文、空格和标点均不改写。
    title_view = re.sub(r"\s", "", str(source.metadata.get("title") or ""))
    remaining_view = re.sub(r"\s", "", re.sub(r"(?m)^\s*#{1,6}\s+", "", without_images))
    if title_view and remaining_view == title_view:
        return ("media_only" if without_images != body else "title_only"), {}
    meaningful = re.sub(r"\s", "", body)
    cjk = len(re.findall(r"[\u3400-\u9fff]", meaningful)) / max(1, len(meaningful))
    controls = sum(ord(c) < 32 and c not in "\r\n\t" or 127 <= ord(c) < 160 for c in body)
    metrics = {"cjk_ratio": round(cjk, 6), "unexpected_control_chars": controls}
    # 这是中文政策库的保守可疑标记，不是自动纠错，更不声称识别了所有乱码。
    if len(meaningful) > 500 and cjk < .02 and (controls > 10 or len(set(meaningful)) < 300):
        return "source_suspect", metrics
    if "\ufffd" in body:
        return "source_suspect", metrics
    return "normal", metrics


def make_parents(source, units, structures, counter, config, config_hash):
    """条文整段保留；普通正文按同章节连续段落组织；表题附注随表。"""
    parents, i = [], 0
    structure_map = {s["id"]: s for s in structures}
    while i < len(units):
        unit = units[i]
        if unit["kind"] in EXCLUDED:
            i += 1
            continue
        structure = structure_map[unit["structure_id"]]
        selected = [unit]
        end_i = i + 1
        kind = "text"
        if unit["kind"] == "literal":
            kind = "literal"
        elif structure["kind"] == "article":
            kind = "article"
            while end_i < len(units) and units[end_i]["start"] < structure["end"]:
                selected.append(units[end_i]); end_i += 1
        else:
            # 标题/单位/附注与整表组成同一父块；不能把后续章节的注释随意拉进来。
            candidate = i
            if unit["kind"] in ("caption", "note"):
                while candidate < len(units) and units[candidate]["kind"] in ("caption", "note", "whitespace"):
                    candidate += 1
            if unit["kind"] == "table" or candidate < len(units) and units[candidate]["kind"] == "table" and units[candidate]["structure_id"] == unit["structure_id"]:
                kind = "table"
                end_i = (candidate if unit["kind"] != "table" else i) + 1
                while end_i < len(units) and units[end_i]["kind"] in ("whitespace", "note") and units[end_i]["structure_id"] == unit["structure_id"]:
                    end_i += 1
                selected = units[i:end_i]
            else:
                # 列表引导语常带冒号；其后的项目不能因为目标长度而失去共同约束。
                look = i + 1
                while look < len(units) and units[look]["kind"] == "whitespace":
                    look += 1
                is_list = unit["kind"] == "list_item" or unit["text"].rstrip().endswith(("：", ":")) and look < len(units) and units[look]["kind"] == "list_item"
                if is_list:
                    kind = "list"
                    while end_i < len(units) and units[end_i]["structure_id"] == unit["structure_id"] and units[end_i]["kind"] in ("whitespace", "list_item", "note"):
                        selected.append(units[end_i]); end_i += 1
                else:
                    while end_i < len(units):
                        following = units[end_i]
                        if following["structure_id"] != unit["structure_id"] or following["kind"] not in ("paragraph", "whitespace", "note"):
                            break
                        if following["kind"] == "paragraph" and following["text"].rstrip().endswith(("：", ":")):
                            break
                        length = counter.count(source.text[unit["start"]:following["end"]])
                        if length > config["parent_target_tokens"]:
                            break
                        selected.append(following); end_i += 1
        start, end = selected[0]["start"], selected[-1]["end"]
        parents.append({"id": stable_id("parent", source.version, start, end, config_hash),
                        "document_id": source.version, "structure_id": unit["structure_id"],
                        "kind": kind, "start": start, "end": end,
                        "source_segments": [source.segment(start, end)],
                        "unit_ids": [u["id"] for u in selected], "section_path": unit["section_path"],
                        "status": "ready"})
        i = end_i
    return parents


def sentence_ranges(source, start, end):
    """只在中文句终或分号后切分；引号/括号未闭合时保持整段，不拆数字与小数。"""
    text, cursor, depth = source.text, start, []
    closes = {"）": "（", ")": "(", "】": "【", "]": "[", "”": "“", "’": "‘", "」": "「", "』": "『"}
    result = []
    i = start
    while i < end:
        char = text[i]
        if char in closes.values():
            depth.append(char)
        elif char in closes and depth and depth[-1] == closes[char]:
            depth.pop()
        boundary = char in "。！？；;" and not depth
        # 句号紧接右引号时，在右引号后判断边界，避免切下孤立右引号。
        if char in closes and not depth and i > start and text[i - 1] in "。！？；;":
            boundary = True
        if boundary:
            stop = i + 1
            while stop < end and text[stop] in " \t\r\n":
                stop += 1
            result.append((cursor, stop)); cursor = stop; i = stop - 1
        i += 1
    if cursor < end:
        result.append((cursor, end))
    return result or [(start, end)]


class TableGrid(HTMLParser):
    """只读 HTML 网格验证：行组边界必须位于所有 rowspan 结束之后。"""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.rows, self.cells, self.current, self.depth, self.invalid = [], [], None, 0, False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth > 1:
                self.invalid = True
        elif tag == "tr":
            if self.current is not None:
                self.invalid = True
            self.current = []
        elif tag in ("td", "th") and self.current is not None:
            attrs = dict(attrs)
            try:
                colspan, rowspan = int(attrs.get("colspan", 1)), int(attrs.get("rowspan", 1))
                if colspan < 1 or rowspan < 1:
                    self.invalid = True
                self.current.append((colspan, rowspan, tag == "th"))
            except ValueError:
                self.invalid = True

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth -= 1
        elif tag == "tr":
            if self.current is None:
                self.invalid = True
            else:
                self.rows.append(self.current); self.current = None


def table_groups(source, unit):
    """返回安全行组与共享表头原文；不转写单元格、不自动补齐缺失值。"""
    text = unit["text"]
    if re.search(r"<table\b", text, re.I):
        grid = TableGrid(); grid.feed(text)
        rows = list(re.finditer(r"<tr\b[^>]*>.*?</tr\s*>", text, re.I | re.S))
        if grid.invalid or grid.depth != 0 or not rows or len(rows) != len(grid.rows):
            return None
        first_cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", rows[0].group(), re.I | re.S)
        header_text = " ".join(re.sub(r"<[^>]+>", "", value) for value in first_cells)
        explicit_header = all(cell[2] for cell in grid.rows[0]) or bool(re.search(r"<thead\b", text[:rows[0].end()], re.I))
        if not explicit_header and not re.search(r"序号|指标|项目|名称|类别|单位|内容|目标|年份|年度|基期|规划期", header_text):
            return None
        occupied, width, group_start, groups = {}, None, 0, []
        for r, cells in enumerate(grid.rows):
            col = 0
            for colspan, rowspan, _ in cells:
                while occupied.get(col, 0) > r:
                    col += 1
                if any(occupied.get(c, 0) > r for c in range(col, col + colspan)):
                    return None
                for c in range(col, col + colspan):
                    occupied[c] = r + rowspan
                col += colspan
            active = sorted(c for c, until in occupied.items() if until > r)
            if not active or active != list(range(active[-1] + 1)):
                return None
            current_width = len(active)
            if width is None:
                width = current_width
            if current_width != width:
                return None
            if max(occupied.values()) <= r + 1:
                groups.append((rows[group_start].start(), rows[r].end())); group_start = r + 1
        if group_start != len(rows) or len(groups) < 2:
            return None
        # 首个完整逻辑行组作为共享表头，跨多行表头不会从 rowspan 中间切开。
        head = groups[0]
        header = source.segment(unit["start"] + head[0], unit["start"] + head[1], "table_header")
        groups = [(unit["start"] + a, unit["start"] + b) for a, b in groups[1:]]
        return groups, [header]
    lines = list(re.finditer(r"[^\n]*(?:\n|$)", text))
    lines = [m for m in lines if m.end() > m.start() and m.group().strip()]
    if len(lines) < 3:
        return None
    def cells(value):
        # 转义竖线和行内代码中的竖线不能作为列边界。
        value = re.sub(r"`[^`]*`", "CODE", value.strip().strip("|"))
        return re.split(r"(?<!\\)\|", value)
    width = len(cells(lines[0].group()))
    if not re.fullmatch(r"[\s|:\-]+", lines[1].group()) or any(len(cells(m.group())) != width for m in lines):
        return None
    header = source.segment(unit["start"], unit["start"] + lines[1].end(), "table_header")
    return [(unit["start"] + m.start(), unit["start"] + m.end()) for m in lines[2:]], [header]


def make_chunks(source, parents, units, counter, config, config_hash):
    chunks = []
    unit_map = {u["id"]: u for u in units}
    for parent in parents:
        path = parent["section_path"]
        def count(ranges, contexts=()):
            return counter.count(retrieval_text(source, path, [source.segment(a, b) for a, b in ranges], contexts))
        ranges = [(parent["start"], parent["end"])]
        if count(ranges) <= config["child_target_tokens"]:
            chunks.append(new_chunk(source, parent, ranges, [], config_hash)); continue
        selected = [unit_map[x] for x in parent["unit_ids"] if unit_map[x]["kind"] not in EXCLUDED]
        # 表格即使超过 768，只要完整输入不超过 8192，仍保持整表。
        if parent["kind"] == "table" and count(ranges) <= config["max_input_tokens"]:
            chunks.append(new_chunk(source, parent, ranges, [], config_hash)); continue
        if parent["kind"] == "table":
            table = next((u for u in selected if u["kind"] == "table"), None)
            safe = table_groups(source, table) if table else None
            if not safe:
                chunk = new_chunk(source, parent, ranges, [], config_hash)
                chunk["status"], chunk["reasons"] = "pending", ["unsafe_oversize_table"]
                chunks.append(chunk); continue
            row_ranges, contexts = safe
            contexts += [source.segment(u["start"], u["end"], u["kind"]) for u in selected if u["kind"] != "table"]
            packed = []
            for item in row_ranges:
                if packed and count([(packed[0][0], item[1])], contexts) > config["child_target_tokens"]:
                    chunks.append(new_chunk(source, parent, [(packed[0][0], packed[-1][1])], contexts, config_hash)); packed = []
                packed.append(item)
            if packed:
                chunks.append(new_chunk(source, parent, [(packed[0][0], packed[-1][1])], contexts, config_hash))
            continue
        # 每个完整段落先作为原子候选；仅在超过硬上限时才尝试句群切分。
        atoms = []
        for unit in selected:
            item = (unit["start"], unit["end"])
            if count([item]) > config["max_input_tokens"] and unit["kind"] not in ("table", "literal"):
                atoms.extend(sentence_ranges(source, *item))
            else:
                atoms.append(item)
        packed = []
        for item in atoms:
            proposed = [(packed[0][0], item[1])] if packed else [item]
            if packed and count(proposed) > config["child_target_tokens"]:
                chunks.append(new_chunk(source, parent, [(packed[0][0], packed[-1][1])], [], config_hash)); packed = []
            packed.append(item)
        if packed:
            chunks.append(new_chunk(source, parent, [(packed[0][0], packed[-1][1])], [], config_hash))
    return chunks


def new_chunk(source, parent, ranges, contexts, config_hash):
    segments = [source.segment(a, b) for a, b in ranges]
    return {"id": stable_id("chunk", source.version, parent["id"], ranges, config_hash),
            "document_id": source.version, "parent_id": parent["id"], "kind": parent["kind"],
            "section_path": parent["section_path"], "source_segments": segments,
            "context_segments": list(contexts), "required_relation_ids": [], "status": "ready", "reasons": [],
            "content_hash": sha(json_bytes([s["text"] for s in segments]))}


def json_bytes(value):
    import json
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
