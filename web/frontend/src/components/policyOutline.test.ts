import { describe, expect, it } from "vitest";
import { policyOutline } from "./policyOutline";

describe("policy chapter outline", () => {
  it("shows only the current heading while retaining full evidence paths and offsets", () => {
    const data = [
      { index: 0, level: 1, path: "第一篇 总体部署", start: 0, end: 1200 },
      { index: 1, level: 2, path: "第一篇 总体部署 / 第一章 发展目标", start: 100, end: 700 },
      { index: 2, level: 3, path: "第一篇 总体部署 / 第一章 发展目标 / 第一节 主要指标", start: 200, end: 500 },
      { index: 3, level: 7, path: "第一篇 总体部署 / 第一章 发展目标 / 第一条 做好落实", start: 220, end: 270 },
    ];
    expect(policyOutline(data)).toEqual([
      { ...data[0], label: "第一篇 总体部署", depth: 0 },
      { ...data[1], label: "第一章 发展目标", depth: 1 },
      { ...data[2], label: "第一节 主要指标", depth: 2 },
    ]);
  });

  it("retains numbered articles only for documents without any chapter headings", () => {
    const article = { index: 12, level: 7, path: "第一条 工作要求", start: 16, end: 100 };
    expect(policyOutline([article])).toEqual([{ ...article, label: "第一条 工作要求", depth: 0 }]);
  });

  it("strips corrupted OCR prefix (%2) without fabricating unverified chapter numbers", () => {
    const original = [
      { index: 0, level: 2, path: "（%2） 发展基础", start: 100, end: 200 },
      { index: 1, level: 2, path: "(%2) 存在问题", start: 250, end: 350 },
      { index: 2, level: 3, path: "(%2) 发展基础 / (%2) 加快数据资源共享", start: 420, end: 500 },
      { index: 3, level: 1, path: "第一章 总体要求......13", start: 600, end: 720 },
    ];
    expect(policyOutline(original).map(item => ({ label: item.label, depth: item.depth }))).toEqual([
      { label: "发展基础", depth: 1 },
      { label: "存在问题", depth: 1 },
      { label: "加快数据资源共享", depth: 2 },
      { label: "第一章 总体要求", depth: 0 },
    ]);
    expect(policyOutline(original)[0].start).toBe(100);
  });

  it("filters table fragments, repeated source positions and invalid headings", () => {
    const raw = [
      { index: 0, level: 1, path: "# 第一章 总则", start: 0, end: 200 },
      { index: 1, level: 1, path: "第一章 总则", start: 0, end: 200 },
      { index: 2, level: 2, path: "| 项目 | 数值 |", start: 80, end: 100 },
      { index: 3, level: 3, path: "专项资金........ 13", start: 85, end: 130 },
    ];
    expect(policyOutline(raw).map(item => item.label)).toEqual(["第一章 总则"]);
  });
});
