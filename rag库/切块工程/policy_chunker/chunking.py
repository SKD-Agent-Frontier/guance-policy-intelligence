"""父块保留结构上下文，子块仅沿完整段落、句群或安全表格行组切分。"""
# 与 structure 的分工：structure 回答“哪里是段落/表格/章节”，本模块回答
# “哪些单元应共享父块、哪些完整范围适合成为检索子块”。
# 流程为 source_quality -> make_parents -> make_chunks；最终 ready/pending 还要
# 经过 relations.assemble，因为补入必要条件后，实际模型输入可能再次超过上限。
from collections import Counter
from html.parser import HTMLParser
import re
from .source import sha, stable_id
from .tokens import retrieval_text

EXCLUDED = {"metadata", "whitespace", "toc", "page_marker", "section", "image"}
# EXCLUDED 只是“不独立生成政策正文块”，不是删除这些内容：units 和快照仍保存它们。
# 章节标题通过 section_path 提供上下文；仅有图片链接时不能假装识别了图片中的政策。


def source_quality(source):
    # 返回 (质量状态, 可复核指标)，不修改 Source.text，也不调用模型判定真假。
    # no_body/media_only/title_only/source_suspect 会由 pipeline 登记，跳过正文切块。
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
    # 去空白只用于算比例；这份临时字符串绝不会写回原文或作为引用文本输出。
    cjk = len(re.findall(r"[\u3400-\u9fff]", meaningful)) / max(1, len(meaningful))
    controls = sum(ord(c) < 32 and c not in "\r\n\t" or 127 <= ord(c) < 160 for c in body)
    metrics = {"cjk_ratio": round(cjk, 6), "unexpected_control_chars": controls}
    # 避免只因 CJK 少就判坏：还要求篇幅较长，并伴随异常控制符或字符集合过窄。
    # U+FFFD 表示上游可能已发生过解码替换；这里保留可疑状态，不尝试猜原字。
    # 这是中文政策库的保守可疑标记，不是自动纠错，更不声称识别了所有乱码。
    if len(meaningful) > 500 and cjk < .02 and (controls > 10 or len(set(meaningful)) < 300):
        return "source_suspect", metrics
    if "\ufffd" in body:
        return "source_suspect", metrics
    return "normal", metrics


