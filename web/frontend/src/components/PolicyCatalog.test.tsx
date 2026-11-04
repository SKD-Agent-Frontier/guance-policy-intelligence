import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PolicyCatalog, reflowPolicyReaderText } from "./PolicyCatalog";
import { catalogClient } from "../api/catalogClient";
import { StrictMode } from "react";

const runtime = vi.hoisted(() => ({ isRealMode: true }));

vi.mock("../api/catalogClient", () => ({
  catalogClient: {
    list: vi.fn(),
    detail: vi.fn(),
    content: vi.fn(),
    readingPage: undefined,
    facets: vi.fn(),
    regions: vi.fn(),
  },
}));
vi.mock("../api/client", () => ({ get isRealMode() { return runtime.isRealMode; } }));

afterEach(() => cleanup());

const facetRegionVersion = `sha256:${"a".repeat(64)}`;
const facetMappingVersion = `sha256:${"b".repeat(64)}`;

const dataset = {
  generation_id: "generation-test",
  revision: 1,
  total_documents: 506,
  partial: null,
  source: "current_published_frozen_release" as const,
  region_version: facetRegionVersion,
  region_mapping_version: facetMappingVersion,
};

const facetsResponse = () => ({
  dataset,
  query: {
    q: null, area: null, fyp: null, plantype: null, admin_level: null, province_code: null,
    prefecture_code: null, county_code: null, region_status: null, year: null,
    fyp_status: null, plantype_status: null,
  },
  facets: {
    admin_level: {
      options: [
        { value: "national", label: "国家级", count: 1 },
        { value: "province", label: "省级", count: 1 },
        { value: "prefecture", label: "市级", count: 1 },
        { value: "county", label: "区县级", count: 1 },
        { value: "unclassified", label: "未分类", count: 0 },
      ],
      regions: {
        province_options: [], prefecture_options: [], county_options: [],
        unclassified: { label: "地区未分类", count: 0 },
        not_applicable: { label: "不适用地区树", count: 1 },
      },
    },
    year: { options: [{ value: "unknown", label: "年份未知", count: 0 }] },
    fyp: { options: [], unclassified: { label: "未分类", count: 0 } },
    plantype: { options: [], unclassified: { label: "未分类", count: 0 } },
  },
});

type TestItem = {
  planid: string;
  title: string | null;
  area: string | null;
  fyp: string | null;
  year?: number | null;
  plantype_label: string | null;
  preview?: string | null;
  matches: Array<{ field: "title" | "body"; offset: number; snippet: string }>;
};

const listResponse = (items: TestItem[], matched = items.length, q: string | null = null, totalPages = 1) => ({
  dataset,
  query: { q, area: null, fyp: null, plantype: null, match_mode: q ? "literal" as const : null },
  matched_documents: matched,
  page: 1,
  page_size: 20,
  total_pages: totalPages,
  items,
});

const detailResponse = (planid: string, title: string, sections = [{ index: 0, level: 1, path: "第一章 总则", start: 0, end: 5000 }]) => ({
  dataset,
  policy: {
    planid,
    title,
    area: "北京市",
    fyp: null,
    plantype_label: "专项规划",
    body_chars: 5000,
    sections,
  },
});

const contentResponse = (planid: string, offset: number, text: string, total = 5000) => ({
  dataset,
  planid,
  title: `政策${planid}`,
  offset,
  limit: 4000,
  total_chars: total,
  has_more: offset + Array.from(text).length < total,
  text,
});

const catalog = vi.mocked(catalogClient);

async function searchCatalog(user = userEvent.setup()) {
  await screen.findByText("当前库共有 506 篇政策文献");
  await user.click(screen.getByRole("button", { name: "检索目录" }));
  await screen.findByRole("button", { name: /数字经济政策|旧政策/ });
}

