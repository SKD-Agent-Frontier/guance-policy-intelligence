"""Scan the clean Markdown corpus for missing/clearly garbled frozen source text.

Usage:
    python web/backend/scripts/audit_policy_body_quality.py "语料库/Agent标准引用库"

Reports candidates; does not modify the corpus or RAG snapshots.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.policy_quality import body_status  # noqa: E402


def scan(root: Path) -> list[dict[str, str | int]]:
    results: list[dict[str, str | int]] = []
    for path in sorted(root.glob("*/*/*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter = re.match(r"\A---\r?\n.*?\r?\n---\r?\n", text, re.S)
        body = text[frontmatter.end():] if frontmatter else text
        metadata = text[:frontmatter.end()] if frontmatter else ""
        title = next(
            (line.partition(":")[2].strip().strip('"') for line in metadata.splitlines()
             if line.startswith("title:")), None,
        )
        status = body_status(body, title)
        # Distinguish tiny metadata-only fragments from a genuine policy text.
        if status == "available" and 0 < len(body.strip()) < 200:
            status = "short_body"
        if status != "available":
            results.append({
                "planid": path.stem, "status": status,
                "body_chars": len(body.strip()), "path": str(path),
            })
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus_root", type=Path)
    parser.add_argument("--csv", type=Path, help="Optional report output")
    args = parser.parse_args()
    rows = scan(args.corpus_root)
    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("missing", "suspected_garbled", "short_body")}
    print(f"quality candidates: {counts}; total={len(rows)}")
    for row in rows:
        print(f"{row['planid']}: {row['status']} ({row['body_chars']} chars)")
    if args.csv:
        with args.csv.open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=["planid", "status", "body_chars", "path"])
            writer.writeheader()
            writer.writerows(rows)
