import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PolicyRichText, policyHeading } from "./PolicyRichText";

afterEach(cleanup);

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
    render(<PolicyRichText planid="P-1" text={"序言\n第一篇 经济发展"}
      originalHints={[{ text: "序言", level: 4 }]} />);
    expect(screen.getByRole("heading", { name: "序言", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "第一篇 经济发展", level: 2 })).toBeInTheDocument();
  });

  it("does not repeat the main document title and centres its approval line", () => {
    const title = "西安市国民经济和社会发展第十四个五年规划和二〇三五年远景目标纲要";
    render(<PolicyRichText planid="10001" documentTitle={title} firstPage text={[
      title,
      "",
      "（2021年2月4日西安市第十六届人民代表大会第六次会议批准）",
      "",
      "第一篇 发展基础",
      "",
      "全面深化改革，推进经济社会发展。",
    ].join("\n")} />);
    expect(screen.queryByRole("heading", { level: 1, name: title })).not.toBeInTheDocument();
    expect(screen.getByText(/2021年2月4日/)).toHaveClass("catalog-document-date");
    expect(screen.getByRole("heading", { level: 2, name: "第一篇 发展基础" })).toBeInTheDocument();
    expect(screen.getByText("全面深化改革，推进经济社会发展。")).toBeInTheDocument();
  });

  it("recognizes hard-wrapped long formal titles without duplicating body headings", () => {
    const title = "中华人民共和国国民经济和社会发展第十四个五年规划和二〇三五年远景目标纲要";
    render(<PolicyRichText planid="10001" documentTitle={title} firstPage
      text={[
        "中华人民共和国国民经济和社会发展第十四个五年规划和二〇三五年",
        "远景目标纲要",
        "",
        "（2021年3月11日全国人民代表大会批准）",
        "",
        "第一篇 开启全面建设社会主义现代化国家新征程",
      ].join("\n")} />);
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
    expect(screen.getByText(/2021年3月11日/)).toHaveClass("catalog-document-date");
    expect(screen.getByRole("heading", { level: 2, name: /第一篇/ })).toBeInTheDocument();
  });

  it("renders a distinct source policy title as a centred H1, without modifying its text", () => {
    const sourceTitle = "西安市国民经济和社会发展第十四个五年规划纲要";
    render(<PolicyRichText planid="10001" documentTitle="西安市十四五规划" firstPage
      text={`${sourceTitle}\n\n2021年3月\n\n第一章 总则`} />);
    const heading = screen.getByRole("heading", { level: 1, name: sourceTitle });
    expect(heading).toHaveClass("catalog-source-title");
    expect(screen.getByText("2021年3月")).toHaveClass("catalog-document-date");
    expect(screen.getByRole("heading", { level: 2, name: "第一章 总则" })).toBeInTheDocument();
  });

  it("does not centre incidental dates inside later pages or ordinary prose", () => {
    const text = "2021年3月\n本季度重点推进项目建设。";
    render(<PolicyRichText planid="10001" documentTitle="政策标题" firstPage={false} text={text} />);
    expect(screen.queryByText("2021年3月")?.classList.contains("catalog-document-date")).toBe(false);
  });

  it("never mistakes long prose or ordinary sentences for headings", () => {
    expect(policyHeading("（四）聚焦就业导向，加强重点群体就业技能培训。实施农民工职业技能提升行动，完善城乡就业体系。")).toBeNull();
    expect(policyHeading("发展目标是扩大就业、提高收入。")).toBeNull();
    expect(policyHeading("政策支持措施包括资金补助和税收减免。")).toBeNull();
  });
});

