import { afterEach, describe, expect, it, vi } from "vitest";
import { addPolicyToTopic, loadTopicCollections, newTopicCollection, removePolicyFromTopic, saveTopicCollections, TOPIC_STORAGE_KEY } from "./topicStore";
import { builtInThemes, classifiedDocuments, policyTopics, tagsForPolicy, themedPolicies } from "./policyTopics";

afterEach(() => { localStorage.removeItem(TOPIC_STORAGE_KEY); vi.restoreAllMocks(); });

describe("builtin policy themes", () => {
  it("classifies precisely 506 unique sample planids with no categories below three documents", () => {
    expect(Object.keys(classifiedDocuments)).toHaveLength(506);
    expect(policyTopics.total_documents).toBe(506);
    expect(builtInThemes.length).toBeGreaterThan(0);
    expect(builtInThemes.every((theme) => theme.count >= 3)).toBe(true);
    for (const theme of builtInThemes) {
      expect(themedPolicies(theme.id)).toHaveLength(theme.count);
    }
    const tags = Object.values(classifiedDocuments);
    expect(tags.filter((item) => item.tags.length === 0)).toHaveLength(policyTopics.unclassified_count);
    expect(tagsForPolicy("nonexistent")).toEqual([]);
  });
  it("can use multiple themes without fabricating tags for unclassified policies", () => {
    for (const [planid, policy] of Object.entries(classifiedDocuments)) {
      expect(policy.tags.every((tag) => builtInThemes.some((t) => t.id === tag))).toBe(true);
      expect(new Set(policy.tags).size).toBe(policy.tags.length);
      expect(tagsForPolicy(planid)).toEqual(policy.tags);
    }
  });
});

describe("personal topic storage", () => {
  const policy = {
    planid: "23132", title: "海西州十四五数字经济发展规划", area: "海西蒙古族藏族自治州",
    fyp: "十四五", plantype_label: "专项规划", generation_id: "gen-1",
  };

  it("creates, persists and deduplicates policies by planid", () => {
    const topic = newTopicCollection(" 数字经济研究 ");
    expect(topic.name).toBe("数字经济研究");
    const added = addPolicyToTopic([topic], topic.id, policy);
    expect(added[0].policies).toEqual([policy]);
    expect(addPolicyToTopic(added, topic.id, policy)).toEqual(added);
    expect(saveTopicCollections(added)).toBe(true);
    expect(loadTopicCollections()).toEqual(added);
    const removed = removePolicyFromTopic(loadTopicCollections(), topic.id, "23132");
    expect(removed[0].policies).toHaveLength(0);
  });

  it("does not throw for inaccessible storage or malformed records", () => {
    localStorage.setItem(TOPIC_STORAGE_KEY, "{broken-json");
    expect(loadTopicCollections()).toEqual([]);
    expect(() => newTopicCollection("  ")).toThrow();
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(saveTopicCollections([newTopicCollection("可用专题")])).toBe(false);
    spy.mockRestore();
  });
});
