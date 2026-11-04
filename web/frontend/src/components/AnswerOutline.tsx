import { Fragment, type ReactNode } from "react";

/** Numbering is semantic: headings have distinct type and visual treatments.
 * Plain answers remain paragraphs, while multi-part reports can use:
 * 一、 -> （一） -> 1、 -> （1）
 *
 * Backend answers also carry Markdown lists, tables, quotes and inline
 * emphasis. Each keeps its own semantics instead of collapsing into
 * preformatted text, because a bare "- " or "|" line is unreadable prose.
 */
type Level = 1 | 2 | 3 | 4;
/** Semantic row kinds keep the outline readable without losing heading levels. */

type Row =
  | { kind: "heading"; level: Level; prefix?: string; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "table"; headers: string[] | null; rows: string[][] }
  | { kind: "quote"; text: string };

const headingPatterns: { level: Level; pattern: RegExp }[] = [
  { level: 1, pattern: /^([一二三四五六七八九十百]+、)\s*(.+)$/ },
  { level: 2, pattern: /^(（[一二三四五六七八九十百]+）)\s*(.+)$/ },
  { level: 3, pattern: /^([1-9]\d*、)\s*(.+)$/ },
  { level: 4, pattern: /^(（[1-9]\d*）)\s*(.+)$/ },
];

