"""Readable search previews independent of frozen text and literal matches."""
from __future__ import annotations

import re

MAX_PREVIEW_CHARS = 172
IMAGE = re.compile(r"!\[[^\]\n]*\]\(\s*(?:media/|https?://)[^)]*\)", re.I)
HEADING = re.compile(r"^\s*#{1,6}\s*")
HTML = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")
TABLE_RULE = re.compile(r"^:?-{3,}:?$")
PUNCTUATION = "。！？；"


def _duplicate_title(line: str, title: str | None) -> bool:
    if not title:
        return False
    normalize = lambda value: SPACE.sub("", HEADING.sub("", value)).strip("“”\"'·:：")
    return normalize(line) == normalize(title)


def _tidy(line: str) -> str:
    line = IMAGE.sub("", line)
    line = HEADING.sub("", line)
    line = HTML.sub("", line)
    line = line.replace("**", "").replace("__", "").replace("\x60", "")
    return SPACE.sub(" ", line).strip()


def _table_row(line: str) -> str | None:
    if "|" not in line:
        return None
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 2:
        return None
    if all(TABLE_RULE.fullmatch(cell.replace(" ", "")) for cell in cells):
        return ""
    return "表格：" + " · ".join(cell for cell in cells if cell)


def _excerpt(line: str, keyword: str | None) -> str:
    clean = _tidy(line)
    if len(clean) <= MAX_PREVIEW_CHARS:
        return clean
    pos = clean.find(keyword) if keyword else 0
    pos = max(0, pos)
    start = max(0, pos - 60)
    boundary = max((clean.rfind(char, max(0, pos - 150), pos)
                    for char in PUNCTUATION), default=-1)
    if boundary >= 0:
        start = boundary + 1
    elif start:
        comma = max(clean.rfind("，", start, pos), clean.rfind(",", start, pos))
        if comma >= 0:
            start = comma + 1
    end = min(len(clean), max(start + MAX_PREVIEW_CHARS, pos + (len(keyword) if keyword else 0)))
    excerpt = clean[start:end].strip(" \t，,")
    if start:
        excerpt = "…" + excerpt
    if end < len(clean):
        excerpt = excerpt.rstrip(" \t，,") + "…"
    return excerpt


def search_preview(body: str, title: str | None, keyword: str) -> str | None:
    """Choose a readable excerpt without changing source positions or match fields."""
    fallback: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line or _duplicate_title(line, title):
            continue
        if line in ("---", "***") or line.startswith(("![", "\x60\x60\x60", "<!--")):
            continue
        table = _table_row(line)
        text = table if table is not None else line
        readable = _tidy(text)
        if len(readable.strip("· -#|")) < 5:
            continue
        if fallback is None and table is None and not HEADING.match(line):
            fallback = _excerpt(text, None)
        if keyword in line and not (HEADING.match(line) and len(readable) < 65):
            result = _excerpt(text, keyword)
            if result:
                return result
    return fallback
