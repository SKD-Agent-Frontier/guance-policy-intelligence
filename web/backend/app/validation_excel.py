"""Read the validation workbook template and export execution results in memory."""
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from xlsxwriter import Workbook

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
HEADERS_IN = ("问题", "预期答案")
HEADERS_OUT = ("问题", "预期答案", "智能体实际回复", "耗时(秒)", "执行状态", "正确性评估")
MAX_ROWS = 500
MAX_UPLOAD_BYTES = 3 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_ZIP_FILES = 150
MAX_CELL_CHARS = 32000
MAX_QUESTION_CHARS = 4000
MAX_TOTAL_INPUT_CHARS = 4_000_000


class TemplateError(ValueError):
    """An uploaded workbook does not match the supported validation template."""


def parse_excel(data: bytes) -> list[dict]:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise TemplateError("上传文件为空或大于3MB")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            files = book.infolist()
            if len(files) > MAX_ZIP_FILES or sum(item.file_size for item in files) > MAX_UNCOMPRESSED_BYTES:
                raise TemplateError("Excel解压内容过大")
            workbook = ET.fromstring(book.read("xl/workbook.xml"))
            sheet = workbook.find("m:sheets/m:sheet", NS)
            if sheet is None:
                raise TemplateError("没有工作表")
            relationships = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
            target = next((item.get("Target") for item in relationships
                           if item.get("Id") == sheet.attrib.get(RID)), None)
            if not target:
                raise TemplateError("无法读取工作表")
            sheet_path = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else "xl/" + target
            )
            if not sheet_path.startswith("xl/worksheets/"):
                raise TemplateError("工作表路径无效")
            root = ET.fromstring(book.read(sheet_path))
            shared = []
            if "xl/sharedStrings.xml" in book.namelist():
                strings = ET.fromstring(book.read("xl/sharedStrings.xml"))
                shared = ["".join(text.text or "" for text in item.findall(".//m:t", NS))
                          for item in strings.findall("m:si", NS)]
    except TemplateError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError, IndexError, StopIteration) as exc:
        raise TemplateError("不是有效的xlsx文件") from exc

    rows = []
    for node in root.findall("m:sheetData/m:row", NS):
        values = {}
        for cell in node.findall("m:c", NS):
            match = re.fullmatch(r"([A-Z]+)[0-9]+", cell.get("r", ""))
            if not match or match.group(1) not in ("A", "B"):
                continue
            if cell.find("m:f", NS) is not None:
                raise TemplateError("问题和预期答案不能是公式")
            raw = cell.findtext("m:v", "", NS)
            if cell.get("t") == "s":
                try:
                    raw = shared[int(raw)]
                except (ValueError, IndexError) as exc:
                    raise TemplateError("字符串索引损坏") from exc
            elif cell.get("t") == "inlineStr":
                raw = "".join(text.text or "" for text in cell.findall(".//m:is/m:t", NS))
            if len(raw) > MAX_CELL_CHARS:
                raise TemplateError("单元格内容过长")
            values[match.group(1)] = raw.strip()
        if values:
            rows.append(values)
            if len(rows) > MAX_ROWS + 1:
                raise TemplateError("最多支持500道题")
    if not rows or (rows[0].get("A"), rows[0].get("B")) != HEADERS_IN:
        raise TemplateError("首行应依次为「问题」「预期答案」")

    result = []
    total_input_chars = 0
    for row in rows[1:]:
        question, expected = row.get("A", ""), row.get("B", "")
        if not question:
            if expected:
                raise TemplateError("存在预期答案但问题为空的行")
            continue
        if len(question) > MAX_QUESTION_CHARS:
            raise TemplateError("单题问题长度不能超过4000字")
        total_input_chars += len(question) + len(expected)
        if total_input_chars > MAX_TOTAL_INPUT_CHARS:
            raise TemplateError("问题与预期答案总长度过大")
        result.append({
            "question": question,
            "expected": expected,
            "answer": "",
            "duration": None,
            "status": "待运行",
            "evaluation": "未评估",
        })
    if not result:
        raise TemplateError("没有有效问题")
    return result


def export_excel(rows: list[dict]) -> bytes:
    output = io.BytesIO()
    workbook = Workbook(output, {
        "in_memory": True,
        "strings_to_formulas": False,
        "strings_to_urls": False,
    })
    try:
        sheet = workbook.add_worksheet("Sheet1")
        title = workbook.add_format({"bold": True, "bg_color": "#EBF2FB"})
        wrap = workbook.add_format({"text_wrap": True, "valign": "top"})
        for column, heading in enumerate(HEADERS_OUT):
            sheet.write_string(0, column, heading, title)
        for column, width in enumerate((44, 54, 80, 15, 18, 16)):
            sheet.set_column(column, column, width)
        sheet.freeze_panes(1, 0)
        for index, row in enumerate(rows, 1):
            for column, key in enumerate(("question", "expected", "answer")):
                sheet.write_string(index, column, str(row.get(key, ""))[:MAX_CELL_CHARS], wrap)
            duration = row.get("duration")
            if type(duration) in (float, int):
                sheet.write_number(index, 3, round(duration, 1))
            sheet.write_string(index, 4, row.get("status", ""), wrap)
            sheet.write_string(index, 5, row.get("evaluation", "未评估"), wrap)
        sheet.autofilter(0, 0, len(rows), len(HEADERS_OUT) - 1)
    finally:
        workbook.close()
    return output.getvalue()
