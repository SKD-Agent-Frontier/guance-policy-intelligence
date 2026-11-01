import { useState } from "react";
import { apiBaseUrl } from "../api/config";
import type { PolicySection } from "../types";

/** Whitelisted local media basename; never render remote or arbitrary file paths. */
const IMAGE_LINK = /^\s*!\[([^\]]*)\]\s*\(\s*media\/([A-Za-z0-9_.-]+)(?:\s+"[^"]*")?\s*\)\s*$/i;
const MARKDOWN_HEADING = /^\s*(#{1,6})\s+(.+?)\s*#*\s*$/;
const SECTION = /^(?:第[一二三四五六七八九十百千0-9]+[章节篇部分]|[一二三四五六七八九十]+[、．.]|[（(][一二三四五六七八九十0-9]+[）)]|\d+[、．.]\s*|[-*•]\s+)/;
type HeadingLevel = 2 | 3 | 4 | 5 | 6;
export type OriginalHeadingHint = { text: string; level: number };
function normalizeHeadingText(value: string): string {
  return value.replace(/[\s　]/g, "").replace(/^#+|#+$/g, "");
}


/** H1 belongs to the document title; source heading levels start from H2. */
export function policyHeading(
  line: string, originalHints: readonly OriginalHeadingHint[] = [],
): { level: HeadingLevel; text: string } | null {
  const source = line.trim();
  // 序言 is a top-level section even when source HTML style hints differ.
  if (/^序\s*言$/.test(source.replace(/^#{1,6}\s*/, ""))) {
    return { level: 2, text: "序言" };
  }
  const normalized = normalizeHeadingText(source.replace(/^#{1,6}\s*/, ""));
  const original = originalHints.find(hint => normalizeHeadingText(hint.text) === normalized
    && Number.isInteger(hint.level) && hint.level >= 2 && hint.level <= 6);
  if (original) return { level: original.level as HeadingLevel, text: source.replace(/^#{1,6}\s*/, "") };
  const markdown = MARKDOWN_HEADING.exec(source);
  if (markdown) return { level: Math.min(6, markdown[1].length + 1) as HeadingLevel, text: markdown[2] };
  // OCR/PDF hard-wrapped sentences can begin with numbering: don't turn entire
  // paragraphs into headings unless the line itself is heading-like.
  // Numbered body paragraphs frequently begin with 一、 or （一）.
  // Without a verified structure position, never promote a full sentence.
  if (source.length > 48 || /[。！？；;，,：:]/.test(source)) return null;
  if (/^第[一二三四五六七八九十百千0-9]+[编篇部章]/.test(source)) return { level: 2, text: source };
  if (/^第[一二三四五六七八九十百千0-9]+节/.test(source)) return { level: 3, text: source };
  if (/^[一二三四五六七八九十百千]+[、．.]\s*\S/.test(source)) return { level: 3, text: source };
  if (/^[（(][一二三四五六七八九十百千0-9]+[）)]\s*\S/.test(source)) return { level: 4, text: source };
  if (/^\d+(?:\.\d+)*[、．.]\s*\S/.test(source)) return { level: 5, text: source };
  if (/^(?:专栏|附表|附件)\s*[一二三四五六七八九十百千0-9]+\s*\S/.test(source)) return { level: 4, text: source };
  return null;
}

/** Main document heading/date are only recognized at the start of the first
 * page. Never infer them inside later pages or inside policy paragraphs. */
function normalizeDocumentTitle(title: string): string {
  return normalizeHeadingText(title).replace(/[“”‘’"'《》<>（）()·—－:：，,、]/g, "");
}
const DATE_STAMP = /^[（(]?\s*(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*日)?\s*[）)]?$/;
const APPROVAL_LINE = /^[（(]?\s*(?:19|20)\d{2}\s*年.*(?:审议通过|批准|通过|印发|公布|发布|会议)[^。；;]*[）)]?$/;
function originalDateLine(line: string): boolean {
  const source = line.trim().replace(/^#{1,6}\s*/, "");
  return source.length >= 5 && source.length <= 110
    && (DATE_STAMP.test(source) || APPROVAL_LINE.test(source));
}

function isDocumentTitle(line: string, title?: string): boolean {
  const source = line.trim().replace(/^#{1,6}\s*/, "").replace(/\s*#+\s*$/, "");
  if (!source || /^第[一二三四五六七八九十百千\d]+[编篇章章节]/.test(source)) return false;
  // Exact metadata title takes precedence even when it is unusually long.
  if (title && normalizeDocumentTitle(source) === normalizeDocumentTitle(title)) return true;
  if (source.length > 130) return false;
  // Source title can differ from metadata (e.g. abbreviated document name).
  return source.length >= 12 && /(?:纲要|规划|报告|通知|方案|意见|办法|条例|决定)$/.test(source)
    && !/[。？！；;]$/.test(source);
}

/**
 * A published section starts at an immutable code-point offset. Prefer this
 * reference over a visual guess based on short source lines.
 */
function sectionHeading(
  line: string, absoluteOffset: number, sections: readonly PolicySection[],
): { level: HeadingLevel; text: string } | null {
  const cleaned = line.trim().replace(/^#{1,6}\s*/, "").replace(/\s*#+\s*$/, "");
  const normalized = normalizeHeadingText(cleaned);
  if (!normalized || !sections.length) return null;
  const matched = sections.find(section => section.level >= 1 && section.level <= 6
    && Math.abs(section.start - absoluteOffset) <= 3
    && normalizeHeadingText(section.path.split(/\s+\/\s+/).at(-1) ?? "") === normalized);
  if (!matched) return null;
  if (/^序\s*言$/.test(cleaned)) return { level: 2, text: "序言" };
  return {
    level: Math.max(2, Math.min(6, matched.level + 1)) as HeadingLevel,
    text: cleaned,
  };
}

function PolicyHeading({ level, children }: { level: HeadingLevel; children: string }) {
  if (level === 2) return <h2 className="catalog-heading">{children}</h2>;
  if (level === 3) return <h3 className="catalog-heading">{children}</h3>;
  if (level === 4) return <h4 className="catalog-heading">{children}</h4>;
  if (level === 5) return <h5 className="catalog-heading">{children}</h5>;
  return <h6 className="catalog-heading">{children}</h6>;
}
/**
 * Printed table-of-contents entries are source text, not document headings.
 * Identify a TOC by repeated *numbered* page references, not by a lone
 * chapter title or an ordinary sentence ending in a year.
 */
const TOC_ENTRY = /^(?:第[一二三四五六七八九十百千0-9]+[编篇部章节]|[（(][一二三四五六七八九十0-9]+[）)]|[一二三四五六七八九十]+[、．.])/;
const TOC_PAGE = /(?:[.．·•…⋯。﹒:：\s]{2,})[0-9０-９]{1,3}\s*$/;
const TOC_ATTACHED_PAGE = /(?:[\u3400-\u9fff）)]|[）)]\s+)[0-9０-９]{1,3}\s*$/;
function tocPageEntry(line: string): boolean {
  const source = line.trim().replace(/^#{1,6}\s*/, "");
  return source.length >= 6 && source.length <= 150 && TOC_ENTRY.test(source)
    && (TOC_PAGE.test(source) || TOC_ATTACHED_PAGE.test(source));
}
function tocRange(lines: readonly string[]): [number, number] | null {
  const firstHeading = lines.findIndex(line => /^目\s*录\s*$/.test(line.trim()));
  const entries = lines.flatMap((line, index) => tocPageEntry(line) ? [index] : []);
  if (entries.length < 2 && (firstHeading < 0 || entries.length < 1)) return null;
  // A standalone page number cannot put an entire policy chapter in TOC mode.
  const first = firstHeading >= 0 && firstHeading <= entries[0] ? firstHeading : entries[0];
  const last = entries.at(-1) ?? -1;
  if (last < first || last - first > 220) return null;
  return [first, last];
}
function cleanTocLine(line: string): string {
  const source = line.trim().replace(/^#{1,6}\s*/, "");
  if (!tocPageEntry(source)) return source;
  return source.replace(/(?:[.．·•…⋯。﹒:：\s]*[0-9０-９]{1,3})\s*$/, "").replace(/[.．·•…⋯。﹒\s]+$/, "");
}

const PDF_PAGE_MARK = /^\s*[—－-]\s*\d{1,4}\s*[—－-]\s*$/;
function collapsedNumericTable(line: string): boolean {
  // The original extractor sometimes flattens whole indicator tables into
  // prose. Display such rows separately, never guess their numerical columns.
  if (line.length < 220 || line.includes("|")) return false;
  const numbers = (line.match(/(?:^|[\s（(])\d+(?:\.\d+)?(?=[\s）),，—－]|$)/g) ?? []).length;
  const markers = (line.match(/(?:预期性|约束性|累计|地区生产总值|（%）|\(%\))/g) ?? []).length;
  return numbers >= 12 && markers >= 4;
}

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

/**
 * Legacy policy archives sometimes flatten a Markdown table into one line:
 * "| 专栏... |---| | 指标 | 2015 | 2020 || 一、经济发展 || 1. GDP |..."
 * Recover a real table only if its header and EVERY data row are consistent.
 */
function flattenedTable(line: string): { caption: string; headers: string[]; rows: string[][] } | null {
  const separator = /\|\s*:?-{3,}:?\s*\|/.exec(line);
  if (!separator || !line.includes("||")) return null;
  const headerStart = separator.index + separator[0].length;
  const rawRows = line.slice(headerStart).split(/\s*\|\|\s*/);
  if (rawRows.length < 2) return null;
  const headers = tableCells(rawRows[0]);
  if (headers.length < 3 || headers.length > 24 || headers.some(x => !x)) return null;
  const rows: string[][] = [];
  for (const raw of rawRows.slice(1)) {
    const cells = tableCells(raw);
    if (cells.length === headers.length) rows.push(cells);
    else if (cells.length === 1 && /^[一二三四五六七八九十]+[、．.]/.test(cells[0])) rows.push([cells[0]]);
    else return null;
  }
  if (!rows.length) return null;
  return { caption: line.slice(0, separator.index).replace(/^\s*\|/, "").trim(), headers, rows };
}

/** Display Markdown emphasis as React text nodes: HTML from source is never executed. */
function inlineContent(source: string): React.ReactNode {
  const marker = /(\*\*([^*\n]+)\*\*|__([^_\n]+)__|(?<!\*)\*([^*\n]+)\*(?!\*)|`([^`\n]+)`)/g;
  const nodes: React.ReactNode[] = [];
  let start = 0;
  for (const match of source.matchAll(marker)) {
    const position = match.index ?? 0;
    if (position > start) nodes.push(source.slice(start, position));
    if (match[2] || match[3]) nodes.push(<strong key={position}>{match[2] || match[3]}</strong>);
    else if (match[4]) nodes.push(<em key={position}>{match[4]}</em>);
    else nodes.push(<code key={position}>{match[5]}</code>);
    start = position + match[0].length;
  }
  if (start < source.length) nodes.push(source.slice(start));
  return nodes.length ? nodes : source;
}

function tableLike(line: string): boolean {
  return line.trim().startsWith("|") && (line.match(/(?<!\\)\|/g)?.length ?? 0) >= 3;
}

/** An unlabelled but internally consistent pipe block still has real columns.
 * Do not invent column headers if the Markdown separator is absent. */
function plainTableRows(lines: string[]): string[][] | null {
  const rows = lines.map(tableCells);
  const count = rows[0]?.length ?? 0;
  return rows.length >= 2 && count >= 2 && count <= 24
    && rows.every(row => row.length === count) ? rows : null;
}

function TableBlock({ headers, rows, caption, headerless = false }: {
  headers: string[]; rows: string[][]; caption?: string; headerless?: boolean;
}) {
  return <div className="catalog-table-scroll" role="region" aria-label="政策原文表格" tabIndex={0}>
    <table className="catalog-markdown-table">
      {caption && <caption>{caption}</caption>}
      {!headerless && <thead><tr>{headers.map((cell, i) => <th key={i} scope="col">{inlineContent(cell)}</th>)}</tr></thead>}
      <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>
        {row.length === 1
          ? <th scope="row" colSpan={headers.length}>{inlineContent(row[0])}</th>
          : row.map((cell, cellIndex) => <td key={cellIndex}>{inlineContent(cell)}</td>)}
      </tr>)}</tbody>
    </table>
  </div>;
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
  if (failed) return <p className="catalog-media-missing" role="note">
    原文引用的{alt || name}未收录对应图片文件
  </p>;
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
export function PolicyRichText({ text, planid, softWrap = false, originalHints = [], documentTitle, firstPage = false,
  sections = [], textOffset = 0 }: {
  text: string; planid: string; softWrap?: boolean; originalHints?: readonly OriginalHeadingHint[];
  documentTitle?: string | null; firstPage?: boolean;
  sections?: readonly PolicySection[]; textOffset?: number;
}) {
  // Split lines without discarding CRLF/CR boundaries: published offsets are
  // Unicode code points of the *original* source, not normalized text.
  const fragments = text.split(/(\r\n|\n|\r)/);
  const lines: string[] = [];
  const linePositions: number[] = [];
  let cursor = textOffset;
  for (let index = 0; index < fragments.length; index += 2) {
    const line = fragments[index];
    lines.push(line);
    linePositions.push(cursor);
    cursor += Array.from(line).length + Array.from(fragments[index + 1] ?? "").length;
  }
  const toc = tocRange(lines);
  const blocks: React.ReactNode[] = [];
  let prose: string[] = [];
  let key = 0;
  const flushProse = () => {
    if (!prose.length) return;
    for (const paragraph of paragraphLines(prose, softWrap)) {
      blocks.push(<p className="catalog-prose" key={key++}>{inlineContent(paragraph)}</p>);
    }
    prose = [];
  };
  let index = 0;
  let leadTitleFound = false;
  let documentLead = firstPage;
  let leadLineCount = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { flushProse(); index++; continue; }
    if (PDF_PAGE_MARK.test(line.trim())) {
      flushProse();
      index++;
      continue;
    }

    // In a printed TOC, every entry uses ordinary body typography; chapter
    // heading hierarchy applies only when the *actual body* begins.
    if (toc && index >= toc[0] && index <= toc[1]) {
      flushProse();
      blocks.push(<p className="catalog-prose catalog-toc-line" key={key++}>{inlineContent(cleanTocLine(line))}</p>);
      index++;
      continue;
    }

    // The metadata title is already displayed as a centered H1 in reader mode.
    // Repeated source titles are left in the frozen text, but not drawn twice.
    if (documentLead && leadLineCount < 12) {
      const plain = line.trim().replace(/^#{1,6}\s*/, "").replace(/\s*#+\s*$/, "");
      // A formal title can be hard-wrapped across two or three lines.
      // Join only an exact match to metadata; never guess a missing title.
      if (!leadTitleFound && documentTitle && leadLineCount <= 2) {
        let joined = plain;
        let end = index;
        const expected = normalizeDocumentTitle(documentTitle);
        for (let next = index + 1; next < Math.min(lines.length, index + 5); next++) {
          const nextLine = lines[next].trim();
          if (!nextLine) continue;
          joined += nextLine.replace(/^#{1,6}\s*/, "");
          end = next;
          if (normalizeDocumentTitle(joined) === expected) {
            flushProse();
            leadTitleFound = true;
            leadLineCount += end - index + 1;
            index = end + 1;
            break;
          }
          if (normalizeDocumentTitle(joined).length >= expected.length) break;
        }
        if (leadTitleFound) continue;
      }
      if (!leadTitleFound && isDocumentTitle(plain, documentTitle ?? undefined)) {
        flushProse();
        leadTitleFound = true;
        leadLineCount++;
        // The metadata title is already H1 above the toolbar. Only source
        // titles that differ materially need an additional original H1.
        if (normalizeDocumentTitle(plain) !== normalizeDocumentTitle(documentTitle ?? "")) {
          blocks.push(<h1 className="catalog-source-title" key={key++}>{plain}</h1>);
        }
        index++;
        continue;
      }
      if ((leadTitleFound || leadLineCount <= 2) && originalDateLine(plain)) {
        flushProse();
        blocks.push(<p className="catalog-document-date" key={key++}>{plain}</p>);
        leadLineCount++;
        index++;
        continue;
      }
      leadLineCount++;
      // Heading or long paragraphs indicate ordinary source content.
      if (policyHeading(line, originalHints) || line.trim().length > 130 || leadLineCount >= 12) documentLead = false;
    }

    const image = IMAGE_LINK.exec(line);
    if (image) {
      flushProse();
      blocks.push(<PolicyImage key={`${planid}:${image[2]}:${key++}`} planid={planid} alt={image[1]} name={image[2]} />);
      index++;
      continue;
    }

    if (collapsedNumericTable(line)) {
      flushProse();
      blocks.push(<div key={key++} className="catalog-table-scroll catalog-table-unparsed"
        role="region" aria-label="原文指标表格（结构不完整）" tabIndex={0}>
        <p className="catalog-table-warning">原文中的表格列结构未完整保留，以下为原始文字，未重排指标数值。</p>
        <pre>{line}</pre>
      </div>);
      index++;
      continue;
    }
    if (line.includes("||") && /\|\s*:?-{3,}:?\s*\|/.test(line)) {
      const recovered = flattenedTable(line);
      flushProse();
      if (recovered) {
        blocks.push(<TableBlock key={key++} caption={recovered.caption} headers={recovered.headers} rows={recovered.rows} />);
      } else {
        // Keep unverified legacy tables separate and horizontally scrollable
        // instead of passing pipes into running Chinese prose.
        blocks.push(<div key={key++} className="catalog-table-scroll catalog-table-unparsed"
          role="region" aria-label="原文表格（无法确认列结构）" tabIndex={0}>
          <pre>{line.replace(/\s*\|\|\s*/g, "\n")}</pre>
        </div>);
      }
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
      blocks.push(<TableBlock key={key++} headers={headers} rows={rows} />);
      continue;
    }

    if (tableLike(line)) {
      flushProse();
      const rows: string[] = [];
      while (index < lines.length && tableLike(lines[index])) {
        rows.push(lines[index]);
        index++;
      }
      const parsed = plainTableRows(rows);
      if (parsed) {
        blocks.push(<TableBlock key={key++} headers={[]} rows={parsed} headerless />);
      } else {
        blocks.push(<div key={key++} className="catalog-table-scroll catalog-table-unparsed"
          role="region" aria-label="原文表格（列结构待核验）" tabIndex={0}>
          <pre>{rows.join("\n")}</pre>
        </div>);
      }
      continue;
    }

    const heading = sectionHeading(line, linePositions[index], sections)
      ?? policyHeading(line, originalHints);
    if (heading) {
      flushProse();
      blocks.push(<PolicyHeading level={heading.level} key={key++}>{heading.text}</PolicyHeading>);
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