def make_parents(source, units, structures, counter, config, config_hash):
    """条文整段保留；普通正文按同章节连续段落组织；表题附注随表。"""
    parents, i = [], 0
    # units 是全文基础单元；structures 给出其章节归属。父块保存连续原文范围，
    # 同时记录所包含的 unit_ids，供后续选取完整段落/表格作为子块原子候选。
    structure_map = {s["id"]: s for s in structures}
    while i < len(units):
        unit = units[i]
        if unit["kind"] in EXCLUDED:
            i += 1
            continue
        structure = structure_map[unit["structure_id"]]
        selected = [unit]
        end_i = i + 1
        # selected/end_i 描述本轮将消费的单元；循环末尾 i=end_i，不会再次生成同一父块。
        kind = "text"
        if unit["kind"] == "literal":
            # 代码等字面块保持独立，不能与正文合并后再按句号切开。
            kind = "literal"
        elif structure["kind"] == "article":
            # 完整条文优先于长度参考值：条文内的条件、说明、列表不能因凑长度丢失。
            kind = "article"
            while end_i < len(units) and units[end_i]["start"] < structure["end"]:
                selected.append(units[end_i]); end_i += 1
        else:
            # 标题/单位/附注与整表组成同一父块；不能把后续章节的注释随意拉进来。
            candidate = i
            # 向前看：当前若是表题/单位/附注，跨过相邻空白后是否紧接同章节表格？
            # 若是，父块从当前说明一直保留到完整表体及后续附注。
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
                # look 只帮助判断下一有效单元；原始空白仍被 selected 的连续范围覆盖。
                while look < len(units) and units[look]["kind"] == "whitespace":
                    look += 1
                is_list = unit["kind"] == "list_item" or unit["text"].rstrip().endswith(("：", ":")) and look < len(units) and units[look]["kind"] == "list_item"
                if is_list:
                    kind = "list"
                    while end_i < len(units) and units[end_i]["structure_id"] == unit["structure_id"] and units[end_i]["kind"] in ("whitespace", "list_item", "note"):
                        selected.append(units[end_i]); end_i += 1
                else:
                    # 普通正文只合并同章节的连续段落。3072 是父块组织参考，不是硬切点；
                    # 一个原始段落已经很长时，仍先保留它，再由子块阶段判断能否安全细分。
                    while end_i < len(units):
                        following = units[end_i]
                        if following["structure_id"] != unit["structure_id"] or following["kind"] not in ("paragraph", "whitespace", "note"):
                            break
                        if following["kind"] == "paragraph" and following["text"].rstrip().endswith(("：", ":")):
                            # 给下一组列表留下可能的引导语，避免提前吞进前一个父块。
                            break
                        length = counter.count(source.text[unit["start"]:following["end"]])
                        if length > config["parent_target_tokens"]:
                            break
                        selected.append(following); end_i += 1
        start, end = selected[0]["start"], selected[-1]["end"]
        # parents.status=ready 只表示父块已建立，不表示它的所有子块都可入索引。
        # 是否可检索以子块定稿后的状态及 usable_chunks 清单为准。
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
    # cursor 是尚未输出的句群起点；depth 是未闭合括号/中文引号的栈。
    # 英文小数点不在候选句终集合中，因此不会把 1.25 万元切成两段。
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
            # 将紧随句终的空白归入前一范围，保证各个范围拼回去仍无缺口、不改换行。
            while stop < end and text[stop] in " \t\r\n":
                stop += 1
            result.append((cursor, stop)); cursor = stop; i = stop - 1
        i += 1
    if cursor < end:
        result.append((cursor, end))
    return result or [(start, end)]
    # 没有可靠边界就返回整段；上层会将仍超限的结果标为 pending，绝不字符硬截。