const MARKDOWN_HEADING = /^(#{1,4})\s+(.+?)\s*#*$/;
const BULLET = /^\s{0,3}[-*+•]\s+(.+)$/;
const ORDERED = /^\s{0,3}(\d{1,3})[.)]\s+(.+)$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const INLINE_MARKER = /(\*\*([^*\n]+)\*\*|__([^_\n]+)__|(?<!\*)\*([^*\n]+)\*(?!\*)|`([^`\n]+)`)/g;

type HeadingRow = Extract<Row, { kind: "heading" }>;
type ListRow = Extract<Row, { kind: "list" }>;
type TableRow = Extract<Row, { kind: "table" }>;

function headingRow(line: string): HeadingRow | null {
  // Markdown headings from report drafts keep their heading semantics. When a
  // heading carries a canonical Chinese outline marker, the marker still wins
  // so 一、/（一）/1、/（1）nest exactly as before.
  const markdown = MARKDOWN_HEADING.exec(line);
  const headingText = markdown ? markdown[2] : line;
  const match = headingPatterns
    .map(({ level, pattern }) => ({ level, match: pattern.exec(headingText) }))
    .find(item => item.match);
  if (match?.match) {
    return { kind: "heading", level: match.level, prefix: match.match[1], text: match.match[2] };
  }
  if (markdown) return { kind: "heading", level: Math.min(markdown[1].length, 4) as Level, text: headingText };
  return null;
}

function tableCells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "")
    .split(/(?<!\\)\|/).map(cell => cell.replace(/\\\|/g, "|").trim());
}

function isTableSeparator(line: string): boolean {
  const cells = tableCells(line);
  return cells.length >= 2 && cells.every(cell => /^:?-{2,}:?$/.test(cell));
}

/** A pipe block only becomes a table when every row shares one column count.
 * Requiring a leading pipe keeps ordinary prose that merely contains "|" out. */
function tableRow(lines: string[], start: number): { row: TableRow; next: number } | null {
  const block: string[] = [];
  let index = start;
  while (index < lines.length && lines[index].trim().startsWith("|")) {
    block.push(lines[index]);
    index++;
  }
  if (block.length < 2) return null;
  const headers = isTableSeparator(block[1]) ? tableCells(block[0]) : null;
  const body = headers ? block.slice(2) : block;
  if (!body.length) return null;
  const width = (headers ?? tableCells(body[0])).length;
  if (width < 2) return null;
  const rows = body.map(tableCells);
  if (!rows.every(row => row.length === width)) return null;
  return { row: { kind: "table", headers, rows }, next: index };
}

function listRow(lines: string[], start: number): { row: ListRow; next: number } | null {
  const bullet = BULLET.exec(lines[start]);
  const ordered = bullet ? null : ORDERED.exec(lines[start]);
  if (!bullet && !ordered) return null;
  const items: string[] = [];
  let index = start;
  while (index < lines.length) {
    const match = bullet ? BULLET.exec(lines[index]) : ORDERED.exec(lines[index]);
    if (!match) break;
    items.push(match[1]);
    index++;
  }
  return { row: { kind: "list", ordered: Boolean(ordered), items }, next: index };
}

export function parseAnswerOutline(text: string): Row[] {
  const rows: Row[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) rows.push({ kind: "paragraph", text: paragraph.join("\n") });
    paragraph = [];
  };

  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const original = lines[index];
    const trimmed = original.trim();
    if (!trimmed) {
      flush();
      index++;
      continue;
    }
    const heading = headingRow(trimmed);
    if (heading) {
      flush();
      rows.push(heading);
      index++;
      continue;
    }
    if (trimmed.startsWith("|")) {
      const table = tableRow(lines, index);
      if (table) {
        flush();
        rows.push(table.row);
        index = table.next;
        continue;
      }
    }
    const quote = QUOTE.exec(original);
    if (quote) {
      flush();
      const quoted: string[] = [];
      while (index < lines.length) {
        const next = QUOTE.exec(lines[index]);
        if (!next) break;
        quoted.push(next[1]);
        index++;
      }
      rows.push({ kind: "quote", text: quoted.join("\n") });
      continue;
    }
    const list = listRow(lines, index);
    if (list) {
      flush();
      rows.push(list.row);
      index = list.next;
      continue;
    }
    paragraph.push(original);
    index++;
  }
  flush();
  return rows;
}

/** Render Markdown emphasis as React nodes; HTML from the answer is never executed. */
function inlineMarkdown(text: string, renderText?: (value: string) => ReactNode): ReactNode {
  const nodes: ReactNode[] = [];
  let start = 0;
  for (const match of text.matchAll(INLINE_MARKER)) {
    const position = match.index ?? 0;
    if (position > start) nodes.push(<Fragment key={`text-${position}`}>{renderText ? renderText(text.slice(start, position)) : text.slice(start, position)}</Fragment>);
    const inner = match[2] || match[3] || match[4] || match[5] || "";
    const content = <Fragment key={`inner-${position}`}>{renderText ? renderText(inner) : inner}</Fragment>;
    if (match[2] || match[3]) nodes.push(<strong key={`strong-${position}`}>{content}</strong>);
    else if (match[4]) nodes.push(<em key={`em-${position}`}>{content}</em>);
    else nodes.push(<code key={`code-${position}`}>{content}</code>);
    start = position + match[0].length;
  }
  if (start < text.length) nodes.push(<Fragment key="tail">{renderText ? renderText(text.slice(start)) : text.slice(start)}</Fragment>);
  return nodes.length ? nodes : <Fragment key="plain">{renderText ? renderText(text) : text}</Fragment>;
}

function renderRow(row: Row, renderText?: (value: string) => ReactNode): ReactNode {
  if (row.kind === "heading") {
    const body = <>{row.prefix && <span className="qa-outline-marker">{row.prefix}</span>}<span>{inlineMarkdown(row.text)}</span></>;
    const cls = `qa-outline-heading qa-outline-level-${row.level}`;
    if (row.level === 1) return <h2 className={cls}>{body}</h2>;
    if (row.level === 2) return <h3 className={cls}>{body}</h3>;
    if (row.level === 3) return <h4 className={cls}>{body}</h4>;
    return <h5 className={cls}>{body}</h5>;
  }
  if (row.kind === "list") {
    const items = row.items.map((item, position) => <li key={position}>{inlineMarkdown(item, renderText)}</li>);
    return row.ordered
      ? <ol className="qa-answer-list qa-answer-list--ordered">{items}</ol>
      : <ul className="qa-answer-list">{items}</ul>;
  }
  if (row.kind === "table") {
    return <div className="qa-answer-table-scroll" role="region" aria-label="回答中的表格" tabIndex={0}>
      <table className="qa-answer-table">
        {row.headers && <thead><tr>{row.headers.map((cell, position) => <th key={position} scope="col">{inlineMarkdown(cell, renderText)}</th>)}</tr></thead>}
        <tbody>{row.rows.map((cells, rowIndex) => <tr key={rowIndex}>
          {cells.map((cell, cellIndex) => <td key={cellIndex}>{inlineMarkdown(cell, renderText)}</td>)}
        </tr>)}</tbody>
      </table>
    </div>;
  }
  if (row.kind === "quote") return <blockquote className="qa-answer-quote">{inlineMarkdown(row.text, renderText)}</blockquote>;
  return <p className="qa-outline-paragraph">{inlineMarkdown(row.text, renderText)}</p>;
}

export function AnswerOutline({ text, renderText }: { text: string; renderText?: (value: string) => ReactNode }) {
  const rows = parseAnswerOutline(text);
  return <div className="qa-answer-outline">
    {rows.map((row, index) => <div key={index} className={`qa-outline-row qa-outline-row-${row.kind === "heading" ? row.level : 0}`}>{renderRow(row, renderText)}</div>)}
  </div>;
}
