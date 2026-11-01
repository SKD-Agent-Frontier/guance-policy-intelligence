import dataset from "./data/policyTopics.json";

export interface BuiltInTheme {
  id: string;
  name: string;
  description: string;
  count: number;
}
export interface ClassifiedPolicy {
  title: string;
  area: string | null;
  fyp: string | null;
  tags: string[];
}
export const policyTopics = dataset;
export const builtInThemes: BuiltInTheme[] = dataset.categories.filter((item) => item.count >= 3);
export const classifiedDocuments = dataset.documents as Record<string, ClassifiedPolicy>;
export function themedPolicies(id: string): Array<ClassifiedPolicy & { planid: string }> {
  return Object.entries(classifiedDocuments)
    .filter(([, document]) => document.tags.includes(id))
    .map(([planid, document]) => ({ ...document, planid }));
}
export function tagsForPolicy(planid: string): string[] {
  return classifiedDocuments[planid]?.tags ?? [];
}
