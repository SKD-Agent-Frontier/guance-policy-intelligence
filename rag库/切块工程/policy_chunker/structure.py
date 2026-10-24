"""Markdown 仅提供线索；结构与片段位置始终映射到未经改写的原文。"""
# 本模块输出两种不同层次的数据：
# 1. units：排他基础单元，包含元数据、空白、目录等，顺序拼接可还原全文。
# 2. structures：可嵌套章节/条文，例如“第一章”包含“第一条”，不要求排他。
# 解析时允许 strip() 创建识别视图，但每个输出的 text 都由 Source.segment 取原文。
import re
from markdown_it import MarkdownIt
from .source import stable_id

HAN = "〇零一二三四五六七八九十百千万两0-9"
# ^ 限制在行首识别章节/条文，防止正文“按照第八条执行”被切成一个新条文。
# LIST 的负向判断用于避开紧接数字的小数形式；它只是结构线索，不是语义证明。
ARTICLE = re.compile(rf"^第([{HAN}]+)条(?:\s|[、：:]|$)")
CHAPTER = re.compile(rf"^第([{HAN}]+)(篇|编|部分|章|节)(?:\s|[、：:]|$)")
LIST = re.compile(r"^(?:[（(][一二三四五六七八九十百0-9]+[）)]|\d{1,3}[、．.]\s*(?!\d))")
TOC_ITEM = re.compile(r"(?:[.．…·]{3,}|\s{2,})\s*\d+\s*$")
# 目录通常带点引线与页码；PAGE 单独登记常见页码行，不能当作正文段落。
PAGE = re.compile(r"^(?:[—–-]\s*\d+\s*[—–-]|[IVX]{1,5})$")
CAPTION = re.compile(r"^(?:表\s*[\d一二三四五六七八九十]+|附表|专栏\s*[\d一二三四五六七八九十]+)")
# caption/note 是表题、单位、附注的候选类型，后续还要结合邻接表格决定归组。
NOTE = re.compile(r"^(?:注[：:]|备注[：:]|说明[：:]|单位[：:（(]|数据来源[：:])")
IMAGE_ONLY = re.compile(r"^(?:!\[[^\]]*\]\([^\n]*?\)\s*)+$")


