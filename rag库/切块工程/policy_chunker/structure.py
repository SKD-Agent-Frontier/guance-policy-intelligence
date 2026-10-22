"""Markdown 仅提供线索；结构与片段位置始终映射到未经改写的原文。"""
import re
from markdown_it import MarkdownIt
from .source import stable_id

HAN = "〇零一二三四五六七八九十百千万两0-9"
ARTICLE = re.compile(rf"^第([{HAN}]+)条(?:\s|[、：:]|$)")
CHAPTER = re.compile(rf"^第([{HAN}]+)(篇|编|部分|章|节)(?:\s|[、：:]|$)")
LIST = re.compile(r"^(?:[（(][一二三四五六七八九十百0-9]+[）)]|\d{1,3}[、．.]\s*(?!\d))")
TOC_ITEM = re.compile(r"(?:[.．…·]{3,}|\s{2,})\s*\d+\s*$")
PAGE = re.compile(r"^(?:[—–-]\s*\d+\s*[—–-]|[IVX]{1,5})$")
CAPTION = re.compile(r"^(?:表\s*[\d一二三四五六七八九十]+|附表|专栏\s*[\d一二三四五六七八九十]+)")
NOTE = re.compile(r"^(?:注[：:]|备注[：:]|说明[：:]|单位[：:（(]|数据来源[：:])")
IMAGE_ONLY = re.compile(r"^(?:!\[[^\]]*\]\([^\n]*?\)\s*)+$")


def number(value):
    """仅解析条号/款号，不把年份和正文金额当结构编号。"""
    if value.isdigit():
        return int(value)
    digits = dict(zip("零〇一二三四五六七八九两", (0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2)))
    total = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in "十百千万":
            unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}[char]
            total += (current or 1) * unit
            current = 0
        else:
            return None
    return total + current


def heading(content):
    """拒绝长段落和句末完整语句；Markdown # 本身不能证明这是章节。"""
    marker = re.match(r"^(#{1,6})[ \t]+", content)
    clean = content[marker.end():] if marker else content
    article = ARTICLE.match(clean)
    if article:
        return {"kind": "article", "level": 7, "number": number(article.group(1)), "title": clean}
    if len(clean) > 80 or re.search(r"[。！？；;]", clean) or clean.endswith(("：", ":")):
        return None
    chapter = CHAPTER.match(clean)
    if chapter:
        level = {"篇": 1, "编": 1, "部分": 1, "章": 2, "节": 3}[chapter.group(2)]
        return {"kind": "section", "level": level, "title": clean}
    if re.match(r"^[一二三四五六七八九十百]+[、．.]", clean):
        return {"kind": "section", "level": 4, "title": clean}
    if re.match(r"^[（(][一二三四五六七八九十百]+[）)]", clean) and len(clean) <= 45 and "：" not in clean:
        return {"kind": "section", "level": 5, "title": clean}
    if re.match(r"^\d{1,2}(?:\.\d{1,2}){1,4}\s+\D", clean):
        return {"kind": "section", "level": min(6, clean.split()[0].count(".") + 3), "title": clean}
    if marker and not LIST.match(clean) and not re.match(r"^\d+(?:\.\d+)?\s*(?:%|％|万元|亿元)", clean):
        return {"kind": "section", "level": len(marker.group(1)), "title": clean}
    return None


