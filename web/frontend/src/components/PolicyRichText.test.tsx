import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PolicyRichText, policyHeading } from "./PolicyRichText";

describe("policy heading hierarchy", () => {
  it.each([
    ["# 总体要求", 2],
    ["## 指导思想", 3],
    ["第一章 发展基础", 2],
    ["第二节 发展目标", 3],
    ["三、自然条件优势明显", 3],
    ["（四）聚焦就业导向", 4],
    ["1. 建设重点项目", 5],
    ["专栏1 “十三五”规划主要指标完成情况", 4],
    ["序言", 2],
    ["第一篇 经济发展", 2],
  ] as const)("recognizes %s as heading %s", (source, level) => {
    expect(policyHeading(source)?.level).toBe(level);
  });

  it("prefers original HTML heading levels where the frozen text matches", () => {
    const hints = [{ text: "经济 发展概况", level: 2 }];
    expect(policyHeading("经济发展概况", hints)).toEqual({
      level: 2, text: "经济发展概况",
    });
    render(<PolicyRichText planid="10001" text="经济发展概况" originalHints={hints} />);
    expect(screen.getByRole("heading", { name: "经济发展概况", level: 2 })).toBeInTheDocument();
  });

  it("keeps 序言 at the same level as 第一篇 even if HTML suggests a smaller heading", () => {
    expect(policyHeading("序言", [{ text: "序言", level: 4 }])).toEqual({ level: 2, text: "序言" });
    expect(policyHeading("第一篇 经济发展")?.level).toBe(2);
    render(<PolicyRichText planid="P-1" text={"序言\\n第一篇 经济发展"}
      originalHints={[{ text: "序言", level: 4 }]} />);
    expect(screen.getByRole("heading", { name: "序言", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "第一篇 经济发展", level: 2 })).toBeInTheDocument();
  });

  it("never mistakes long prose or ordinary sentences for headings", () => {
    expect(policyHeading("（四）聚焦就业导向，加强重点群体就业技能培训。实施农民工职业技能提升行动，完善城乡就业体系。")).toBeNull();
    expect(policyHeading("发展目标是扩大就业、提高收入。")).toBeNull();
    expect(policyHeading("政策支持措施包括资金补助和税收减免。")).toBeNull();
  });
});

describe("policy source rich-text rendering", () => {
  it("renders document hierarchy and a markdown table rather than pipe characters", () => {
    render(<PolicyRichText planid="P-1" softWrap text={[
      "# 总体目标",
      "一、重点任务",
      "专栏1 “十三五”规划主要指标完成情况",
      "| 指标 | 2015年 | 2020年 |",
      "| --- | ---: | :--- |",
      "| GDP | 265.0 | 4.8% |",
      "| 研发投入 | 0.18 | 0.17 |",
      "说明：以上指标采用原文口径。",
    ].join("\n")} />);
    expect(screen.getByRole("heading", { level: 2, name: "总体目标" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "一、重点任务" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 4, name: /专栏1/ })).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "指标" })).toBeInTheDocument();
    expect(within(table).getByRole("cell", { name: "4.8%" })).toBeInTheDocument();
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("说明：以上指标采用原文口径。")).toBeInTheDocument();
  });

  it("recovers a fully consistent flattened legacy table with grouped headings", () => {
    const line = "| 专栏：“十三五”经济社会发展主要目标 |---| | 指标 | 2015 | 2020 | 属性 || 一、经济发展 || 1.地区生产总值 | 494.4 | 600 | 预期性 || 2.人均生产总值 | 10486 | 15000 | 预期性";
    render(<PolicyRichText planid="P-1" softWrap text={line} />);
    const table = screen.getByRole("table", { name: "专栏：“十三五”经济社会发展主要目标" });
    expect(within(table).getAllByRole("columnheader")).toHaveLength(4);
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByRole("cell", { name: "494.4" })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: "一、经济发展" })).toHaveAttribute("colspan", "4");
  });

  it("does not invent table cells when a flattened table's rows are incomplete", () => {
    const fragment = "| 专栏：“十三五”发展目标 |---| | 指标 | 2015 | 2020 || 人均生产总值 | 100";
    render(<PolicyRichText planid="P-1" text={fragment} />);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    const fallback = screen.getByRole("region", { name: "原文表格（无法确认列结构）" });
    expect(fallback).toHaveTextContent("人均生产总值");
  });

  it("renders only safe policy-local images, gracefully reporting missing originals", () => {
    render(<PolicyRichText planid="P-1" text={[
      "![图3](media/WY1589821259615703040.PNG)",
      "",
      "![远程图片](https://evil.example/image.png)",
      "<script>alert(1)</script>",
    ].join("\n")} />);
    const image = screen.getByRole("img", { name: "图3" });
    expect(image.getAttribute("src")).toContain("/api/policies/P-1/media/WY1589821259615703040.PNG");
    expect(screen.queryByRole("img", { name: "远程图片" })).not.toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    fireEvent.error(image);
    expect(screen.getByRole("note")).toHaveTextContent("图片暂不可用");
  });

  it("preserves unknown source text literally instead of rendering arbitrary HTML", () => {
    render(<PolicyRichText planid="P-1" text={"<img src='https://evil.example/tracker'>"} />);
    expect(screen.getByText("<img src='https://evil.example/tracker'>")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
