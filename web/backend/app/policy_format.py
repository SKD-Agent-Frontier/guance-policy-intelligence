"""Read-only formatting hints from original HTML. Never modifies source files.

Only explicit H1-H6 or headings identified by HTML classes are accepted.
Never serves HTML to the browser and never changes the frozen Markdown/offsets.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

MAX_HTML_SIZE = 8 * 1024 * 1024
MAX_HINTS = 500
_HEADING_CLASS = re.compile(
    r"(?:^|[\s_-])(?:mso)?(?:heading|标题)[\s_-]*([1-6])(?:$|[\s_-])", re.IGNORECASE
)


class _HeadingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hints: list[dict[str, str | int]] = []
        self._frames: list[dict] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "head"):
            self._skip += 1
            return
        if self._skip:
            return
        attrs = dict(attrs)
        level = int(tag[1:]) if re.fullmatch(r"h[1-6]", tag) else None
        if tag in ("p", "div") and level is None:
            css = attrs.get("class") or ""
            match = _HEADING_CLASS.search(css)
            if match:
                level = int(match.group(1))
        if level is not None and tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._frames.append({"tag": tag, "level": min(6, level + 1), "text": []})

    def handle_data(self, data):
        if not self._skip:
            for frame in self._frames:
                frame["text"].append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if self._frames and self._frames[-1]["tag"] == tag:
            frame = self._frames.pop()
            label = " ".join("".join(frame["text"]).split())
            if 2 <= len(label) <= 100 and len(self.hints) < MAX_HINTS:
                self.hints.append({"text": label, "level": frame["level"]})


def read_original_html_headings(root: Path, planid: str) -> dict:
    """Open only {root}/{corpus|edge_cases}/{planid}/{planid}.html."""
    root = root.resolve()
    for section in ("corpus", "edge_cases"):
        path = (root / section / planid / f"{planid}.html").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        if path.stat().st_size > MAX_HTML_SIZE:
            return {"source": "unavailable", "headings": []}
        parser = _HeadingParser()
        try:
            with path.open("r", encoding="utf-8", errors="replace") as source:
                parser.feed(source.read())
            parser.close()
        except (OSError, ValueError):
            return {"source": "unavailable", "headings": []}
        return {"source": "original_html", "headings": parser.hints}
    return {"source": "unavailable", "headings": []}