class TableGrid(HTMLParser):
    """只读 HTML 网格验证：行组边界必须位于所有 rowspan 结束之后。"""
    def __init__(self):
        # 每个单元格只记录 (colspan, rowspan, 是否th)，足够检查行组约束。
        # 这里不重建 HTML，也不把单元格内容转成新的 Markdown 原文。
        super().__init__(convert_charrefs=False)
        self.rows, self.cells, self.current, self.depth, self.invalid = [], [], None, 0, False

    def handle_starttag(self, tag, attrs):
        # HTMLParser 按标签顺序回调。嵌套 table、未闭合上一行、非法跨度均使网格不可靠。
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
        # 遇到 </tr> 才结束一行；rows 是网格信息，原始行文本稍后由正则范围取回。
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
    # 成功返回 (行组范围列表, 共享表头segments)，失败返回 None。
    # 本函数只决定“哪里允许切”，不按 token 拼行组；拼组由 make_chunks 完成。
    if re.search(r"<table\b", text, re.I):
        grid = TableGrid(); grid.feed(text)
        rows = list(re.finditer(r"<tr\b[^>]*>.*?</tr\s*>", text, re.I | re.S))
        # rows 保存相对 table 单元的原文位置；与解析器的行数对不上时不能猜测对齐。
        if grid.invalid or grid.depth != 0 or not rows or len(rows) != len(grid.rows):
            return None
        first_cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", rows[0].group(), re.I | re.S)
        header_text = " ".join(re.sub(r"<[^>]+>", "", value) for value in first_cells)
        explicit_header = all(cell[2] for cell in grid.rows[0]) or bool(re.search(r"<thead\b", text[:rows[0].end()], re.I))
        # 表头优先依赖 th/thead；否则只接受含常见表头词的首行，避免把数据行当表头。
        if not explicit_header and not re.search(r"序号|指标|项目|名称|类别|单位|内容|目标|年份|年度|基期|规划期", header_text):
            return None
        occupied, width, group_start, groups = {}, None, 0, []
        # occupied[c] 是第 c 列被占用到的“排他行号”。例如第0行 rowspan=2，
        # 该列记为2；处理第1行时仍被占用，必须等第1行结束后才允许结束这一行组。
        # width 是已确认列数；group_start 是当前尚未封口行组的首行。
        for r, cells in enumerate(grid.rows):
            col = 0
            for colspan, rowspan, _ in cells:
                while occupied.get(col, 0) > r:
                    col += 1
                if any(occupied.get(c, 0) > r for c in range(col, col + colspan)):
                    # 新格跨入仍被上一行占用的列，网格发生冲突，不能进行行组切分。
                    return None
                for c in range(col, col + colspan):
                    occupied[c] = r + rowspan
                col += colspan
            active = sorted(c for c, until in occupied.items() if until > r)
            # 当前行的覆盖列必须从0连续，且与首行等宽；缺列或宽度变化都保持待处理。
            if not active or active != list(range(active[-1] + 1)):
                return None
            current_width = len(active)
            if width is None:
                width = current_width
            if current_width != width:
                return None
            if max(occupied.values()) <= r + 1:
                # 本行结束时所有 rowspan 都已到期，才是安全的逻辑行组边界。
                groups.append((rows[group_start].start(), rows[r].end())); group_start = r + 1
        if group_start != len(rows) or len(groups) < 2:
            return None
        # 首个完整逻辑行组作为共享表头，跨多行表头不会从 rowspan 中间切开。
        head = groups[0]
        header = source.segment(unit["start"] + head[0], unit["start"] + head[1], "table_header")
        groups = [(unit["start"] + a, unit["start"] + b) for a, b in groups[1:]]
        # 局部行位置加上 unit.start，换成完整源文件码点位置；首行组作为共享表头。
        return groups, [header]
    lines = list(re.finditer(r"[^\n]*(?:\n|$)", text))
    # Markdown 表格要求至少包含表头、分隔线与数据行。空行过滤仅用于表结构判断，
    # 返回的每个范围仍指向原始 text，因此不会把原始行尾空格或 CRLF 改掉。
    lines = [m for m in lines if m.end() > m.start() and m.group().strip()]
    if len(lines) < 3:
        return None
    def cells(value):
        # 转义竖线和行内代码中的竖线不能作为列边界。
        value = re.sub(r"`[^`]*`", "CODE", value.strip().strip("|"))
        return re.split(r"(?<!\\)\|", value)
    width = len(cells(lines[0].group()))
    # 所有行必须列数一致，第二行必须是 Markdown 表头分隔线；否则不安全分组。
    if not re.fullmatch(r"[\s|:\-]+", lines[1].group()) or any(len(cells(m.group())) != width for m in lines):
        return None
    header = source.segment(unit["start"], unit["start"] + lines[1].end(), "table_header")
    return [(unit["start"] + m.start(), unit["start"] + m.end()) for m in lines[2:]], [header]


