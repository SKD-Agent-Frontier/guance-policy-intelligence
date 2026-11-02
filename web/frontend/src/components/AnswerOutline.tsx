import type { ReactNode } from "react";

/** Numbering is semantic: headings have distinct type and visual treatments.
 * Plain answers remain paragraphs, while multi-part reports can use:
 * 一、 -> （一） -> 1、 -> （1）
 */
type Level = 1 | 2 | 3 | 4;
type Row = { text: string; level: Level | 0; prefix?: string };

const headingPatterns: { level: Level; pattern: RegExp }[] = [
  { level: 1, pattern: /^([一二三四五六七八九十百]+、)\s*(.+)$/ },
  { level: 2, pattern: /^(（[一二三四五六七八九十百]+）)\s*(.+)$/ },
  { level: 3, pattern: /^([1-9]\d*、)\s*(.+)$/ },
  { level: 4, pattern: /^(（[1-9]\d*）)\s*(.+)$/ },
];

export function parseAnswerOutline(text: string): Row[] {
  const rows: Row[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) rows.push({ level: 0, text: paragraph.join("\n") });
    paragraph = [];
  };

  for (const original of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = original.trim();
    if (!line) {
      flush();
      continue;
    }
    // Markdown headings from report drafts keep their heading semantics. When a
    // heading carries a canonical Chinese outline marker, the marker still wins
    // so 一、/（一）/1、/（1）nest exactly as before.
    const markdown = /^(#{1,4})\s+(.+?)\s*#*$/.exec(line);
    const headingText = markdown ? markdown[2] : line;
    const match = headingPatterns
      .map(({ level, pattern }) => ({ level, match: pattern.exec(headingText) }))
      .find(item => item.match);
    if (match?.match) {
      flush();
      rows.push({ level: match.level, prefix: match.match[1], text: match.match[2] });
    } else if (markdown) {
      flush();
      rows.push({ level: Math.min(markdown[1].length, 4) as Level, text: headingText });
    } else {
      paragraph.push(line);
    }
  }
  flush();
  return rows;
}

export function AnswerOutline({ text }: { text: string }) {
  const rows = parseAnswerOutline(text);
  return <div className="qa-answer-outline">
    {rows.map((row, index) => {
      let child: ReactNode;
      if (row.level) {
        const body = <>{row.prefix && <span className="qa-outline-marker">{row.prefix}</span>}<span>{row.text}</span></>;
        const cls = `qa-outline-heading qa-outline-level-${row.level}`;
        if (row.level === 1) child = <h2 className={cls}>{body}</h2>;
        else if (row.level === 2) child = <h3 className={cls}>{body}</h3>;
        else if (row.level === 3) child = <h4 className={cls}>{body}</h4>;
        else child = <h5 className={cls}>{body}</h5>;
      } else child = <p className="qa-outline-paragraph">{row.text}</p>;
      return <div key={index} className={`qa-outline-row qa-outline-row-${row.level}`}>{child}</div>;
    })}
  </div>;
}
