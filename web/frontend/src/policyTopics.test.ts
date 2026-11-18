import { describe, expect, it } from "vitest";
import type { PolicyListItem } from "./types";
import { tagsForPolicy, themeCounts, themedPolicies } from "./policyTopics";

const policy = (planid: string, title: string): PolicyListItem => ({
  planid, title, area: "示例地区", fyp: null, plantype_label: "专项规划", matches: [],
});

describe("published catalog topic classification", () => {
  it("derives overlapping categories from policy titles instead of a bundled sample map", () => {
    const item = policy("P-1", "数字经济科技创新总体规划");
    expect(tagsForPolicy(item.title ?? "")).toEqual(expect.arrayContaining(["comprehensive", "digital", "industry"]));
    expect(themedPolicies("digital", [item])).toEqual([expect.objectContaining({ planid: "P-1", title: item.title })]);
  });

  it("counts the supplied complete catalog and omits categories without matching titles", () => {
    const items = [policy("P-1", "数字经济发展规划"), policy("P-2", "养老服务体系规划")];
    const counts = themeCounts(items);
    expect(counts.find((theme) => theme.id === "digital")?.count).toBe(1);
    expect(counts.find((theme) => theme.id === "welfare")?.count).toBe(1);
    expect(counts.find((theme) => theme.id === "health")).toBeUndefined();
  });
});
