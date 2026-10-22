"""从原工程复用的质量/表格/安全边界工具；不含其父子切块核心。"""
# source_quality 不清洗原文；table_groups 只判定保护边界。
# sentence_ranges 仅供完整性校验，不生成新版父子候选。
import re
from html.parser import HTMLParser
EXCLUDED = {"metadata", "whitespace", "toc", "page_marker", "section", "image", "document_title"}

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
