/** Personal research collections are separate from the public, title-based thematic taxonomy.
 * MVP storage is browser-local; records are keyed by planid and are never written to the RAG corpus.
 */
export interface TopicPolicy {
  planid: string;
  title: string;
  area: string | null;
  fyp: string | null;
  plantype_label: string | null;
  generation_id: string | null;
}
export interface TopicCollection {
  id: string;
  name: string;
  created_at: string;
  policies: TopicPolicy[];
}
export const TOPIC_STORAGE_KEY = "guance:research-topics:v1";

export function loadTopicCollections(): TopicCollection[] {
  try {
    const raw = localStorage.getItem(TOPIC_STORAGE_KEY);
    if (!raw) return [];
    const data: unknown = JSON.parse(raw);
    if (!Array.isArray(data)) return [];
    return data.filter((item): item is TopicCollection =>
      !!item && typeof item === "object" &&
      typeof item.id === "string" && typeof item.name === "string" &&
      Array.isArray(item.policies)
    ).map((item) => ({
      ...item,
      policies: item.policies.filter((policy): policy is TopicPolicy =>
        !!policy && typeof policy.planid === "string" &&
        typeof policy.title === "string"
      ),
    }));
  } catch {
    return [];
  }
}

export function saveTopicCollections(topics: TopicCollection[]): boolean {
  try {
    localStorage.setItem(TOPIC_STORAGE_KEY, JSON.stringify(topics));
    return true;
  } catch {
    return false;
  }
}

export function newTopicCollection(name: string): TopicCollection {
  const clean = name.trim().replace(/\s+/g, " ");
  if (!clean || clean.length > 60) throw new Error("专题名称应为 1–60 个字符");
  return {
    id: typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID() : `topic-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    name: clean, created_at: new Date().toISOString(), policies: [],
  };
}

export function addPolicyToTopic(items: TopicCollection[], id: string, policy: TopicPolicy): TopicCollection[] {
  if (!policy.planid.trim() || !policy.title.trim()) return items;
  return items.map((topic) =>
    topic.id === id && !topic.policies.some((item) => item.planid === policy.planid)
      ? { ...topic, policies: [...topic.policies, policy] }
      : topic
  );
}

export function removePolicyFromTopic(items: TopicCollection[], id: string, planid: string): TopicCollection[] {
  return items.map((topic) => topic.id === id
    ? { ...topic, policies: topic.policies.filter((policy) => policy.planid !== planid) }
    : topic);
}