def parse(source, config_hash):
    """返回排他基础单元与可嵌套章节。空白也必须作为单元保留下来。"""
    text = source.text
    lines = source.lines
    offsets = source.offsets
    body_line = next((i for i, x in enumerate(offsets) if x == source.body_start), 0)
    protected = {}
    hints_used = len(lines) < 50000
    if hints_used:
        parser = MarkdownIt("commonmark").enable("table")
        for token in parser.parse(text[source.body_start:]):
            if token.map and token.type in ("table_open", "fence", "code_block", "list_item_open"):
                a, b = [x + body_line for x in token.map]
                kind = "table" if token.type == "table_open" else ("list_item" if token.type == "list_item_open" else "literal")
                # 嵌套列表使用最外层完整项，保持续行和项目内段落，不重复生成重叠单元。
                if a not in protected or b > protected[a][0]:
                    protected[a] = (b, kind)
    # HTML 块可能含 <br>、跨行单元格；只提取边界，不将其转写回 Markdown。
    stack = []
    for match in re.finditer(r"</?table\b[^>]*>", text, re.I):
        if match.start() < source.body_start:
            continue
        if not match.group().startswith("</"):
            stack.append(match.start())
        elif stack:
            start = stack.pop()
            if not stack:
                a = max(0, __import__("bisect").bisect_right(offsets, start) - 1)
                b = __import__("bisect").bisect_left(offsets, match.end())
                protected[a] = (b, "table")
    for start in stack[:1]:
        a = __import__("bisect").bisect_right(offsets, start) - 1
        protected[a] = (len(lines), "table")

    units, structures, headings = [], [], []
    if source.body_start:
        units.append(make_unit(source, 0, source.body_start, "metadata", config_hash))
    i, in_toc = body_line, False
    while i < len(lines):
        start, end = offsets[i], offsets[i + 1]
        content = lines[i].strip()
        info = None
        if i in protected:
            finish, kind = protected[i]
            end = offsets[min(finish, len(lines))]
            next_i = min(finish, len(lines))
        else:
            next_i = i + 1
            if not content:
                kind = "whitespace"
            elif re.fullmatch(r"(?:#{1,6}\s*)?目\s*录", content):
                kind, in_toc = "toc", True
            elif TOC_ITEM.search(content) and (in_toc or content.startswith("第")):
                kind = "toc"
            elif PAGE.match(content):
                kind = "page_marker"
            else:
                in_toc = False
                plain = re.sub(r"^#{1,6}\s+", "", content)
                info = heading(content)
                if CAPTION.match(plain) and len(plain) <= 100:
                    kind, info = "caption", None
                elif NOTE.match(plain):
                    kind, info = "note", None
                elif info:
                    kind = info["kind"]
                elif IMAGE_ONLY.fullmatch(content):
                    kind = "image"
                elif CAPTION.match(content) and len(content) <= 100:
                    kind = "caption"
                elif NOTE.match(content):
                    kind = "note"
                elif LIST.match(re.sub(r"^#{1,6}\s+", "", content)):
                    kind = "list_item"
                else:
                    kind = "paragraph"
        # 连续普通行属于同一自然段；不能为单字断行制造几万个无意义子块。
        if units and kind in ("paragraph", "whitespace", "toc", "page_marker") and units[-1]["kind"] == kind and units[-1]["end"] == start:
            # 先延长边界，最后统一取原文，避免单字断行导致反复复制增长中的长段落。
            unit = units[-1]
            unit["end"] = end
        else:
            unit = make_unit(source, start, end, kind, config_hash)
            units.append(unit)
        if info:
            info.update({"start": start, "end": len(text), "unit_id": unit["id"]})
            headings.append(info)
        i = next_i

    for unit in units:
        unit.update(make_unit(source, unit["start"], unit["end"], unit["kind"], config_hash))

    # 栈只按真实识别的标题创建层级；无标题区域属于匿名文档根，绝不编造标题。
    stack = []
    root_id = stable_id("section", source.version, "root", config_hash)
    structures.append({"id": root_id, "document_id": source.version, "kind": "root", "level": 0,
                       "start": source.body_start, "end": len(text), "parent_id": None, "title": None,
                       "path": [], "scope_id": root_id})
    for info in headings:
        while stack and stack[-1]["level"] >= info["level"]:
            stack.pop()["end"] = info["start"]
        parent = stack[-1] if stack else structures[0]
        record = {**info, "id": stable_id("section", source.version, info["start"], config_hash),
                  "document_id": source.version, "parent_id": parent["id"],
                  "path": parent["path"] + [info["title"]],
                  "scope_id": parent["scope_id"] if info["kind"] == "article" else None}
        if record["scope_id"] is None:
            record["scope_id"] = record["id"]
        structures.append(record)
        stack.append(record)
    # 用有序扫描分配归属，避免全文单元与所有章节做笛卡尔比较。
    active, index = [structures[0]], 1
    for unit in units:
        while index < len(structures) and structures[index]["start"] <= unit["start"]:
            candidate = structures[index]
            while len(active) > 1 and active[-1]["end"] <= candidate["start"]:
                active.pop()
            active.append(candidate)
            index += 1
        while len(active) > 1 and active[-1]["end"] <= unit["start"]:
            active.pop()
        unit["structure_id"] = active[-1]["id"]
        unit["section_path"] = active[-1]["path"]
        if unit["kind"] == "metadata":
            unit["structure_id"] = None
    return units, structures, hints_used


def make_unit(source, start, end, kind, config_hash):
    return {"id": stable_id("unit", source.version, start, end, kind, config_hash),
            "document_id": source.version, "kind": kind, **source.segment(start, end)}
