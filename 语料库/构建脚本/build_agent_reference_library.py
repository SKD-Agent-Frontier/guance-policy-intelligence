#!/usr/bin/env python3
"""无损构建管策智研 Agent 标准引用库。

从仓库根目录运行：
    python "语料库/构建脚本/build_agent_reference_library.py"
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit


GROUPS = {
    "corpus": "corpus（代表性样本）",
    "edge_cases": "edge_cases（边界压力样本）",
}
CHUNK_SIZE = 1024 * 1024
INLINE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*(?:<(?P<angle>[^>]*)>|(?P<plain>[^\s)]+))(?:\s+[^)]*)?\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*(['\"])(?P<src>.*?)\1", re.IGNORECASE)


class BuildError(RuntimeError):
    """Raised when the reference library cannot be built safely."""


@dataclass(frozen=True)
class FileEntry:
    source: Path
    target_relative: Path
    group: str
    planid: str
    kind: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def source_state(root: Path) -> tuple[tuple[str, int, int], ...]:
    """A metadata snapshot that detects any source-tree mutation during a run."""
    return tuple(
        sorted(
            (str(item.relative_to(root)), item.stat().st_size, item.stat().st_mtime_ns)
            for item in root.rglob("*")
            if item.is_file()
        )
    )


def collect_entries(source_root: Path) -> tuple[list[FileEntry], dict[str, list[str]]]:
    if not source_root.is_dir():
        raise BuildError(f"输入目录不存在：{source_root}")

    entries: list[FileEntry] = []
    planids_by_group: dict[str, list[str]] = {}
    all_planids: set[str] = set()

    for output_group, input_group in GROUPS.items():
        group_root = source_root / input_group
        if not group_root.is_dir():
            raise BuildError(f"输入分组不存在：{group_root}")

        planids: list[str] = []
        for sample_dir in sorted(group_root.iterdir(), key=lambda path: path.name):
            if not sample_dir.is_dir():
                continue
            planid = sample_dir.name
            if not planid.isdigit():
                raise BuildError(f"发现非法 planid 目录：{sample_dir}")
            if planid in all_planids:
                raise BuildError(f"planid 跨分组重复：{planid}")
            all_planids.add(planid)
            planids.append(planid)

            markdown = sample_dir / f"{planid}.md"
            if not markdown.is_file() or markdown.is_symlink():
                raise BuildError(f"缺少同名 Markdown：{markdown}")
            entries.append(FileEntry(markdown, Path(output_group) / planid / markdown.name, output_group, planid, "md"))

            media_root = sample_dir / "media"
            if media_root.exists() and not media_root.is_dir():
                raise BuildError(f"media 不是目录：{media_root}")
            if media_root.is_dir():
                for media_file in sorted(media_root.rglob("*"), key=lambda path: str(path.relative_to(media_root))):
                    if media_file.is_dir():
                        continue
                    if not media_file.is_file() or media_file.is_symlink():
                        raise BuildError(f"media 中存在非普通文件：{media_file}")
                    relative_media = media_file.relative_to(media_root)
                    entries.append(
                        FileEntry(
                            media_file,
                            Path(output_group) / planid / "media" / relative_media,
                            output_group,
                            planid,
                            "media",
                        )
                    )
        planids_by_group[output_group] = planids

    if not entries:
        raise BuildError("未发现任何可构建样本")
    return entries, planids_by_group


def expected_directories(entries: Iterable[FileEntry]) -> set[Path]:
    directories = {Path("corpus"), Path("edge_cases")}
    for entry in entries:
        directories.add(entry.target_relative.parent)
        if entry.kind == "media":
            directories.add(entry.target_relative.parents[len(entry.target_relative.parts) - 3])
    return directories


def copy_entries(entries: Iterable[FileEntry], destination_root: Path) -> None:
    for entry in entries:
        destination = destination_root / entry.target_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry.source, destination)
        if sha256(entry.source) != sha256(destination):
            raise BuildError(f"复制后哈希不一致：{entry.target_relative}")


def verify_file_layout(entries: list[FileEntry], destination_root: Path) -> dict[str, int]:
    expected_files = {entry.target_relative for entry in entries}
    actual_files = {
        item.relative_to(destination_root)
        for item in destination_root.rglob("*")
        if item.is_file()
    }
    missing = sorted(expected_files - actual_files)
    extra = sorted(actual_files - expected_files)
    if missing or extra:
        details = []
        if missing:
            details.append("缺少文件：" + ", ".join(map(str, missing[:10])))
        if extra:
            details.append("额外文件：" + ", ".join(map(str, extra[:10])))
        raise BuildError("目标文件集合不符合契约；" + "；".join(details))

    expected_dirs = expected_directories(entries)
    actual_dirs = {
        item.relative_to(destination_root)
        for item in destination_root.rglob("*")
        if item.is_dir()
    }
    extra_dirs = sorted(actual_dirs - expected_dirs)
    if extra_dirs:
        raise BuildError("目标存在额外目录：" + ", ".join(map(str, extra_dirs[:10])))

    md_count = media_count = 0
    for entry in entries:
        destination = destination_root / entry.target_relative
        if sha256(entry.source) != sha256(destination):
            raise BuildError(f"验收哈希不一致：{entry.target_relative}")
        if entry.kind == "md":
            md_count += 1
        else:
            media_count += 1
    return {"md": md_count, "media": media_count}


def markdown_without_code_fences(text: str) -> str:
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "".join(kept)


def image_references(markdown_path: Path) -> list[str]:
    try:
        content = markdown_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise BuildError(f"无法按 UTF-8 解析图片引用：{markdown_path}") from error
    content = markdown_without_code_fences(content)
    references = []
    for match in INLINE_IMAGE_RE.finditer(content):
        references.append(match.group("angle") if match.group("angle") is not None else match.group("plain"))
    references.extend(match.group("src") for match in HTML_IMAGE_RE.finditer(content))
    return references


def validate_image_references(entries: list[FileEntry], source_root: Path, destination_root: Path) -> dict[str, object]:
    results: Counter[str] = Counter()
    source_missing: list[str] = []
    excluded_scope: list[str] = []
    copy_failures: list[str] = []
    markdown_entries = [entry for entry in entries if entry.kind == "md"]

    for entry in markdown_entries:
        source_markdown = entry.source
        target_markdown = destination_root / entry.target_relative
        sample_root = source_markdown.parent.resolve()
        target_sample_root = target_markdown.parent.resolve()
        for raw_reference in image_references(source_markdown):
            normalized = html.unescape(raw_reference.strip())
            parsed = urlsplit(normalized)
            if parsed.scheme.lower() in {"http", "https", "ftp", "mailto"} or parsed.netloc:
                results["external"] += 1
                continue
            if parsed.scheme.lower() == "data":
                results["embedded"] += 1
                continue
            if parsed.scheme:
                results["outside_scope"] += 1
                excluded_scope.append(f"{entry.target_relative}: {raw_reference}")
                continue

            local_part = unquote(parsed.path)
            if not local_part:
                results["outside_scope"] += 1
                excluded_scope.append(f"{entry.target_relative}: {raw_reference}")
                continue
            candidate = (source_markdown.parent / local_part).resolve()
            target_candidate = (target_markdown.parent / local_part).resolve()
            if not is_within(candidate, sample_root) or not is_within(target_candidate, target_sample_root):
                results["outside_scope"] += 1
                excluded_scope.append(f"{entry.target_relative}: {raw_reference}")
            elif not candidate.is_file():
                results["source_missing"] += 1
                source_missing.append(f"{entry.target_relative}: {raw_reference}")
            elif not is_within(candidate, sample_root / "media"):
                results["excluded_scope"] += 1
                excluded_scope.append(f"{entry.target_relative}: {raw_reference}")
            elif target_candidate.is_file():
                results["resolved"] += 1
            else:
                results["copy_failure"] += 1
                copy_failures.append(f"{entry.target_relative}: {raw_reference}")

    if copy_failures:
        raise BuildError("已复制的 media 图片引用未能解析：" + "; ".join(copy_failures[:10]))
    return {
        "counts": dict(sorted(results.items())),
        "source_missing": source_missing,
        "excluded_scope": excluded_scope,
        "copy_failures": copy_failures,
    }


def relative_display(path: Path, repository_root: Path) -> str:
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError:
        return path.as_posix()


def write_report(
    report_path: Path,
    repository_root: Path,
    source_root: Path,
    destination_root: Path,
    planids_by_group: dict[str, list[str]],
    checks: dict[str, int],
    image_results: dict[str, object],
) -> None:
    image_counts = image_results["counts"]
    source_missing = image_results["source_missing"]
    excluded_scope = image_results["excluded_scope"]
    lines = [
        "# Agent 标准引用库构建说明",
        "",
        "## 输入与输出",
        "",
        f"- 输入：`{relative_display(source_root, repository_root)}`",
        f"- 输出：`{relative_display(destination_root, repository_root)}`",
        "- 构建范围仅包含 `corpus（代表性样本）` 与 `edge_cases（边界压力样本）`；根目录中的其他目录未被扫描或修改。",
        "",
        "## 构建方式",
        "",
        "在仓库根目录执行：",
        "",
        "```powershell",
        'python "语料库/构建脚本/build_agent_reference_library.py"',
        "```",
        "",
        "脚本按字节复制 `{planid}.md` 与 `media/` 内的全部普通文件；不复制 HTML、origin 或其他辅助文件。若既有目标库与当前干净版不完全一致，脚本会失败且不删除目标中的任何文件。",
        "",
        "## 构建与验收结果",
        "",
        f"- Markdown：{checks['md']} 篇（corpus：{len(planids_by_group['corpus'])}；edge_cases：{len(planids_by_group['edge_cases'])}）。",
        f"- Media：{checks['media']} 个文件（从当前干净版实时统计）。",
        f"- planid：{sum(len(values) for values in planids_by_group.values())} 个，跨分组唯一；每个 Markdown 文件名均与 planid 一致。",
        "- Markdown 与 Media 均逐文件以 SHA-256 比对源端和目标端，全部一致。",
        "- 目标目录文件集合已校验：仅包含 Markdown 与 media 内文件；不含 HTML、origin、PDF、Word 或核验表。",
        "- 构建前后对输入目录进行了文件状态复核；脚本未写入干净版语料或原始数据库。",
        "",
        "## Markdown 图片引用校验",
        "",
        f"- 已解析的本地 media 引用：{image_counts.get('resolved', 0)}。",
        f"- 源数据中已失效的本地引用：{image_counts.get('source_missing', 0)}。",
        f"- 指向未复制范围或范围外的引用：{image_counts.get('excluded_scope', 0) + image_counts.get('outside_scope', 0)}。",
        f"- 外链：{image_counts.get('external', 0)}；内嵌 data 图片：{image_counts.get('embedded', 0)}。",
        "- 复制过程造成的图片解析失败：0。",
        "",
        "## 异常与未解决问题",
        "",
    ]
    if source_missing:
        by_document: Counter[str] = Counter(item.split(": ", 1)[0] for item in source_missing)
        lines.append("以下为源数据原有的 `media` 缺失引用；未修改正文，也未补造文件：")
        lines.append("")
        for document, count in sorted(by_document.items()):
            lines.append(f"- `{document}`：{count} 处。")
    elif excluded_scope:
        lines.append("存在未复制范围的图片引用；详见下列按文档汇总。")
    else:
        lines.append("未发现源端既有的图片引用异常。")
    if excluded_scope:
        by_document = Counter(item.split(": ", 1)[0] for item in excluded_scope)
        lines.append("")
        for document, count in sorted(by_document.items()):
            lines.append(f"- `{document}`：{count} 处未复制范围引用。")
    lines.extend(
        [
            "",
            "## 最终结论",
            "",
            "构建与全量文件哈希验收通过。标准引用库忠实保留干净版的 Markdown 与 Media；图片引用异常均为源数据既有状态，未进行修复或正文改写。",
            "",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="无损构建 Agent 标准引用库")
    script_path = Path(__file__).resolve()
    corpus_root = script_path.parent.parent
    parser.add_argument("--source", type=Path, default=corpus_root / "样例数据（干净版）")
    parser.add_argument("--output", type=Path, default=corpus_root / "Agent标准引用库")
    parser.add_argument("--report", type=Path, default=corpus_root / "数据修正说明" / "Agent标准引用库构建说明.md")
    args = parser.parse_args()

    source_root = args.source.resolve()
    destination_root = args.output.resolve()
    report_path = args.report.resolve()
    repository_root = corpus_root.parent
    if destination_root == source_root or is_within(destination_root, source_root):
        raise BuildError("目标目录不得位于输入干净版语料内")

    entries, planids_by_group = collect_entries(source_root)
    state_before = source_state(source_root)
    stage_parent = destination_root.parent
    if not stage_parent.is_dir():
        raise BuildError(f"目标上级目录不存在：{stage_parent}")

    built_now = False
    if destination_root.exists():
        checks = verify_file_layout(entries, destination_root)
    else:
        staging = Path(tempfile.mkdtemp(prefix=".agent-reference-stage-", dir=stage_parent))
        staged_output = staging / destination_root.name
        try:
            staged_output.mkdir()
            copy_entries(entries, staged_output)
            checks = verify_file_layout(entries, staged_output)
            image_results = validate_image_references(entries, source_root, staged_output)
            if source_state(source_root) != state_before:
                raise BuildError("构建期间检测到输入干净版语料发生变化，已停止发布")
            staged_output.replace(destination_root)
            built_now = True
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    image_results = validate_image_references(entries, source_root, destination_root)
    if source_state(source_root) != state_before:
        raise BuildError("验收期间检测到输入干净版语料发生变化")
    write_report(report_path, repository_root, source_root, destination_root, planids_by_group, checks, image_results)

    summary = {
        "status": "passed",
        "built_now": built_now,
        "groups": {group: len(planids) for group, planids in planids_by_group.items()},
        "planids": sum(len(planids) for planids in planids_by_group.values()),
        "markdown_files": checks["md"],
        "media_files": checks["media"],
        "image_references": image_results["counts"],
        "report": relative_display(report_path, repository_root),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as error:
        print(f"构建失败：{error}", file=sys.stderr)
        raise SystemExit(1)