beforeEach(() => {
  vi.resetAllMocks();
  // Legacy tests exercise the raw code-point content endpoint. Opt in to
  // the newer presentation route explicitly in its own regression case.
  catalog.readingPage = undefined as never;
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  runtime.isRealMode = true;
  catalog.list.mockResolvedValue(listResponse([
    { planid: "P-1", title: "数字经济政策", area: "北京市", fyp: null, plantype_label: "专项规划", matches: [] },
  ]) as never);
  catalog.detail.mockImplementation(async (planid) => detailResponse(planid, `政策${planid}`) as never);
  catalog.content.mockImplementation(async (planid, offset = 0) => contentResponse(planid, offset, `正文${offset}`) as never);
  catalog.facets.mockImplementation(async () => facetsResponse() as never);
  catalog.regions.mockImplementation(async (regionVersion) => ({
    region_version: regionVersion ?? facetRegionVersion,
    artifact_sha256: "d".repeat(64), source: "fixture", source_url: "https://example.test/regions",
    retrieved_at_utc: "2026-10-31T00:00:00Z", snapshot_as_of: null, official_version: null,
    node_count: 0, data: [],
  }) as never);
});

describe("PolicyCatalog", () => {
  it("loads a policy opened from a topic under StrictMode", async () => {
    render(<StrictMode><PolicyCatalog requestedPolicyId="P-1" onBackToTopics={() => {}} /></StrictMode>);
    expect(await screen.findByRole("heading", { name: "政策P-1" })).toBeVisible();
    expect(await screen.findByText("正文0")).toBeVisible();
    expect(screen.queryByText("正在读取政策信息…")).not.toBeInTheDocument();
  });
  it("only shows the library count before an explicit search", async () => {
    render(<PolicyCatalog />);
    expect(await screen.findByText("当前库共有 506 篇政策文献")).toBeVisible();
    expect(screen.queryByText("按政策元数据筛选，或检索标题与原文中的字面内容。")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /数字经济政策/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "目录分页" })).not.toBeInTheDocument();
    expect(catalog.detail).not.toHaveBeenCalled();
    expect(catalog.content).not.toHaveBeenCalled();
  });

  it("highlights every applied literal occurrence in titles and snippets safely", async () => {
    const query = "A+B";
    const title = 'A+B <img src=x onerror="alert(1)"> NEW A+B';
    const snippet = '  A+B <script>alert("x")</script> NEW A+B  ';
    const user = userEvent.setup();
    const { container } = render(<PolicyCatalog />);
    await screen.findByText("当前库共有 506 篇政策文献");
    await user.type(screen.getByLabelText("关键词字面检索"), query);
    catalog.list.mockResolvedValueOnce(listResponse([
      {
        planid: "P-1",
        title,
        area: null,
        fyp: null,
        plantype_label: null,
        matches: [
          { field: "title", offset: 0, snippet: title },
          { field: "body", offset: 0, snippet },
        ],
      },
    ], 1, query) as never);
    await user.click(screen.getByRole("button", { name: "检索目录" }));

    const titleElement = await screen.findByText((_, element) =>
      element?.classList.contains("catalog-policy-title") === true,
    );
    const snippets = container.querySelectorAll<HTMLElement>(".catalog-snippet");
    expect(titleElement.textContent).toBe(title);
    expect(snippets).toHaveLength(1);
    expect(snippets[0].textContent).toBe(snippet.trim());
    expect(titleElement.querySelectorAll("mark")).toHaveLength(2);
    expect(snippets[0].querySelectorAll("mark")).toHaveLength(2);
    expect(container.querySelector("img, script")).toBeNull();

    await user.clear(screen.getByLabelText("关键词字面检索"));
    await user.type(screen.getByLabelText("关键词字面检索"), "NEW");
    expect(titleElement.querySelectorAll("mark")).toHaveLength(2);
    expect(snippets[0].querySelectorAll("mark")).toHaveLength(2);
    expect(catalog.list).toHaveBeenCalledTimes(2);
  });

  it("uses a single prepared preview instead of duplicate title hits and raw table fragments", async () => {
    const user = userEvent.setup();
    const { container } = render(<PolicyCatalog />);
    await screen.findByText("当前库共有 506 篇政策文献");
    await user.type(screen.getByLabelText("关键词字面检索"), "数字经济");
    catalog.list.mockResolvedValueOnce(listResponse([{
      planid: "P-1",
      title: "海西州“十四五”数字经济发展规划",
      area: "海西州", fyp: null, plantype_label: null,
      preview: "推动数字经济与实体经济深度融合。",
      matches: [
        { field: "title", offset: 8, snippet: "海西州“十四五”数字经济发展规划" },
        { field: "body", offset: 0, snippet: "| 数字经济 | 2020年 | 4.8% |\n| --- | --- |" },
      ],
    }], 1, "数字经济") as never);
    await user.click(screen.getByRole("button", { name: "检索目录" }));
    const snippets = container.querySelectorAll<HTMLElement>(".catalog-snippet");
    expect(snippets).toHaveLength(1);
    expect(snippets[0]).toHaveTextContent("推动数字经济与实体经济深度融合。");
    expect(snippets[0]).not.toHaveTextContent("|");
    expect(snippets[0].querySelectorAll("mark")).toHaveLength(1);
  });

  it("labels a truly empty frozen body as 正文缺失", async () => {
    const user = userEvent.setup();
    catalog.detail.mockResolvedValueOnce({
      ...detailResponse("P-1", "广元市十四五现代物流发展规划"),
      policy: { ...detailResponse("P-1", "广元市十四五现代物流发展规划").policy,
        body_chars: 0, body_status: "missing" },
    } as never);
    catalog.content.mockResolvedValueOnce({
      ...contentResponse("P-1", 0, "", 0), body_status: "missing",
    } as never);
    render(<PolicyCatalog />);
    await searchCatalog(user);
    await user.click(screen.getByRole("button", { name: /数字经济政策/ }));
    expect(await screen.findByText("正文缺失")).toBeInTheDocument();
    expect(screen.getByText("0 字")).toBeInTheDocument();
  });

  it("warns on suspected source gibberish and makes unchanged raw text opt-in", async () => {
    const user = userEvent.setup();
    catalog.detail.mockResolvedValueOnce({
      ...detailResponse("P-1", "德宏州十四五规划"),
      policy: { ...detailResponse("P-1", "德宏州十四五规划").policy,
        body_status: "suspected_garbled" },
    } as never);
    catalog.content.mockResolvedValueOnce({
      ...contentResponse("P-1", 0, "ILLEGIBLE !#$%&()*+,-./0123456789", 1000),
      body_status: "suspected_garbled",
    } as never);
    render(<PolicyCatalog />);
    await searchCatalog(user);
    await user.click(screen.getByRole("button", { name: /数字经济政策/ }));
    expect(await screen.findByText(/正文疑似乱码/)).toBeInTheDocument();
    expect(screen.queryByText("ILLEGIBLE !#$%&()*+,-./0123456789")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看异常原始文本" }));
    expect(screen.getByText("ILLEGIBLE !#$%&()*+,-./0123456789")).toBeInTheDocument();
  });

  it("does not render title-only debris as source body when backend marks it missing", async () => {
    const user = userEvent.setup();
    catalog.detail.mockResolvedValueOnce({
      ...detailResponse("P-1", "政策P-1"),
      policy: { ...detailResponse("P-1", "政策P-1").policy,
        body_chars: 5, body_status: "missing" },
    } as never);
    catalog.content.mockResolvedValueOnce({
      ...contentResponse("P-1", 0, "政策P-1", 5), body_status: "missing",
    } as never);
    render(<PolicyCatalog />);
    await searchCatalog(user);
    await user.click(screen.getByRole("button", { name: /数字经济政策/ }));
    expect(await screen.findByText("正文缺失")).toBeInTheDocument();
    expect(screen.queryByText("政策P-1", { selector: ".catalog-prose" })).not.toBeInTheDocument();
  });

  it("keeps the printed policy index in preview but removes it from full reading pages", async () => {
    const user = userEvent.setup();
    const text = [
      "目 录", "前 言................................5",
      "第一章 发展基础....................6",
      "第一节 发展现状....................6",
      "第二章 总体要求...................13",
      "前 言", "真实政策正文。", "第一章 发展基础",
    ].join("\n");
    const tocEnd = text.indexOf("前 言\n真实政策正文");
    catalog.detail.mockResolvedValue({
      ...detailResponse("P-1", "福建省“十四五”全民医疗保障专项规划", []),
      policy: {
        ...detailResponse("P-1", "福建省“十四五”全民医疗保障专项规划", []).policy,
        printed_toc: [0, tocEnd],
      },
    } as never);
    catalog.content.mockResolvedValue(contentResponse("P-1", 0, text, text.length) as never);
    render(<PolicyCatalog />);
    await searchCatalog(user);
    const card = screen.getByRole("button", { name: /数字经济政策/ });
    await user.hover(card);
    const preview = await screen.findByRole("complementary", { name: "政策详情预览" });
    await waitFor(() => expect(preview.querySelectorAll(".catalog-toc-line").length).toBeGreaterThan(2));
    expect(preview.querySelector(".catalog-toc-depth-1")).toHaveTextContent("第一节 发展现状");
    expect(preview.querySelector(".catalog-toc-line")?.textContent).not.toMatch(/\.{3,}\d+/);
    await user.click(card);
    const reader = await screen.findByRole("article", { name: "政策原文正文" });
    await waitFor(() => expect(within(reader).getByText("真实政策正文。")).toBeVisible());
    expect(reader.querySelectorAll(".catalog-toc-line")).toHaveLength(0);
    expect(within(reader).queryByText("目 录")).not.toBeInTheDocument();
    expect(within(reader).getByRole("heading", { name: "第一章 发展基础" })).toBeInTheDocument();
  });

  it("opens a standalone original view and restores the search on return", async () => {
    const user = userEvent.setup();
    render(<PolicyCatalog />);
    await user.type(screen.getByLabelText("关键词字面检索"), "数字经济");
    await searchCatalog(user);
    await user.click(screen.getByRole("button", { name: /数字经济政策/ }));
    expect(await screen.findByRole("main", { name: "政策原文阅读" })).toBeVisible();
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0 });
    expect(await screen.findByText("正文0")).toBeVisible();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "章节目录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "政策列表" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回检索结果" }));
    expect(screen.getByRole("searchbox")).toHaveValue("数字经济");
    expect(screen.getByRole("button", { name: /数字经济政策/ })).toBeVisible();
    expect(catalog.list).toHaveBeenCalledTimes(2);
  });

  it("previews a hovered policy without navigating and dismisses on Escape", async () => {
    const user = userEvent.setup();
    render(<PolicyCatalog />);
    await searchCatalog(user);
    await user.hover(screen.getByRole("button", { name: /数字经济政策/ }));
    expect(await screen.findByRole("complementary", { name: "政策详情预览" })).toBeVisible();
    expect(await screen.findByText("正文0")).toBeVisible();
    expect(screen.getByRole("searchbox")).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("complementary", { name: "政策详情预览" })).not.toBeInTheDocument();
  });

  it("submits literal search, exact filters, and pagination parameters", async () => {
    const user = userEvent.setup();
    render(<PolicyCatalog />);
    await screen.findByText("当前库共有 506 篇政策文献");

    await user.type(screen.getByLabelText("关键词字面检索"), "数字经济");
    await user.type(screen.getByLabelText("地区精确筛选"), "北京市");
    await user.type(screen.getByLabelText("规划期精确筛选"), "十四五");
    await user.type(screen.getByLabelText("政策类型精确筛选"), "专项规划");
    await user.selectOptions(screen.getByLabelText("每页条数"), "10");
    catalog.list.mockResolvedValueOnce(listResponse([
      { planid: "P-1", title: "数字经济政策", area: "北京市", fyp: null, plantype_label: "专项规划", matches: [] },
    ], 1, "数字经济") as never);
    await user.click(screen.getByRole("button", { name: "检索目录" }));

    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith({
      q: "数字经济",
      area: "北京市",
      fyp: "十四五",
      plantype: "专项规划",
      page: 1,
      page_size: 10,
      generation_id: "generation-test", revision: 1, region_version: `sha256:${"a".repeat(64)}`, region_mapping_version: `sha256:${"b".repeat(64)}`,
    }, expect.any(AbortSignal)));
    expect(await screen.findByText(/字面匹配.*篇/)).toBeVisible();
    expect(screen.getByText("当前库共有 506 篇政策文献")).toBeVisible();
  });

  it("requests the selected directory page", async () => {
    catalog.list.mockResolvedValue(listResponse([
      { planid: "P-1", title: "数字经济政策", area: "北京市", fyp: null, plantype_label: "专项规划", matches: [] },
    ], 21, "数字经济", 2) as never);
    const user = userEvent.setup();
    render(<PolicyCatalog />);
    await screen.findByText("当前库共有 506 篇政策文献");
    await user.type(screen.getByLabelText("关键词字面检索"), "数字经济");
    await user.click(screen.getByRole("button", { name: "检索目录" }));
    await user.click(screen.getByRole("button", { name: /下一页/ }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(
      { q: "数字经济", page: 2, page_size: 20, generation_id: "generation-test", revision: 1, region_version: `sha256:${"a".repeat(64)}`, region_mapping_version: `sha256:${"b".repeat(64)}` },
      expect.any(AbortSignal),
    ));
  });

  it("shows an empty result without guessing facets or adding samples", async () => {
    catalog.list.mockResolvedValue(listResponse([], 0) as never);
    render(<PolicyCatalog />);
    await userEvent.setup().click(screen.getByRole("button", { name: "检索目录" }));
    expect(await screen.findByText("当前条件下没有匹配的政策。")).toBeVisible();
    expect(screen.queryByRole("button", { name: /数字经济政策/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/地区分布|政策类型分布/)).not.toBeInTheDocument();
  });

  it("reflows policy prose visually but keeps explicit paragraphs and section headings", () => {
    expect(reflowPolicyReaderText("建设新能源产业集群。发展高\n性能镁合金、镁系材料\n和配套产业。\n\n三、自然条件优势明显\n海西州气候冷凉，\n气温可随地理高度变化。"))
      .toBe("建设新能源产业集群。发展高性能镁合金、镁系材料和配套产业。\n\n三、自然条件优势明显\n海西州气候冷凉，气温可随地理高度变化。");
    expect(reflowPolicyReaderText("## 第二章 发展目标\n第一条 推进工作\n\n开展具体任务。"))
      .toBe("## 第二章 发展目标\n第一条 推进工作\n\n开展具体任务。");
  });

  it("shows missing metadata as unavailable and reads the original in Unicode code-point pages", async () => {
    const user = userEvent.setup();
    const firstPage = `${"甲".repeat(3999)}😀`;
    catalog.detail.mockResolvedValue(detailResponse("P-1", "政策P-1", [
      { index: 0, level: 1, path: "第一章 总则", start: 0, end: 5000 },
      { index: 1, level: 1, path: "第二章 支持措施", start: 4000, end: 5000 },
    ]) as never);
    catalog.content.mockImplementation(async (planid, offset = 0) => contentResponse(planid, offset, offset === 0 ? firstPage : "乙".repeat(1000)) as never);
    render(<PolicyCatalog />);

    await searchCatalog(user);
    await user.hover(screen.getByRole("button", { name: /数字经济政策/ }));
    expect(await screen.findByRole("heading", { name: "政策P-1" })).toBeVisible();
    expect(screen.getAllByText("未提供").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: /第二章 支持措施/ }));
    expect(await screen.findByText("乙".repeat(1000))).toBeVisible();
    await user.click(await screen.findByRole("button", { name: /数字经济政策/ }));
    expect(await screen.findByRole("heading", { name: "政策P-1" })).toBeVisible();
    expect(await screen.findByText(firstPage)).toBeVisible();
    expect(screen.getByLabelText("原文页码")).toHaveTextContent("第 1 / 2 页");
    expect(screen.getByText("5000 字")).toBeInTheDocument();
    expect(screen.queryByText("0 / 5000 字")).not.toBeInTheDocument();
    expect(catalog.content).toHaveBeenCalledWith("P-1", 0, 4000, expect.any(AbortSignal));

    vi.mocked(window.scrollTo).mockClear();
    await user.click(screen.getByRole("button", { name: /下一页/ }));
    await waitFor(() => expect(catalog.content).toHaveBeenLastCalledWith("P-1", 4000, 4000, expect.any(AbortSignal)));
    expect(await screen.findByText("乙".repeat(1000))).toBeVisible();
    expect(screen.getByLabelText("原文页码")).toHaveTextContent("第 2 / 2 页");
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0 });

    await user.click(screen.getByRole("button", { name: /上一页/ }));
    await waitFor(() => expect(catalog.content).toHaveBeenLastCalledWith("P-1", 0, 4000, expect.any(AbortSignal)));

  });

  it("uses paragraph-aligned presentation pages for regular reading and raw offsets for section navigation", async () => {
    const user = userEvent.setup();
    const response = (page: number) => ({
      ...contentResponse("P-1", page === 1 ? 0 : 3975,
        page === 1 ? "第一章 总则\\n\\n" + "政策目标".repeat(50) : "第二页开头完整段落。", 6000),
      page, page_count: 2,
    });
    const readingPage = vi.fn(async (_planid: string, page: number) => response(page));
    catalog.readingPage = readingPage as never;
    catalog.detail.mockResolvedValue(detailResponse("P-1", "数字经济政策", [
      { index: 0, level: 1, path: "第一章 总则", start: 0, end: 3800 },
      { index: 1, level: 1, path: "第二章 发展目标", start: 4100, end: 6000 },
    ]) as never);
    render(<PolicyCatalog />);
    await searchCatalog(user);
    await user.click(screen.getByRole("button", { name: /数字经济政策/ }));
    const reader = await screen.findByRole("article", { name: "政策原文正文" });
    await waitFor(() => expect(readingPage).toHaveBeenCalledWith("P-1", 1, 4000, expect.any(AbortSignal)));
    await user.click(within(reader).getByRole("button", { name: /下一页/ }));
    await waitFor(() => expect(readingPage).toHaveBeenLastCalledWith("P-1", 2, 4000, expect.any(AbortSignal)));
    expect(within(reader).getByLabelText("原文页码")).toHaveTextContent("第 2 / 2 页");

    await user.click(screen.getByRole("button", { name: /返回检索结果/ }));
    await user.hover(screen.getByRole("button", { name: /数字经济政策/ }));
    const preview = await screen.findByRole("complementary", { name: "政策详情预览" });
    await user.click(within(preview).getByRole("button", { name: "第二章 发展目标" }));
    await waitFor(() => expect(catalog.content).toHaveBeenCalledWith("P-1", 4100, 4000, expect.any(AbortSignal)));
  });

  it("supports direct policy page selection and shows reader metadata", async () => {
    const user = userEvent.setup();
    catalog.detail.mockResolvedValue({
      ...detailResponse("P-1", "政策P-1"),
      policy: {
        ...detailResponse("P-1", "政策P-1").policy,
        fyp: "第十四个五年规划", plantype_label: "专项规划", body_chars: 9000,
      },
    } as never);
    catalog.content.mockImplementation(async (planid, offset = 0) =>
      contentResponse(planid, offset, offset === 8000 ? "最后一页正文" : "甲".repeat(4000), 9000) as never);
    render(<PolicyCatalog />);
    await searchCatalog(user);
    await user.click(screen.getByRole("button", { name: /数字经济政策/ }));
    const reader = await screen.findByRole("article", { name: "政策原文正文" });
    const facts = within(reader).getByLabelText("政策元数据");
    expect(facts).toHaveTextContent("地区：北京市");
    expect(facts).toHaveTextContent("规划时期：十四五");
    expect(facts).toHaveTextContent("政策类型：专项规划");
    expect(facts).toHaveTextContent("planid：P-1");
    const picker = within(reader).getByRole("combobox", { name: "跳转原文页码" });
    expect(picker).toHaveValue("1");
    await user.selectOptions(picker, "3");
    await waitFor(() => expect(catalog.content).toHaveBeenLastCalledWith("P-1", 8000, 4000, expect.any(AbortSignal)));
    expect(await within(reader).findByText("最后一页正文")).toBeVisible();
    expect(within(reader).getByLabelText("原文页码")).toHaveTextContent("第 3 / 3 页");
    await user.click(within(reader).getByRole("button", { name: /上一页/ }));
    await waitFor(() => expect(catalog.content).toHaveBeenLastCalledWith("P-1", 4000, 4000, expect.any(AbortSignal)));
  });

  it.each([
    { fyp: "十一五", year: null, expected: "十一五" },
    { fyp: "第十一个五年计划", year: 2008, expected: "十一五" },
    { fyp: "十二五", year: null, expected: "十二五" },
    { fyp: "第十四个五年规划", year: 2024, expected: "十四五" },
    { fyp: "第十四个五年规划", year: null, expected: "十四五" },
    { fyp: "第十三个五年规划", year: null, expected: "十三五" },
    { fyp: "十五五", year: null, expected: "十五五" },
    { fyp: "十五", year: null, expected: "十五" },
    { fyp: "第十个五年规划", year: null, expected: "十五" },
    { fyp: "第十个五年计划", year: null, expected: "十五" },
    { fyp: "政府工作报告", year: 2016, expected: "2016年", body: "正文提到十三五，但该文是工作报告。" },
    { fyp: "政府工作报告", year: null, expected: "未提供" },
    { fyp: null, year: 2020, expected: "2020年" },
    { fyp: "不识别的政策名称", year: 2020, expected: "不识别的政策名称" },
    { fyp: "不识别的政策名称", year: null, expected: "不识别的政策名称" },
    { fyp: "  ", year: 2020, expected: "2020年" },
    { fyp: null, year: null, expected: "未提供" },
  ])("formats period consistently in the card and preview: $expected", async ({ fyp, year, expected, body }) => {
    const periodList = listResponse([
      { planid: "P-1", title: "规划期测试", area: null, fyp, year, plantype_label: null, matches: [] },
    ]);
    catalog.detail.mockResolvedValueOnce({
      ...detailResponse("P-1", "规划期测试"),
      policy: { ...detailResponse("P-1", "规划期测试").policy, fyp, year },
    } as never);
    if (body) catalog.content.mockResolvedValueOnce(contentResponse("P-1", 0, body) as never);

    const user = userEvent.setup();
    render(<PolicyCatalog />);
    await screen.findByText("当前库共有 506 篇政策文献");
    catalog.list.mockResolvedValueOnce(periodList as never);
    await user.click(screen.getByRole("button", { name: "检索目录" }));
    const card = await screen.findByRole("button", { name: /规划期测试/ });
    expect(card.querySelector(".catalog-policy-meta")?.children[1].textContent).toBe(expected);
    await user.hover(card);
    const preview = await screen.findByRole("complementary", { name: "政策详情预览" });
    expect(within(preview).getByText("规划期").nextElementSibling?.textContent).toBe(expected);
    if (body) expect(await within(preview).findByText(body)).toBeVisible();
  });

  it("shows the detail dataset version when it differs from the directory snapshot", async () => {
    catalog.detail.mockResolvedValue({
      ...detailResponse("P-1", "政策P-1"),
      dataset: { ...dataset, generation_id: "new-generation", revision: 2 },
    } as never);
    const user = userEvent.setup();
    render(<PolicyCatalog />);
    await searchCatalog(user);
    await user.click(screen.getByRole("button", { name: /数字经济政策/ }));
    expect(await screen.findByText(/政策信息与检索时的数据版本不同/)).toBeVisible();
  });

  it("keeps an empty real service response explicit and offers retry", async () => {
    const failure = Object.assign(new Error("政策目录服务不可用"), { status: 503 });
    catalog.list.mockRejectedValueOnce(failure);
    render(<PolicyCatalog />);

    expect(await screen.findByRole("alert")).toHaveTextContent("政策目录服务不可用");
    expect(screen.getByRole("button", { name: "重试" })).toBeVisible();
    expect(screen.queryByText(/演示目录数据/)).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("当前库共有 506 篇政策文献")).toBeVisible();
    expect(screen.queryByRole("button", { name: /数字经济政策/ })).not.toBeInTheDocument();
    expect(catalog.list).toHaveBeenCalledTimes(2);
  });

  it("does not let a late list response replace a newer search", async () => {
    let resolveOld!: (value: unknown) => void;
    let resolveNew!: (value: unknown) => void;
    catalog.list
      .mockResolvedValueOnce(listResponse([
        { planid: "P-1", title: "数字经济政策", area: null, fyp: null, plantype_label: null, matches: [] },
      ]) as never)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }) as never)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNew = resolve; }) as never);
    const user = userEvent.setup();
    render(<PolicyCatalog />);
    await screen.findByText("当前库共有 506 篇政策文献");

    const query = screen.getByLabelText("关键词字面检索");
    await user.clear(query);
    await user.type(query, "旧查询");
    await user.click(screen.getByRole("button", { name: "检索目录" }));
    await user.clear(query);
    await user.type(query, "新查询");
    await user.click(screen.getByRole("button", { name: "检索目录" }));

    await act(async () => resolveNew(listResponse([
      { planid: "NEW", title: "新查询结果", area: null, fyp: null, plantype_label: null, matches: [] },
    ]) as never));
    expect(await screen.findByRole("button", { name: /新查询结果/ })).toBeVisible();
    await act(async () => resolveOld(listResponse([
      { planid: "OLD", title: "旧查询结果", area: null, fyp: null, plantype_label: null, matches: [] },
    ]) as never));

    expect(screen.getByRole("button", { name: /新查询结果/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /旧查询结果/ })).not.toBeInTheDocument();
  });

  it("does not let a late detail response replace the selected policy", async () => {
    let resolveOld!: (value: unknown) => void;
    catalog.detail
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }) as never)
      .mockImplementationOnce(async () => detailResponse("P-2", "新政策") as never);
    catalog.list.mockResolvedValue(listResponse([
      { planid: "P-1", title: "旧政策", area: null, fyp: null, plantype_label: null, matches: [] },
      { planid: "P-2", title: "新政策", area: null, fyp: null, plantype_label: null, matches: [] },
    ]) as never);
    const user = userEvent.setup();
    render(<PolicyCatalog />);

    await searchCatalog(user);
    await user.hover(screen.getByRole("button", { name: /旧政策/ }));
    await screen.findByText("正在读取政策信息…");
    await user.hover(screen.getByRole("button", { name: /新政策/ }));
    expect(await screen.findByRole("heading", { name: "新政策" })).toBeVisible();
    await act(async () => resolveOld(detailResponse("P-1", "旧政策详情") as never));
    expect(screen.getByRole("heading", { name: "新政策" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "旧政策详情" })).not.toBeInTheDocument();
  });

  it("labels local catalog data clearly in demo mode", async () => {
    runtime.isRealMode = false;
    render(<PolicyCatalog />);
    await screen.findByText("当前库共有 506 篇政策文献");
    expect(screen.getByText(/演示目录数据/)).toBeVisible();
  });
});
