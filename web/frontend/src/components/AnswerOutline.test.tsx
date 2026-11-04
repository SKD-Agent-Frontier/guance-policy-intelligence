import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AnswerOutline, parseAnswerOutline } from "./AnswerOutline";

describe("AnswerOutline markdown rendering", () => {
  it("keeps bullet lists, tables and quotes as semantic structures", () => {
    const rows = parseAnswerOutline([
      "## 总体情况",
      "- 甲政策提出产业扶持。",
      "- 乙政策提出人才补贴。",
      "",
      "| 维度 | 甲政策 | 乙政策 |",
      "| --- | --- | --- |",
      "| 目标 | 扩大规模 | 提升质量 |",
      "",
      "> 已核验引句。",
    ].join("\n"));
    expect(rows.map(row => row.kind)).toEqual(["heading", "list", "table", "quote"]);

    const { container } = render(<AnswerOutline text={[
      "## 总体情况",
      "- 甲政策提出产业扶持。",
      "- 乙政策提出人才补贴。",
      "",
      "| 维度 | 甲政策 | 乙政策 |",
      "| --- | --- | --- |",
      "| 目标 | 扩大规模 | 提升质量 |",
      "",
      "> 已核验引句。",
    ].join("\n")} />);

    expect(container.querySelector("h3.qa-outline-heading")).toHaveTextContent("总体情况");
    expect(container.querySelectorAll("ul.qa-answer-list li")).toHaveLength(2);
    expect(container.querySelector("table.qa-answer-table thead th")).toHaveTextContent("维度");
    expect(container.querySelectorAll("table.qa-answer-table tbody tr")).toHaveLength(1);
    expect(container.querySelector("blockquote.qa-answer-quote")).toHaveTextContent("已核验引句。");
    expect(container.querySelector("pre")).toBeNull();
  });

  it("keeps prose that merely contains pipes or bullets out of lists and tables", () => {
    const { container } = render(<AnswerOutline text="口径 A|B 为同一政策的不同表述。\n\n见 3.1 节所述内容。" />);
    expect(container.querySelector("ul.qa-answer-list")).toBeNull();
    expect(container.querySelector("table.qa-answer-table")).toBeNull();
    expect(container.querySelector("p.qa-outline-paragraph")).toHaveTextContent("口径 A|B 为同一政策的不同表述。");
  });

  it("renders inline emphasis without executing HTML", () => {
    const { container } = render(<AnswerOutline text="**加粗结论** 与 <img src=x onerror=alert(1)> 内容。" />);
    expect(container.querySelector("strong")).toHaveTextContent("加粗结论");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("keeps citation superscripts inside list items", () => {
    const { container } = render(<AnswerOutline text="- 甲政策事实。" renderText={(value) => <span>{value}[1]</span>} />);
    const item = container.querySelector("ul.qa-answer-list li");
    expect(item).toHaveTextContent("甲政策事实。[1]");
    expect(item?.querySelector("span")).not.toBeNull();
  });
});
