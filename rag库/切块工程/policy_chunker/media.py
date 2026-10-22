"""图片仅登记资源与出现位置；不执行 OCR，不把 alt 文本冒充图片内容。"""
from pathlib import Path
import re
from urllib.parse import unquote
from PIL import Image
from .source import sha, stable_id


def register(source, root, units, parents):
    assets, occurrences, issues = [], [], []
    by_path = {}
    media_dir = source.path.parent / "media"
    for path in sorted(media_dir.rglob("*")) if media_dir.exists() else []:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        asset_id = stable_id("asset", sha(raw))
        record = {"id": asset_id, "sha256": sha(raw), "bytes": len(raw),
                  "paths": [path.relative_to(root).as_posix()], "document_ids": [source.version],
                  "width": None, "height": None, "status": "registered", "recognition_status": "not_run"}
        try:
            with Image.open(path) as image:
                record["width"], record["height"] = image.size
                image.verify()
        except Exception as error:
            record["status"] = "unreadable"
            issues.append({"reason": "unreadable_media", "path": record["paths"][0], "detail": str(error)})
        assets.append(record); by_path[path.resolve()] = record
    matches = [(m.start(), m.end(), m.group(2), m.group(1)) for m in re.finditer(r"!\[([^\]]*)\]\(([^)\r\n]+)\)", source.text)]
    matches += [(m.start(), m.end(), m.group(1), None) for m in re.finditer(r'<img\b[^>]*?src=[\"\']([^\"\']+)[\"\'][^>]*>', source.text, re.I)]
    for start, end, target, alt in sorted(matches):
        if start < source.body_start:
            continue
        target = target.strip()
        if target.startswith("<") and ">" in target:
            target = target[1:target.index(">")]
        else:
            target = target.split(' "', 1)[0].split(" '", 1)[0]
        status, asset = "missing", None
        if re.match(r"^[a-z]+:", target, re.I):
            status = "external_not_fetched"
        else:
            resolved = (source.path.parent / unquote(target.split("#", 1)[0])).resolve()
            if not resolved.is_relative_to(source.path.parent.resolve()):
                status = "outside_document"
            elif resolved in by_path:
                asset = by_path[resolved]; status = asset["status"]
        parent = next((p for p in parents if p["start"] <= start and p["end"] >= end), None)
        unit = next((u for u in units if u["start"] <= start < u["end"]), None)
        record = {"id": stable_id("occurrence", source.version, start, end),
                  "document_id": source.version, "asset_id": asset["id"] if asset else None,
                  "target": target, "alt": alt, "source_segment": source.segment(start, end, "image_reference"),
                  "parent_id": parent["id"] if parent else None,
                  "structure_id": unit["structure_id"] if unit else None,
                  "status": status, "recognition_status": "not_run"}
        occurrences.append(record)
        if status != "registered":
            issues.append({"reason": "missing_media" if status == "missing" else status,
                           "range": {"start": start, "end": end}, "target": target})
    return assets, occurrences, issues
