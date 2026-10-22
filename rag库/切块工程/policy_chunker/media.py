"""图片仅登记资源与出现位置；不执行 OCR，不把 alt 文本冒充图片内容。"""
# 两种身份必须分开：asset 表示图片字节，occurrence 表示这张图在某份政策中的一次出现。
# 同一图片可在多个文档出现，允许共享 asset，但每次引用的文档和位置都要保留。
# 只登记 media 里的现有文件，不下载外链、不补造缺图、不推断页码或图中事实。
from pathlib import Path
import re
from urllib.parse import unquote
from PIL import Image
from .source import sha, stable_id


def register(source, root, units, parents):
    # 返回 (资源记录, 出现位置记录, 异常信息)。root 用于保存相对输入库的可迁移路径。
    assets, occurrences, issues = [], [], []
    by_path = {}
    media_dir = source.path.parent / "media"
    for path in sorted(media_dir.rglob("*")) if media_dir.exists() else []:
        # 连未在正文中引用的图片也登记；它们有 asset，没有可编造的正文 occurrence。
        if not path.is_file():
            continue
        raw = path.read_bytes()
        asset_id = stable_id("asset", sha(raw))
        # 身份取决于字节哈希，不依赖文件名；不同名称但字节相同的资源可在批次汇总时合并。
        record = {"id": asset_id, "sha256": sha(raw), "bytes": len(raw),
                  "paths": [path.relative_to(root).as_posix()], "document_ids": [source.version],
                  "width": None, "height": None, "status": "registered", "recognition_status": "not_run"}
        try:
            with Image.open(path) as image:
                # 读取尺寸并执行 Pillow 格式校验，仅作资源检查，不提取文字、裁剪或重采样。
                record["width"], record["height"] = image.size
                image.verify()
        except Exception as error:
            record["status"] = "unreadable"
            issues.append({"reason": "unreadable_media", "path": record["paths"][0], "detail": str(error)})
        assets.append(record); by_path[path.resolve()] = record
    matches = [(m.start(), m.end(), m.group(2), m.group(1)) for m in re.finditer(r"!\[([^\]]*)\]\(([^)\r\n]+)\)", source.text)]
    # 同时支持当前输入使用的 Markdown 内联图与 HTML img；范围覆盖整个原始引用写法。
    matches += [(m.start(), m.end(), m.group(1), None) for m in re.finditer(r'<img\b[^>]*?src=[\"\']([^\"\']+)[\"\'][^>]*>', source.text, re.I)]
    for start, end, target, alt in sorted(matches):
        if start < source.body_start:
            continue
        target = target.strip()
        # 下述处理只解析链接目标：剥离 <> 包装、可选标题及 URL 片段。
        # MD 原句和链接的原始写法仍由 source_segment 完整保存，不被替换。
        if target.startswith("<") and ">" in target:
            target = target[1:target.index(">")]
        else:
            target = target.split(' "', 1)[0].split(" '", 1)[0]
        status, asset = "missing", None
        if re.match(r"^[a-z]+:", target, re.I):
            # 外部协议链接只记录状态；正文中的外链不会触发网络请求。
            status = "external_not_fetched"
        else:
            resolved = (source.path.parent / unquote(target.split("#", 1)[0])).resolve()
            if not resolved.is_relative_to(source.path.parent.resolve()):
                # ../ 等不能让一篇政策把其他目录的文件误认作自己的媒体资源。
                status = "outside_document"
            elif resolved in by_path:
                asset = by_path[resolved]; status = asset["status"]
        parent = next((p for p in parents if p["start"] <= start and p["end"] >= end), None)
        # 找不到父块并非凭空补一个：纯图片文档没有文本父块，parent_id 可为空。
        unit = next((u for u in units if u["start"] <= start < u["end"]), None)
        record = {"id": stable_id("occurrence", source.version, start, end),
                  "document_id": source.version, "asset_id": asset["id"] if asset else None,
                  "target": target, "alt": alt, "source_segment": source.segment(start, end, "image_reference"),
                  "parent_id": parent["id"] if parent else None,
                  "structure_id": unit["structure_id"] if unit else None,
                  "status": status, "recognition_status": "not_run"}
        occurrences.append(record)
        if status != "registered":
            # 图片异常单独登记；是否影响原生正文取决于正文自身证据，不能一概删整篇。
            issues.append({"reason": "missing_media" if status == "missing" else status,
                           "range": {"start": start, "end": end}, "target": target})
    return assets, occurrences, issues
