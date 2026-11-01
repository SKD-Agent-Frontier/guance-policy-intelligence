import type { PolicySection } from "../types";

export type OutlineEntry = PolicySection & { label: string; depth: number };

/** RAG section paths are evidence paths: the navigation UI must not repeat
 * the full path at every level. Preserve original start/end offsets. */
export function policyOutline(sections: readonly PolicySection[]): OutlineEntry[] {
  const valid = sections
    // Unnumbered printed index entries are not structural section headings.
    // Genuine chapters with page leaders remain available for safe cleanup.
    .filter(section => {
      const source = (section.path.split(/\s+\/\s+/).at(-1) ?? "")
        .replace(/^\s*#{1,6}\s*/, "").trim();
      const printedPage = /[.．·…⋯﹒]{3,}\s*[0-9０-９]{1,4}\s*$/.test(source);
      const numberedHeading = /^第[一二三四五六七八九十百千0-9]+[编篇部章节]/.test(source);
      return !printedPage || numberedHeading;
    })
    .filter(section => Number.isInteger(section.level) && section.level >= 1 && section.level <= 7
      && Number.isInteger(section.start) && section.start >= 0
      && Number.isInteger(section.end) && section.end > section.start)
    .map(section => ({
      ...section,
      label: (section.path.split(/\s+\/\s+/).at(-1) ?? "")
        .replace(/^\s*#{1,6}\s*/, "")
        // Some RAG headings contain OCR/formatting artifacts such as "(%2)".
        // Delete only the invalid prefix, never fabricate a chapter number.
        .replace(/^\s*[（(]\s*[%％]\s*2\s*[）)]\s*/, "")
        .replace(/^\s*[%％]\s*2[）)]?\s*/, "")
        .replace(/[.．·…⋯﹒]{3,}\s*[0-9０-９]{1,4}\s*$/, "")
        .trim(),
    }))
    .filter(section => section.label.length >= 2 && section.label.length <= 100
      && !/[|\n\r]/.test(section.label) && !section.label.includes("![")
      && !/[.．…·]{3,}\s*\d+\s*$/.test(section.label));
  const sectionsOnly = valid.filter(section => section.level <= 6);
  // Article-based legislation can lack chapters altogether: keep articles in
  // that one case, but don't crowd the chapter index with every numbered clause.
  const candidates = sectionsOnly.length ? sectionsOnly : valid.filter(section => section.level === 7);
  const sorted = [...candidates].sort((a, b) => a.start - b.start
    || a.level - b.level || a.index - b.index);
  const seen = new Set<string>();
  const unique = sorted.filter(section => {
    const key = section.start + ":" + section.label;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!unique.length) return [];
  const shallowest = Math.min(...unique.map(section => section.level));
  return unique.map(section => ({
    ...section,
    depth: Math.max(0, Math.min(4, section.level - shallowest)),
  }));
}
