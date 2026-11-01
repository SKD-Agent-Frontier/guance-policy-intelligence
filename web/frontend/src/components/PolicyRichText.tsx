import { useState } from "react";
import { apiBaseUrl } from "../api/config";

/** Whitelisted local media basename; never render remote or arbitrary file paths. */
const IMAGE_LINK = /^\s*!\[([^\]]*)\]\(\s*media\/([A-Za-z0-9_.-]+)(?:\s+"[^"]*")?\s*\)\s*$/i;
const HEADING = /^\s*(#{1,6})\s+(.+)$/;
const SECTION = /^(?:第[一二三四五六七八九十百千0-9]+[章节篇部分]|[一二三四五六七八九十]+[、．.]|[（(][一二三四五六七八九十0-9]+[）)]|\d+[、．.]\s*|[-*•]\s+)/;
const RULE = /^:?-{3,}:?$/;

function tableCells(line: string): string[] {
  const raw = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return raw.split(/(?<!\\)\|/).map(cell => cell.replace(/\\\|/g, "|").trim());
}

function tableHeader(lines: string[], index: number): string[] | null {
  if (index + 1 >= lines.length || !lines[index].includes("|")) return null;
  const headers = tableCells(lines[index]);
  const dividers = tableCells(lines[index + 1]);
  if (headers.length < 2 || headers.length > 24 || dividers.length !== headers.length
    || !dividers.every(cell => RULE.test(cell.replace(/\s/g, "")))) return null;
  return headers;
}

function paragraphLines(lines: string[], softWrap: boolean): string[] {
  if (!softWrap) return lines;
  const merged: string[] = [];
  for (const source of lines) {
    const line = source.trim();
    const last = merged.at(-1) ?? "";
    if (!last || SECTION.test(line) || SECTION.test(last)
      || /[。！？；;：:]$/.test(last) || /^\d+$/.test(last) || /^\d+$/.test(line)) {
      merged.push(line);
    } else {
      const gap = /[A-Za-z0-9]$/.test(last) && /^[A-Za-z0-9]/.test(line) ? " " : "";
      merged[merged.length - 1] = last + gap + line;
    }
  }
  return merged;
}

function PolicyImage({ planid, name, alt }: { planid: string; name: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <div className="catalog-media-missing" role="note">图片暂不可用：{alt || name}</div>;
  return <figure className="catalog-media-figure">
    <img
      src={`${apiBaseUrl}/api/policies/${encodeURIComponent(planid)}/media/${encodeURIComponent(name)}`}
      alt={alt || name}
      loading="lazy"
      onError={() => setFailed(true)}
    />
    {alt && <figcaption>{alt}</figcaption>}
  </figure>;
}

/**
 * Render structured Markdown tables and local images without injecting HTML.
 * All remaining policy source is rendered as ordinary escaped text.
 * Never alters the underlying source, code-point offsets, or pagination.
 */
export function PolicyRichText({ text, planid, softWrap = false }: {
  text: string; planid: string; softWrap?: boolean;
}) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let prose: string[] = [];
  let key = 0;
  const flushProse = () => {
    if (!prose.length) return;
    for (const paragraph of paragraphLines(prose, softWrap)) {
      blocks.push(<p className="catalog-prose" key={key++}>{paragraph}</p>);
    }
    prose = [];
  };
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { flushProse(); index++; continue; }

    const image = IMAGE_LINK.exec(line);
    if (image) {
      flushProse();
      blocks.push(<PolicyImage key={key++} planid={planid} alt={image[1]} name={image[2]} />);
      index++;
      continue;
    }

    const headers = tableHeader(lines, index);
    if (headers) {
      flushProse();
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        const row = tableCells(lines[index]);
        if (row.length !== headers.length) break;
        rows.push(row);
        index++;
      }
      blocks.push(<div key={key++} className="catalog-table-scroll" role="region" aria-label="政策原文表格" tabIndex={0}>
        <table className="catalog-markdown-table">
          <thead><tr>{headers.map((cell, i) => <th key={i} scope="col">{cell}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) =>
            <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody>
        </table>
      </div>);
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushProse();
      blocks.push(<div className={`catalog-markdown-heading level-${heading[1].length}`} key={key++}>{heading[2]}</div>);
      index++;
      continue;
    }
    if (softWrap && prose.length && (SECTION.test(line.trim())
      || SECTION.test(prose[prose.length - 1].trim())
      || /[。！？；;：:]$/.test(prose[prose.length - 1].trim()))) flushProse();
    prose.push(line);
    index++;
  }
  flushProse();
  return <div className="catalog-rich-text">{blocks}</div>;
}
