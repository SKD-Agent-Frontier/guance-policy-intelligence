"""Presentation-only quality signals for unchanged frozen policy body text.

These flags do NOT repair, delete or normalize the source. They are not proof
that an original PDF/Word is unreadable, only that the published text is.
"""
from __future__ import annotations

import unicodedata
from typing import Literal

BodyStatus = Literal["available", "missing", "suspected_garbled"]


def body_status(body: str, title: str | None = None) -> BodyStatus:
    """Detect missing or badly extracted bodies without touching their source."""
    if not body.strip():
        return "missing"
    # A title alone is not a policy body, nor are remnants of a website footer.
    if title and "".join(body.split()) == "".join(title.split()):
        return "missing"
    if len(body.strip()) < 500 and sum(word in body for word in (
        "网站导航", "网站声明", "主办单位", "网站标识码", "ICP备",
    )) >= 3:
        return "missing"
    if len(body) < 800:
        return "available"
    # Sample across the entire frozen text, not only its title/TOC.
    mid = len(body) // 2
    sample = body[:8192] + body[max(0, mid - 4096):mid + 4096] + body[-8192:]
    sample = "".join(char for char in sample if not char.isspace())
    if len(sample) < 500:
        return "available"
    chinese = sum("\u3400" <= char <= "\u9fff" for char in sample) / len(sample)
    bad_control = sum((ord(char) < 32 or 127 <= ord(char) <= 159) for char in sample) / len(sample)
    replacements = sample.count("\ufffd") / len(sample)
    symbols = sum(unicodedata.category(char).startswith(("P", "S")) for char in sample) / len(sample)
    # No normal Chinese planning policy consists of thousands of punctuation
    # glyphs and C1 control bytes with essentially zero Han characters.
    if chinese < 0.02 and (bad_control > 0.005 or replacements > 0.03 or symbols > 0.55):
        return "suspected_garbled"
    return "available"
