"""Locate printed policy tables of contents in frozen text, without changing it.

All ranges are Unicode code-point offsets in the *original* body, end-exclusive.
Only high-confidence page-numbered TOCs near the opening of a document qualify.
"""
from __future__ import annotations

import re

_TOC_HEADING = re.compile(r"^\s*目\s*录\s*$")
_CHAPTER = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千0-9]+[编篇部章节]|"
    r"[（(][一二三四五六七八九十0-9]+[）)]|"
    r"[一二三四五六七八九十]+[、．.])"
)
_LEADER = re.compile(
    r"(?:[.．·•…⋯。﹒:：]{4,}\s*[0-9０-９]{0,3}|"
    r"[.．·•…⋯。﹒:：]{2,}\s*[0-9０-９]{1,3})\s*$"
)
_ATTACHED = re.compile(r"(?:[一-龥）)]|[）)]\s+)[0-9０-９]{1,3}\s*$")
_PAPER_PAGE = re.compile(r"^\s*[-—－]\s*\d{1,4}\s*[-—－]\s*$")


def printed_toc_range(body: str) -> tuple[int, int] | None:
    """Find a printed index without mistaking real chapter text for a TOC.

    Some PDF extractions drop printed page numbers while leaving dotted
    leaders intact. Two nearby leader-bearing entries plus an explicit
    目录 heading are still strong evidence; without that heading we require
    four complete numbered entries. Return original code-point offsets.
    """
    if not body:
        return None

    # The original TOC, if present, is a front-matter artifact.
    fragment = body[:min(len(body), 16000)]
    rows = fragment.splitlines(keepends=True)
    offsets: list[int] = []
    position = 0
    for row in rows:
        offsets.append(position)
        position += len(row)

    heading = next((i for i, row in enumerate(rows[:160])
                    if _TOC_HEADING.fullmatch(row.strip())), None)

    def entry(row: str) -> bool:
        text = row.strip()
        return 6 <= len(text) <= 180 and bool(_CHAPTER.match(text)) and bool(
            _LEADER.search(text) or _ATTACHED.search(text)
        )

    if heading is not None:
        # Source PDF page breaks often occur inside the printed index.
        candidates = [i for i in range(heading + 1, min(len(rows), heading + 180))
                      if entry(rows[i])]
        if len(candidates) < 2:
            return None
        # Stop at the first large gap: do not suppress subsequent real chapters
        # merely because they contain a numeric sentence ending.
        first, last = candidates[0], candidates[0]
        for candidate in candidates[1:]:
            if candidate - last > 10:
                break
            last = candidate
        if last == first:
            return None
        start_line = heading
        # Single detached PDF page number immediately ahead of 目录 belongs
        # to front matter, not policy prose.
        if heading > 0 and re.fullmatch(r"[0-9０-９]{1,3}", rows[heading - 1].strip()):
            start_line -= 1
        start = offsets[start_line]
    else:
        candidates = [i for i, row in enumerate(rows[:140]) if entry(row)]
        if len(candidates) < 4:
            return None
        first, last = candidates[0], candidates[0]
        count = 1
        for candidate in candidates[1:]:
            if candidate - last > 5:
                break
            last = candidate
            count += 1
        if count < 4:
            return None
        start = offsets[first]

    last_line = last + 1
    # Include adjacent paper-page separators, but never the real text of
    # "前言", "第一章", etc. that starts after the printed table.
    while last_line < len(rows) and (
        not rows[last_line].strip() or _PAPER_PAGE.fullmatch(rows[last_line].strip())
    ):
        last_line += 1
    end = offsets[last_line] if last_line < len(rows) else position
    if not (0 <= start < end <= len(body)):
        return None
    return start, end
