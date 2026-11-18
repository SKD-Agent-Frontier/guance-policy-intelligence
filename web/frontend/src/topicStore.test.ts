import { afterEach, describe, expect, it, vi } from "vitest";
import { addPolicyToTopic, loadTopicCollections, newTopicCollection, removePolicyFromTopic, saveTopicCollections, TOPIC_STORAGE_KEY } from "./topicStore";

afterEach(() => { localStorage.removeItem(TOPIC_STORAGE_KEY); vi.restoreAllMocks(); });

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
