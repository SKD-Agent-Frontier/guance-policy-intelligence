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
    def normalize(value):
        return SPACE.sub("", HEADING.sub("", value)).strip("“”\"'·:：")
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


def _blocks(body: str, title: str | None):
    """Coalesce source hard-wraps without touching the original or its offsets."""
    pending: list[str] = []
    at_start = True

    def flush():
        nonlocal pending
        if not pending:
            return None
        joined = pending[0]
        for next_line in pending[1:]:
            gap = (" " if joined[-1:].isascii() and joined[-1:].isalnum()
                   and next_line[:1].isascii() and next_line[:1].isalnum()
                   else "")
            joined += gap + next_line
        pending = []
        return joined

    compact_title = SPACE.sub("", title or "").strip("“”\"'·:：")
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            item = flush()
            if item:
                yield item, False
                at_start = False
            continue
        if line.startswith(("![", "\x60\x60\x60", "<!--")) or line in ("---", "***"):
            item = flush()
            if item:
                yield item, False
                at_start = False
            continue
        # Many source files repeat the title in two or three wrapped lines.
        compact_line = SPACE.sub("", HEADING.sub("", line)).strip("“”\"'·:：")
        if _duplicate_title(line, title) or (at_start and len(compact_line) >= 7
                                              and compact_line in compact_title):
            continue
        table = _table_row(line)
        if table is not None:
            item = flush()
            if item:
                yield item, False
                at_start = False
            if table:
                yield table, True
                at_start = False
            continue
        if HEADING.match(line):
            item = flush()
            if item:
                yield item, False
                at_start = False
            # An isolated heading is not a useful search preview.
            continue
        pending.append(line)
    item = flush()
    if item:
        yield item, False


def search_preview(body: str, title: str | None, keyword: str) -> str | None:
    """Choose readable context; raw matches still carry exact source code-point offsets."""
    fallback: str | None = None
    for line, is_table in _blocks(body, title):
        readable = _tidy(line)
        if len(readable.strip("· -#|")) < 6:
            continue
        if fallback is None and not is_table and len(readable) >= 14:
            fallback = _excerpt(line, None)
        if keyword in line:
            result = _excerpt(line, keyword)
            if result:
                return result
    return fallback