describe("printed table of contents", () => {
  it("removes leaders and printed page numbers without applying heading styles", () => {
    render(<PolicyRichText planid="P-TOC" softWrap text={[
      "目 录",
      "（五）推动福彩公益事业有序发展······31",
      "（六）促进慈善事业健康发展31",
      "（七）加强区划地名和行政区域界线管理··32",
      "第四章深入推进特色鲜明的基本社会服务体系",
      "（一）持续优化儿童福利政策体系..34",
      "（二）健全未成年人关爱保护体系： 35",
      "",
      "第一章 发展基础",
      "正文内容。",
    ].join("\n")} />);
    expect(screen.getByText("（五）推动福彩公益事业有序发展")).toHaveClass("catalog-toc-line");
    expect(screen.getByText("（六）促进慈善事业健康发展")).toHaveClass("catalog-toc-line");
    expect(screen.getByText("（七）加强区划地名和行政区域界线管理")).toHaveClass("catalog-toc-line");
    expect(screen.getByText("第四章深入推进特色鲜明的基本社会服务体系")).toHaveClass("catalog-toc-line");
    expect(screen.getByText("（一）持续优化儿童福利政策体系")).toHaveClass("catalog-toc-line");
    expect(screen.getByText("（二）健全未成年人关爱保护体系")).toHaveClass("catalog-toc-line");
    expect(screen.queryByText(/·{2,}31/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "第一章 发展基础" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /第四章深入/ })).not.toBeInTheDocument();
  });

  it("omits every printed-TOC line from the full reader, but retains actual chapters", () => {
    const text = [
      "— 3 —", "目 录", "前 言..............................5",
      "第一章 发展基础....................6",
      "第一节 发展现状....................6",
      "第二章 总体要求....................13",
      "— 5 —", "前 言", "人民健康是社会主义现代化的重要标志。",
      "第一章 发展基础", "真实的政策内容。",
    ].join("\n");
    const first = text.indexOf("目 录");
    const end = text.indexOf("前 言\n人民健康");
    const { container } = render(<PolicyRichText planid="32011" firstPage
      text={text} printedToc={[first, end]} omitPrintedToc />);
    expect(container.querySelectorAll(".catalog-toc-line")).toHaveLength(0);
    expect(screen.queryByText("目 录")).not.toBeInTheDocument();
    expect(screen.queryByText(/发展基础[.．·…]{3,}/)).not.toBeInTheDocument();
    expect(screen.getByText("人民健康是社会主义现代化的重要标志。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "第一章 发展基础" })).toBeInTheDocument();
  });

  it("shows printed TOC only in the preview with body-sized text and indentation", () => {
    const text = [
      "目 录", "前 言...................................5",
      "第一章 发展基础..........................6",
      "第一节 发展现状..........................6",
      "（一）工作成效............................7",
      "第二章 总体要求.........................13",
      "前 言", "实际政策正文。",
    ].join("\n");
    const end = text.indexOf("前 言\n实际政策正文");
    render(<PolicyRichText planid="32011" text={text} printedToc={[0, end]} />);
    const entries = document.querySelectorAll(".catalog-toc-line");
    expect(entries).toHaveLength(6);
    expect(entries[1]).toHaveClass("catalog-toc-depth-0");
    expect(entries[3]).toHaveClass("catalog-toc-depth-1");
    expect(entries[4]).toHaveClass("catalog-toc-depth-2");
    expect(entries[1].textContent).toBe("前 言");
    expect(entries[2].textContent).toBe("第一章 发展基础");
    expect(entries[3].textContent).toBe("第一节 发展现状");
    expect(entries[3].textContent).not.toMatch(/[.．·…]{2,}|6$/);
    expect(screen.getByText("实际政策正文。")).toBeInTheDocument();
  });

  it("hides continuing TOC on subsequent reading pages even when the heading is absent", () => {
    const text = "第一节 发展现状................6\n第二节 发展形势...............10\n前 言\n正文开始。";
    const last = "第一节 发展现状................6\n第二节 发展形势...............10\n".length;
    const { container } = render(<PolicyRichText planid="32011" textOffset={350}
      text={text} printedToc={[110, 350 + last]} omitPrintedToc />);
    expect(container.querySelectorAll(".catalog-toc-line")).toHaveLength(0);
    expect(screen.queryByText(/第一节 发展现状/)).not.toBeInTheDocument();
    expect(screen.getByText("正文开始。")).toBeInTheDocument();
  });

  it("does not strip dates, numbers or chapter formatting in ordinary policy paragraphs", () => {
    render(<PolicyRichText planid="P-BODY" text={"第一章 政策措施\n（五）2025年投资项目31个。\n建设目标2025"} />);
    expect(screen.getByRole("heading", { name: "第一章 政策措施" })).toBeInTheDocument();
    expect(screen.getByText("（五）2025年投资项目31个。")).not.toHaveClass("catalog-toc-line");
    expect(screen.getByText("建设目标2025")).toBeInTheDocument();
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

  it("keeps flattened OCR indicator data separate and avoids inventing table columns", () => {
    const source = "“十四五”时期经济社会发展主要指标属性" +
      Array.from({ length: 19 }, (_, n) => " " + (n + 1) + " 地区生产总值 "
        + (n * 1.25).toFixed(2) + " 预期性").join("");
    render(<PolicyRichText planid="P-TABLE" text={source + "\n\n到2035年实现高质量发展。"} />);
    const fallback = screen.getByRole("region", { name: "原文指标表格（结构不完整）" });
    expect(fallback).toHaveTextContent("未重排指标数值");
    expect(fallback).toHaveTextContent(source);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("到2035年实现高质量发展。")).toBeInTheDocument();
  });

  it("does not show standalone printed PDF page numbers as policy text", () => {
    render(<PolicyRichText planid="P-TOC" text={[
      "— 10 —", "目 录", "第一章 发展基础..................................6",
      "— 11 —", "第二章 指导思想………………………………13",
      "实际政策正文未做修改。",
    ].join("\n")} />);
    expect(screen.queryByText("— 10 —")).not.toBeInTheDocument();
    expect(screen.queryByText("— 11 —")).not.toBeInTheDocument();
    expect(screen.getByText("第一章 发展基础")).toHaveClass("catalog-toc-line");
    expect(screen.getByText("第二章 指导思想")).toHaveClass("catalog-toc-line");
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
    expect(screen.getByRole("note")).toHaveTextContent("原文引用的图3未收录对应图片文件");
  });

  it("resets a previous missing-image state when switching policy documents", () => {
    const { rerender } = render(<PolicyRichText planid="10001" text="![图1](media/image1.png)" />);
    fireEvent.error(screen.getByRole("img", { name: "图1" }));
    expect(screen.getByRole("note")).toHaveTextContent("图1");
    rerender(<PolicyRichText planid="10002" text="![图1](media/image1.png)" />);
    const img = screen.getByRole("img", { name: "图1" });
    expect(img.getAttribute("src")).toContain("/api/policies/10002/media/image1.png");
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("preserves unknown source text literally instead of rendering arbitrary HTML", () => {
    render(<PolicyRichText planid="P-1" text={"<img src='https://evil.example/tracker'>"} />);
    expect(screen.getByText("<img src='https://evil.example/tracker'>")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
