"""In-memory DOCX/XLSX export of completed policy research."""
from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from xlsxwriter import Workbook

router = APIRouter(prefix="/api/exports", tags=["exports"])
MAX_PAYLOAD = 2_000_000
MAX_CELL = 32_000
MAX_ITEMS = 2_000
MAX_ANSWER = 150_000


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(default="", max_length=10_000)
    result: dict[str, Any]


def value(item: Any, limit: int = MAX_CELL) -> str:
    if not isinstance(item, (str, int, float, bool)):
        return ""
    raw = str(item)
    # WordprocessingML and SpreadsheetML reject several XML control characters.
    raw = "".join(ch if _valid_xml_character(ord(ch)) else "�" for ch in raw)
    return raw if len(raw) <= limit else raw[: limit - 1] + "…"


def _valid_xml_character(codepoint: int) -> bool:
    return (
        codepoint in (0x9, 0xA, 0xD)
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def records(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [item for item in data[:MAX_ITEMS] if isinstance(item, dict)]


def list_items(data: Any, limit: int) -> list[Any]:
    return data[:limit] if isinstance(data, list) else []


def excel_text(item: Any) -> str:
    raw = value(item)
    # Formula-like strings are literal text; also bound joined fields after joining.
    if raw.lstrip().startswith(("=", "+", "-", "@")):
        raw = "'" + raw
    return raw[:MAX_CELL]


def word_export(question: str, result: dict[str, Any]) -> bytes:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Microsoft YaHei", Pt(10.5)
    normal.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    doc.sections[0].top_margin = Cm(2.2)
    doc.sections[0].bottom_margin = Cm(2)
    doc.add_heading("政策研究结果", 0)
    if question:
        doc.add_heading("研究问题", level=1)
        doc.add_paragraph(value(question, 10_000))

    doc.add_heading("研究正文", level=1)
    answer = value(result.get("answer"), MAX_ANSWER + 1)
    if len(answer) > MAX_ANSWER:
        answer = answer[: MAX_ANSWER - 1] + "…"
    for line in (answer or "本轮无研究正文").replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)", line)
        if heading:
            doc.add_heading(heading.group(2), level=min(len(heading.group(1)) + 1, 4))
        elif re.match(r"^[一二三四五六七八九十]+、", line):
            doc.add_heading(line, level=2)
        else:
            doc.add_paragraph(line)

    claims = records(result.get("claims"))
    if claims:
        doc.add_heading("事实依据", level=1)
        for number, claim in enumerate(claims, 1):
            doc.add_paragraph(f"{number}. {value(claim.get('text'))}")
            for quote in list_items(claim.get("quotes"), 12):
                text = value(quote)
                if text.strip():
                    doc.add_paragraph("原文：" + text)
            chunk_ids = claim.get("chunk_ids")
            if isinstance(chunk_ids, list) and chunk_ids:
                doc.add_paragraph("证据区块：" + "、".join(value(item, 200) for item in chunk_ids[:30]))

    sources = records(result.get("evidence_refs"))
    if sources:
        doc.add_heading("参考文献", level=1)
        for number, source in enumerate(sources, 1):
            components = [
                f"《{value(source.get('title'))}》",
                value(source.get("area")),
                value(source.get("fyp")),
                "planid=" + value(source.get("planid")),
                value(source.get("section_path")),
            ]
            doc.add_paragraph(f"[{number}] " + " · ".join(part for part in components if part))

    if result.get("evidence_status") != "verified":
        doc.add_paragraph("提示：本轮证据未全部核验，请核对来源原文。")
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def tab(wb: Workbook, title: str, columns: list[str], rows: list[list[Any]], widths: list[int]) -> None:
    sheet = wb.add_worksheet(title)
    header = wb.add_format({"bold": True, "bg_color": "#E4EAF5", "font_color": "#263653"})
    body = wb.add_format({"valign": "top", "text_wrap": True})
    sheet.freeze_panes(1, 0)
    for index, column in enumerate(columns):
        sheet.write_string(0, index, column, header)
        sheet.set_column(index, index, widths[index])
    for row_index, row in enumerate(rows[:MAX_ITEMS], 1):
        for column_index, cell in enumerate(row):
            if type(cell) is int:
                sheet.write_number(row_index, column_index, cell, body)
            else:
                sheet.write_string(row_index, column_index, excel_text(cell), body)
    if rows:
        sheet.autofilter(0, 0, min(len(rows), MAX_ITEMS), len(columns) - 1)


def flatten(obj: Any, path: str = "", depth: int = 0):
    if depth >= 8:
        yield path, "[超出层级限制]"
    elif isinstance(obj, dict):
        for key, nested in list(obj.items())[:MAX_ITEMS]:
            yield from flatten(nested, (path + "." if path else "") + value(key), depth + 1)
    elif isinstance(obj, list):
        for index, nested in enumerate(obj[:MAX_ITEMS]):
            yield from flatten(nested, path + f"[{index}]", depth + 1)
    else:
        yield path, value(obj)


def excel_export(question: str, result: dict[str, Any]) -> bytes:
    output = io.BytesIO()
    workbook = Workbook(output, {
        "in_memory": True,
        "strings_to_formulas": False,
        "strings_to_urls": False,
    })
    try:
        summary = [
            ["研究问题", question],
            ["任务类型", result.get("task_type")],
            ["执行状态", result.get("status")],
            ["证据状态", result.get("evidence_status")],
        ]
        if result.get("evidence_status") != "verified":
            summary.append(["证据提示", "本轮证据未全部核验，请核对来源原文。"])
        answer = value(result.get("answer"), MAX_ANSWER + 1)
        if len(answer) > MAX_ANSWER:
            answer = answer[: MAX_ANSWER - 1] + "…"
        answer_lines = answer.splitlines()
        visible_lines = answer_lines[:MAX_ITEMS - len(summary) - 1]
        summary += [
            ["研究正文", line]
            for line in visible_lines
            if line.strip()
        ]
        if len(answer_lines) > len(visible_lines):
            summary.append(["研究正文", "[导出行数达到上限，后续内容已省略]"])
        tab(workbook, "研究结果", ["项目", "内容"], summary, [24, 96])
        tab(
            workbook,
            "核验事实",
            ["序号", "维度", "事实", "原文引句", "证据区块"],
            [
                [
                    number,
                    claim.get("dimension"),
                    claim.get("text"),
                    "；".join(filter(None, (value(item) for item in list_items(claim.get("quotes"), 12)))),
                    "、".join(value(item, 200) for item in list_items(claim.get("chunk_ids"), 50)),
                ]
                for number, claim in enumerate(records(result.get("claims")), 1)
            ],
            [9, 22, 72, 75, 42],
        )
        tab(
            workbook,
            "参考文献",
            ["序号", "planid", "标题", "地区", "时期", "章节路径", "chunk_id", "起点", "终点"],
            [
                [
                    number,
                    ref.get("planid"),
                    ref.get("title"),
                    ref.get("area"),
                    ref.get("fyp"),
                    ref.get("section_path"),
                    ref.get("chunk_id"),
                    ref.get("char_start"),
                    ref.get("char_end"),
                ]
                for number, ref in enumerate(records(result.get("evidence_refs")), 1)
            ],
            [9, 15, 65, 24, 25, 48, 35, 13, 13],
        )
        structured = result.get("structured")
        if isinstance(structured, (dict, list)):
            tab(
                workbook,
                "结构化研究",
                ["字段路径", "内容"],
                [[path, content] for path, content in list(flatten(structured))[:MAX_ITEMS]],
                [70, 96],
            )
    finally:
        workbook.close()
    return output.getvalue()


@router.post("/{format_name}")
async def export_result(format_name: str, payload: ExportRequest):
    if format_name not in {"docx", "xlsx"}:
        raise HTTPException(404, "不支持的导出格式")
    if len(payload.model_dump_json().encode("utf-8")) > MAX_PAYLOAD:
        raise HTTPException(413, "导出内容过大")
    result = payload.result
    if result.get("status") != "completed":
        raise HTTPException(422, "未完成的研究不允许导出为正式报告")

    data = word_export(payload.question, result) if format_name == "docx" else excel_export(payload.question, result)
    name = "guance-research-" + datetime.now(UTC).strftime("%Y%m%d")
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if format_name == "docx"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return StreamingResponse(io.BytesIO(data), media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{name}.{format_name}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })
