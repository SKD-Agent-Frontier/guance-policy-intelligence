import type { PolicySection } from "../types";

export type OutlineEntry = PolicySection & { label: string; depth: number };

/** RAG section paths are evidence paths: the navigation UI must not repeat
 * the full path at every level. Preserve original start/end offsets. */
export function policyOutline(sections: readonly PolicySection[]): OutlineEntry[] {
  const valid = sections
    .filter(section => Number.isInteger(section.level) && section.level >= 1 && section.level <= 7
      && Number.isInteger(section.start) && section.start >= 0
      && Number.isInteger(section.end) && section.end > section.start)
    .map(section => ({
      ...section,
      label: section.path.split(/\s+\/\s+/).at(-1)?.replace(/^\s*#{1,6}\s*/, "").trim() ?? "",
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
  const shallowest = Math.min(...unique.map(section => section.level), 1);
  return unique.map(section => ({
    ...section,
    depth: Math.max(0, Math.min(4, section.level - shallowest)),
  }));
}
