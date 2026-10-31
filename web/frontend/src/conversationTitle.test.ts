import { describe, expect, it } from "vitest";

import { summarizeConversationTitle } from "./conversationTitle";

describe("policy research conversation titles", () => {
  it.each([
    [
      "密云区在‘十一五’规划期间所处的发展环境总体如何？其中的主要机遇和挑战有哪些？",
      "qa",
      "密云区“十一五”发展环境分析",
    ],
    [
      "与河北省其他地市相比，衡水市在‘十三五’规划中提出的产业发展目标有哪些不同？",
      "compare",
      "衡水市“十三五”产业规划比较",
    ],
    [
      "我想了解沧州市在十三五期间经济社会发展取得了哪些重大成就",
      "qa",
      "沧州市“十三五”经济社会发展成就",
    ],
    [
      "比较浙江和江苏十四五数字经济发展目标的异同",
      "compare",
      "浙江与江苏“十四五”数字经济目标比较",
    ],
    [
      "比较浙江和江苏十四五的数字经济政策",
      "compare",
      "浙江与江苏“十四五”数字经济政策比较",
    ],
    [
      "梳理浙江省“十四五”数字经济相关政策",
      "organize",
      "浙江“十四五”数字经济政策梳理",
    ],
    [
      "当前发布库一共有多少篇政策文献？",
      "qa",
      "政策文献数量统计",
    ],
  ] as const)("summarizes %s", (question, mode, title) => {
    expect(summarizeConversationTitle(question, mode)).toBe(title);
  });

  it("keeps concise user labels and never returns a raw 32-character truncation", () => {
    expect(summarizeConversationTitle("第一项数字经济研究", "qa")).toBe("第一项数字经济研究");
    expect(summarizeConversationTitle(" ", "qa")).toBe("新对话");
    const long = summarizeConversationTitle("请说明十一五政策规划中涉及的一系列实施措施与发展要求，以及这些要求产生了哪些政策影响？", "qa");
    expect(long.length).toBeLessThanOrEqual(28);
    expect(long).not.toContain("？");
  });
});