def make_chunks(source, parents, units, counter, config, config_hash):
    # 输入：完整父块及其单元成员。输出：尚未装配跨条依赖的子块草稿。
    # 768 是拼接目标，8192 是完整输入硬上限；两个值不能混成“每768就切一刀”。
    chunks = []
    unit_map = {u["id"]: u for u in units}
    for parent in parents:
        path = parent["section_path"]
        def count(ranges, contexts=()):
            # 候选阶段就计入元数据前缀和模板，不能只对 source_text 的正文长度计数。
            return counter.count(retrieval_text(source, path, [source.segment(a, b) for a, b in ranges], contexts))
        ranges = [(parent["start"], parent["end"])]
        if count(ranges) <= config["child_target_tokens"]:
            # 短父块直接复用同一范围，避免切出碎片。父 ID 与子 ID 仍分别存在。
            chunks.append(new_chunk(source, parent, ranges, [], config_hash)); continue
        selected = [unit_map[x] for x in parent["unit_ids"] if unit_map[x]["kind"] not in EXCLUDED]
        # 表格即使超过 768，只要完整输入不超过 8192，仍保持整表。
        if parent["kind"] == "table" and count(ranges) <= config["max_input_tokens"]:
            chunks.append(new_chunk(source, parent, ranges, [], config_hash)); continue
        if parent["kind"] == "table":
            table = next((u for u in selected if u["kind"] == "table"), None)
            safe = table_groups(source, table) if table else None
            if not safe:
                # 保存整表原文与明确原因，后续不会把这个 pending 块发布为可用对象。
                chunk = new_chunk(source, parent, ranges, [], config_hash)
                chunk["status"], chunk["reasons"] = "pending", ["unsafe_oversize_table"]
                chunks.append(chunk); continue
            row_ranges, contexts = safe
            # 每个行组只包含自己的原文行；共同表头、表题、单位和附注另存 contexts。
            # 这样既能补全检索语境，又不会伪造一段原文中并不存在的连续文本。
            contexts += [source.segment(u["start"], u["end"], u["kind"]) for u in selected if u["kind"] != "table"]
            packed = []
            for item in row_ranges:
                # 贪心合并相邻完整行组；加入下一组后超过目标时，先封口当前组。
                # 单个行组本身超限不硬切，最终 assemble 会保留超限状态。
                if packed and count([(packed[0][0], item[1])], contexts) > config["child_target_tokens"]:
                    chunks.append(new_chunk(source, parent, [(packed[0][0], packed[-1][1])], contexts, config_hash)); packed = []
                packed.append(item)
            if packed:
                chunks.append(new_chunk(source, parent, [(packed[0][0], packed[-1][1])], contexts, config_hash))
            continue
        # 每个完整段落先作为原子候选；仅在超过硬上限时才尝试句群切分。
        atoms = []
        # atoms 中每一项是允许独立保留的原文范围；普通单元只有超硬上限才尝试句群。
        for unit in selected:
            item = (unit["start"], unit["end"])
            if count([item]) > config["max_input_tokens"] and unit["kind"] not in ("table", "literal"):
                atoms.extend(sentence_ranges(source, *item))
            else:
                atoms.append(item)
        packed = []
        for item in atoms:
            # 合并时取从首原子起点到末原子终点的连续原文，保留其间原有空白。
            # 超过目标便输出已有完整组；不把单个长段落截成固定长度。
            proposed = [(packed[0][0], item[1])] if packed else [item]
            if packed and count(proposed) > config["child_target_tokens"]:
                chunks.append(new_chunk(source, parent, [(packed[0][0], packed[-1][1])], [], config_hash)); packed = []
            packed.append(item)
        if packed:
            chunks.append(new_chunk(source, parent, [(packed[0][0], packed[-1][1])], [], config_hash))
    return chunks


def new_chunk(source, parent, ranges, contexts, config_hash):
    # 这里只创建草稿：source_segments 是可定位证据，context_segments 是额外原文条件。
    # required_relation_ids、真实 token 数、实际输入哈希由 relations.assemble 补齐。
    segments = [source.segment(a, b) for a, b in ranges]
    return {"id": stable_id("chunk", source.version, parent["id"], ranges, config_hash),
            "document_id": source.version, "parent_id": parent["id"], "kind": parent["kind"],
            "section_path": parent["section_path"], "source_segments": segments,
            "context_segments": list(contexts), "required_relation_ids": [], "status": "ready", "reasons": [],
            "content_hash": sha(json_bytes([s["text"] for s in segments]))}


def json_bytes(value):
    # 固定序列化再求内容指纹，明确区分 ['甲','乙'] 与 ['甲乙'] 两种原文分段方式。
    import json
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