def number(value):
    """仅解析条号/款号，不把年份和正文金额当结构编号。"""
    if value.isdigit():
        return int(value)
    digits = dict(zip("零〇一二三四五六七八九两", (0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2)))
    # current 暂存最近的数字，遇到十/百等单位后累加；例如“二十三”得到 23。
    # 这是常见结构编号的简式解析，不用于任意中文金额或复杂中文数词的换算。
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
    # clean 仅用于判别；即使去掉了前导 ##，最终单位仍保留原始 ## 和原始空白。
    article = ARTICLE.match(clean)
    if article:
        # 条文单独放在相对层级 7，便于章节栈让篇/章/节成为它的上级。
        # 这里的 level 是本工程层级约定，不直接等于原文 Markdown # 的数量。
        return {"kind": "article", "level": 7, "number": number(article.group(1)), "title": clean}
    if len(clean) > 80 or re.search(r"[。！？；;]", clean) or clean.endswith(("：", ":")):
        # 长度与标点限制是保守启发式：整段正文即使带 ##，也不强行认作标题。
        # 冒号结尾可能是列表引导语，应保留为正文供父块关联共同条件。
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
        # 识别“1.2.1 编制依据”这类结构号，要求编号后有空白及非数字正文，
        # 区别于“1.2万元”。返回的层级上限为 6，给条文层级 7 留出位置。
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
    # Markdown parser 只接收正文，其 map 是正文内行号；body_line 把它换回整文件行号。
    # protected[a]=(b,kind) 保护 [a,b) 行：表格、代码块、完整列表项不能按单行拆散。
    protected = {}
    hints_used = len(lines) < 50000
    # 极端单字断行会制造数万行，跳过解析器线索以控制开销；仍用原文边界规则扫描。
    # 返回 hints_used 让上层登记这种降级，不能把跳过解析器当成原文已“修复”。
    if hints_used:
        parser = MarkdownIt("commonmark").enable("table")
        for token in parser.parse(text[source.body_start:]):
            if token.map and token.type in ("table_open", "fence", "code_block", "list_item_open"):
                a, b = [x + body_line for x in token.map]
                # 只使用 token.map 的位置，不采用 parser 可能规范化过的 token.content。
                kind = "table" if token.type == "table_open" else ("list_item" if token.type == "list_item_open" else "literal")
                # 嵌套列表使用最外层完整项，保持续行和项目内段落，不重复生成重叠单元。
                if a not in protected or b > protected[a][0]:
                    protected[a] = (b, kind)
    # HTML 块可能含 <br>、跨行单元格；只提取边界，不将其转写回 Markdown。
    stack = []
    # HTML table 可能嵌套，因此用栈找最外层原文边界；嵌套网格能否安全分行，
    # 由 chunking.TableGrid 另行判断。本阶段只负责把整个表保护为一个单元。
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
        # 缺少 </table> 时保守保护到文件末尾，防止把疑似表内文字任意拆成政策段落。
        a = __import__("bisect").bisect_right(offsets, start) - 1
        protected[a] = (len(lines), "table")

    units, structures, headings = [], [], []
    if source.body_start:
        units.append(make_unit(source, 0, source.body_start, "metadata", config_hash))
    i, in_toc = body_line, False
    # i 是原始文件行序号；next_i 允许一次跨过完整保护块，其他情况只前进一行。
    # kind 分类有先后关系：先保留表格/代码等完整块，再判断目录、标题和普通正文。
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
                # 有目录上下文，或本行本身像带页码的“第×章”，才按目录项排除。
                kind = "toc"
            elif PAGE.match(content):
                kind = "page_marker"
            else:
                in_toc = False
                plain = re.sub(r"^#{1,6}\s+", "", content)
                info = heading(content)
                if CAPTION.match(plain) and len(plain) <= 100:
                    # 表题常被写成 ## 表1；优先标作 caption，以便与表体一起保留。
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
        # 前面仅延长了同类单元的边界；现在一次性取回完整原文，并按最终范围生成 ID。
        unit.update(make_unit(source, unit["start"], unit["end"], unit["kind"], config_hash))

    # 栈只按真实识别的标题创建层级；无标题区域属于匿名文档根，绝不编造标题。
    stack = []
    root_id = stable_id("section", source.version, "root", config_hash)
    # 文档根是位置容器，不是编造的章节。title=None、path=[] 表示没有可靠标题。
    structures.append({"id": root_id, "document_id": source.version, "kind": "root", "level": 0,
                       "start": source.body_start, "end": len(text), "parent_id": None, "title": None,
                       "path": [], "scope_id": root_id})
    for info in headings:
        # 遇到同级或更高层级标题时，前一章节在新标题起点结束；更低层级则继续嵌套。
        # 例如第一章(level=2) -> 第一节(level=3) -> 第二章(level=2)，
        # 会先结束第一节和第一章，再将第二章挂到文档根下。
        while stack and stack[-1]["level"] >= info["level"]:
            stack.pop()["end"] = info["start"]
        parent = stack[-1] if stack else structures[0]
        record = {**info, "id": stable_id("section", source.version, info["start"], config_hash),
                  "document_id": source.version, "parent_id": parent["id"],
                  "path": parent["path"] + [info["title"]],
                  "scope_id": parent["scope_id"] if info["kind"] == "article" else None}
        if record["scope_id"] is None:
            # 普通章节成为自己的编号作用域，条文沿用上级作用域。
            # relations 模块会用它区分不同章节中重复出现的“第一条”。
            record["scope_id"] = record["id"]
        structures.append(record)
        stack.append(record)
    # 用有序扫描分配归属，避免全文单元与所有章节做笛卡尔比较。
    active, index = [structures[0]], 1
    # 章节与单元已按位置有序，用活动栈同步向前走；不逐单位扫描全部章节。
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
    # kind 保存结构判断；segment 保存同一范围的原文和行号。
    # ID 还绑定来源版本及规则配置，防止把不同版本中位置相同的文字当成同一个单元。
    return {"id": stable_id("unit", source.version, start, end, kind, config_hash),
            "document_id": source.version, "kind": kind, **source.segment(start, end)}
