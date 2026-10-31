import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PolicyCatalog } from "./PolicyCatalog";
import { catalogClient } from "../api/catalogClient";
import { ApiError } from "../api/http";
import type {
  PolicyFacetFilters,
  PolicyFacetsResponse,
  PolicyListParams,
  PolicyListResponse,
  PolicyRegionsResponse,
} from "../types";

const runtime = vi.hoisted(() => ({ isRealMode: true }));

vi.mock("../api/catalogClient", () => ({
  catalogClient: {
    list: vi.fn(),
    detail: vi.fn(),
    content: vi.fn(),
    facets: vi.fn(),
    regions: vi.fn(),
  },
}));
vi.mock("../api/client", () => ({ get isRealMode() { return runtime.isRealMode; } }));

afterEach(() => cleanup());

const regionVersion = `sha256:${"a".repeat(64)}`;
const mappingVersion = `sha256:${"b".repeat(64)}`;
const pin = {
  generation_id: "fixture-gen",
  revision: 1,
  region_version: regionVersion,
  region_mapping_version: mappingVersion,
};
const updatedPin = {
  generation_id: "fixture-gen-next",
  revision: 2,
  region_version: `sha256:${"c".repeat(64)}`,
  region_mapping_version: `sha256:${"d".repeat(64)}`,
};

const fixtureTree: PolicyRegionsResponse["data"] = [
  {
    code: "110000000000", name: "北京市", source_type: "直辖市", tier: "province",
    children: [
      { code: "110101000000", name: "东城区", source_type: "市辖区", tier: "county", children: [] },
      { code: "110102000000", name: "西城区", source_type: "市辖区", tier: "county", children: [] },
    ],
  },
  {
    code: "130000000000", name: "河北省", source_type: "省", tier: "province",
    children: [{
      code: "130100000000", name: "石家庄市", source_type: "地级市", tier: "prefecture",
      children: [{ code: "130102000000", name: "长安区", source_type: "市辖区", tier: "county", children: [] }],
    }],
  },
];

function facetsFixture(filters: PolicyFacetFilters = {}, versions = pin): PolicyFacetsResponse {
  return {
    dataset: {
      ...versions, total_documents: 3, partial: false, source: "current_published_frozen_release",
    },
    query: {
      q: filters.q ?? null,
      area: filters.area ?? null,
      fyp: filters.fyp ?? null,
      plantype: filters.plantype ?? null,
      admin_level: filters.admin_level ?? null,
      province_code: filters.province_code ?? null,
      prefecture_code: filters.prefecture_code ?? null,
      county_code: filters.county_code ?? null,
      region_status: filters.region_status ?? null,
      year: filters.year ?? null,
      fyp_status: filters.fyp_status ?? null,
      plantype_status: filters.plantype_status ?? null,
    },
    facets: {
      admin_level: {
        options: [
          { value: "national", label: "国家级", count: 0 },
          { value: "province", label: "省级", count: 0 },
          { value: "prefecture", label: "市级", count: 0 },
          { value: "county", label: "区县级", count: 3 },
          { value: "unclassified", label: "未分类", count: 0 },
        ],
        regions: {
          province_options: [
            { code: "110000000000", name: "北京市", source_type: "直辖市", tier: "province", count: 0 },
            { code: "130000000000", name: "河北省", source_type: "省", tier: "province", count: 2 },
          ],
          prefecture_options: [
            { code: "130100000000", name: "石家庄市", source_type: "地级市", tier: "prefecture", count: 1 },
          ],
          county_options: [
            { code: "110101000000", name: "东城区", source_type: "市辖区", tier: "county", count: 0 },
            { code: "130102000000", name: "长安区", source_type: "市辖区", tier: "county", count: 1 },
          ],
          unclassified: { label: "地区未分类", count: 0 },
          not_applicable: { label: "不适用地区树", count: 0 },
        },
      },
      year: { options: [
        { value: 2021, label: "2021", count: 1 },
        { value: 2020, label: "2020", count: 1 },
        { value: "unknown", label: "年份未知", count: 0 },
      ] },
      fyp: {
        options: [
          { value: "unclassified", label: "unclassified", count: 1 },
          { value: "第十四个五年规划", label: "第十四个五年规划", count: 1 },
        { value: "政府工作报告", label: "政府工作报告", count: 0 },
        ],
        unclassified: { label: "未分类", count: 1 },
      },
      plantype: {
        options: [
          { value: "unclassified", label: "unclassified", count: 1 },
          { value: "专项规划", label: "专项规划", count: 2 },
        ],
        unclassified: { label: "未分类", count: 0 },
      },
    },
  };
}

function regionsFixture(version = regionVersion): PolicyRegionsResponse {
  return {
    region_version: version,
    artifact_sha256: "d".repeat(64),
    source: "fixture",
    source_url: "https://example.test/regions",
    retrieved_at_utc: "2026-10-31T00:00:00Z",
    snapshot_as_of: null,
    official_version: null,
    node_count: 6,
    data: fixtureTree,
  };
}

const listResult = (params: PolicyListParams = {}): PolicyListResponse => ({
  dataset: { ...facetsFixture().dataset },
  query: {
    q: params.q ?? null,
    area: params.area ?? null,
    fyp: params.fyp ?? null,
    plantype: params.plantype ?? null,
    match_mode: params.q ? "literal" : null,
    admin_level: params.admin_level ?? null,
    province_code: params.province_code ?? null,
    prefecture_code: params.prefecture_code ?? null,
    county_code: params.county_code ?? null,
    region_status: params.region_status ?? null,
    year: params.year ?? null,
    fyp_status: params.fyp_status ?? null,
    plantype_status: params.plantype_status ?? null,
  },
  matched_documents: 1,
  page: params.page ?? 1,
  page_size: params.page_size ?? 20,
  total_pages: 4,
  items: [{
    planid: "p1", title: "数字经济促进政策2021年", area: "北京市", fyp: "第十四个五年规划",
    year: 2021, plantype_label: "专项规划", admin_level: "county", region_code: "110101000000",
    region_status: "mapped", matches: [],
  }],
});

const catalog = vi.mocked(catalogClient);

async function readyCatalog() {
  render(<PolicyCatalog />);
  await screen.findByText("当前库共有 3 篇政策文献");
  const panel = await screen.findByRole("region", { name: "政策维度筛选" });
  await within(panel).findByRole("button", { name: /年份未知/ });
  return panel;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  runtime.isRealMode = true;
  catalog.list.mockImplementation(async params => listResult(params) as never);
  catalog.facets.mockImplementation(async filters => facetsFixture(filters ?? {}) as never);
  catalog.regions.mockImplementation(async version => regionsFixture(version ?? regionVersion) as never);
  catalog.detail.mockImplementation(async planid => ({
    dataset: facetsFixture().dataset,
    policy: {
      planid, title: `政策${planid}`, area: "北京市", fyp: null, year: null,
      plantype_label: "专项规划", body_chars: 2, sections: [],
    },
  }) as never);
  catalog.content.mockImplementation(async (planid, offset = 0, limit = 4000) => ({
    dataset: facetsFixture().dataset, planid, title: `政策${planid}`, offset, limit,
    total_chars: 2, has_more: false, text: "正文",
  }) as never);
});

describe("PolicyCatalog facet integration", () => {
  it("applies year and fyp independently with the same search filters and complete version pin", async () => {
    const user = userEvent.setup();
    const panel = await readyCatalog();
    await user.type(screen.getByLabelText("关键词字面检索"), "数字经济");
    await user.type(screen.getByLabelText("地区精确筛选"), "北京市");
    await user.type(screen.getByLabelText("规划期精确筛选"), "第十四个五年规划");
    await user.type(screen.getByLabelText("政策类型精确筛选"), "专项规划");
    await user.click(screen.getByRole("button", { name: "检索目录" }));

    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({
      q: "数字经济", area: "北京市", fyp: "第十四个五年规划", plantype: "专项规划", ...pin,
    }), expect.any(AbortSignal)));

    await user.clear(screen.getByLabelText("地区精确筛选"));
    await user.type(screen.getByLabelText("地区精确筛选"), "上海市");
    await user.click(within(panel).getByRole("button", { name: /年份未知/ }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({
      q: "数字经济", area: "上海市", fyp: "第十四个五年规划", plantype: "专项规划", year: "unknown", ...pin,
    }), expect.any(AbortSignal)));
    expect(screen.getByRole("searchbox", { name: "关键词字面检索" })).toHaveValue("数字经济");
    expect(screen.getByLabelText("地区精确筛选")).toHaveValue("上海市");

    await user.click(within(panel).getByRole("button", { name: "政府工作报告 0" }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({
      q: "数字经济", area: "上海市", fyp: "政府工作报告", plantype: "专项规划", year: "unknown", ...pin,
    }), expect.any(AbortSignal)));
    const lastFacetRequest = catalog.facets.mock.calls.at(-1)?.[0];
    expect(lastFacetRequest).toMatchObject({ q: "数字经济", area: "上海市", year: "unknown", fyp: "政府工作报告", plantype: "专项规划" });
    expect(catalog.regions).toHaveBeenLastCalledWith(regionVersion, expect.any(AbortSignal));
    expect(screen.getByLabelText("已应用筛选条件")).toHaveTextContent("年份：未知");
  });

  it("keeps explicit zero-count choices selectable and distinguishes missing values from the real string sentinel", async () => {
    const user = userEvent.setup();
    const panel = await readyCatalog();
    const fypRow = () => within(panel).getByRole("heading", { name: "规划时期" }).parentElement!;
    const plantypeRow = () => within(panel).getByRole("heading", { name: "政策类型" }).parentElement!;

    await user.click(within(panel).getByRole("button", { name: /年份未知 0/ }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ year: "unknown", ...pin }), expect.any(AbortSignal)));

    await user.click(within(fypRow()).getByRole("button", { name: "unclassified 1" }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ fyp: "unclassified", year: "unknown", ...pin }), expect.any(AbortSignal)));
    expect(catalog.list.mock.calls.at(-1)?.[0]).not.toHaveProperty("fyp_status");
    expect(screen.getByLabelText("已应用筛选条件")).toHaveTextContent("规划期：unclassified");
    expect(screen.getByLabelText("已应用筛选条件")).not.toHaveTextContent("fyp_status");

    await user.click(within(fypRow()).getByRole("button", { name: "未分类 1" }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ fyp_status: "unclassified", year: "unknown", ...pin }), expect.any(AbortSignal)));
    expect(screen.getByLabelText("已应用筛选条件")).toHaveTextContent("规划期状态：未提供");

    await user.click(within(plantypeRow()).getByRole("button", { name: "unclassified 1" }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ plantype: "unclassified", fyp_status: "unclassified", ...pin }), expect.any(AbortSignal)));
    expect(catalog.list.mock.calls.at(-1)?.[0]).not.toHaveProperty("plantype_status");

    await user.click(within(plantypeRow()).getByRole("button", { name: "未分类 0" }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ plantype_status: "unclassified", fyp_status: "unclassified", ...pin }), expect.any(AbortSignal)));
    expect(catalog.list.mock.calls.at(-1)?.[0]).not.toHaveProperty("plantype");
  });

  it("pins repeated facet requests and does not restart the one-row bootstrap list after applying filters", async () => {
    const user = userEvent.setup();
    const panel = await readyCatalog();
    const bootstrapCalls = () => catalog.list.mock.calls.filter(([params]) => params?.page_size === 1);
    expect(bootstrapCalls()).toHaveLength(1);

    await user.click(within(panel).getByRole("button", { name: "2021 1" }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ year: 2021, ...pin }), expect.any(AbortSignal)));
    await user.click(within(panel).getByRole("button", { name: "2021 1" }));
    await waitFor(() => expect(catalog.list.mock.calls.at(-1)?.[0]).not.toHaveProperty("year"));

    expect(catalog.facets.mock.calls.at(-1)?.[0]).toMatchObject(pin);
    expect(bootstrapCalls()).toHaveLength(1);
  });

  it("uses region-tree ancestry, clears descendants when the province changes, and combines exact area with codes", async () => {
    const user = userEvent.setup();
    const panel = await readyCatalog();
    await user.type(screen.getByLabelText("地区精确筛选"), "北京市");
    await user.type(screen.getByLabelText("关键词字面检索"), "数字经济");
    await user.click(within(panel).getByRole("button", { name: /区县级/ }));
    await user.click(within(panel).getByRole("button", { name: /河北省 2/ }));
    await user.click(within(panel).getByRole("button", { name: /石家庄市 1/ }));
    await user.click(within(panel).getByRole("button", { name: /长安区 1/ }));

    await user.click(within(panel).getByRole("button", { name: /省级/ }));
    await user.click(within(panel).getByRole("button", { name: /北京市 0/ }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({
      q: "数字经济", area: "北京市", admin_level: "province", province_code: "110000000000",
      ...pin,
    }), expect.any(AbortSignal)));
    expect(catalog.facets.mock.calls.at(-1)?.[0]).toMatchObject({
      area: "北京市", province_code: "110000000000",
    });
  });

  it("does not show region-tree choices for national documents", async () => {
    const user = userEvent.setup();
    const panel = await readyCatalog();
    await user.click(within(panel).getByRole("button", { name: /区县级/ }));
    await user.click(within(panel).getByRole("button", { name: "地区未分类 0" }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({
      admin_level: "county", region_status: "unclassified", ...pin,
    }), expect.any(AbortSignal)));

    await user.click(within(panel).getByRole("button", { name: /国家级 0/ }));

    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ admin_level: "national", ...pin }), expect.any(AbortSignal)));
    expect(catalog.list.mock.calls.at(-1)?.[0]).not.toHaveProperty("region_status");
    expect(within(panel).queryByRole("button", { name: /北京市|河北省|东城区|西城区|石家庄市/ })).not.toBeInTheDocument();
  });

  it("shows missing region counts as pending while an explicit zero remains visible", async () => {
    const user = userEvent.setup();
    const panel = await readyCatalog();
    await user.click(within(panel).getByRole("button", { name: /区县级/ }));
    await user.click(within(panel).getByRole("button", { name: /北京市 0/ }));

    expect(within(panel).getByRole("button", { name: /东城区 0/ })).toBeEnabled();
    expect(within(panel).getByRole("button", { name: /西城区 待统计/ })).toBeEnabled();
  });

  it.each([400, 404, 503])("keeps legacy search and reading available when facets returns %i", async status => {
    catalog.facets.mockRejectedValue(new ApiError("http", "维度接口缺失", status));
    catalog.list.mockImplementation(async params => listResult(params) as never);
    const user = userEvent.setup();
    render(<PolicyCatalog />);

    expect(await screen.findByText("当前库共有 3 篇政策文献")).toBeVisible();
    expect(await screen.findByText(/维度筛选暂不可用/)).toBeVisible();
    await user.type(screen.getByLabelText("关键词字面检索"), "数字经济");
    await user.type(screen.getByLabelText("地区精确筛选"), "北京市");
    await user.click(screen.getByRole("button", { name: "检索目录" }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith({
      q: "数字经济", area: "北京市", page: 1, page_size: 20,
    }, expect.any(AbortSignal)));

    const policy = await screen.findByRole("button", { name: /促进政策2021年/ });
    await user.click(policy);
    expect(await screen.findByRole("heading", { name: "政策p1" })).toBeVisible();
    expect(catalog.detail).toHaveBeenCalledWith("p1", expect.any(AbortSignal));
    expect(catalog.content).toHaveBeenCalledWith("p1", 0, 4000, expect.any(AbortSignal));
    expect(screen.queryByText(/演示目录数据/)).not.toBeInTheDocument();
  });

  it.each([400, 503])("does not drop a selected new filter when refreshing facets returns %i", async status => {
    const user = userEvent.setup();
    const panel = await readyCatalog();
    const callsBefore = catalog.list.mock.calls.length;
    catalog.facets.mockRejectedValueOnce(new ApiError("http", "维度暂不可用", status));

    await user.click(within(panel).getByRole("button", { name: /2021 1/ }));
    expect(await screen.findByText(/维度筛选或版本校验暂不可用/)).toBeVisible();
    expect(catalog.list).toHaveBeenCalledTimes(callsBefore);
    expect(screen.getByLabelText("已应用筛选条件")).toHaveTextContent("年份：2021");
  });

  it("rejects an old facet response after a newer selection and reloads on list version conflict", async () => {
    const user = userEvent.setup();
    const panel = await readyCatalog();
    let resolveOld!: (value: PolicyFacetsResponse) => void;
    let resolveNew!: (value: PolicyFacetsResponse) => void;
    catalog.facets
      .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }) as never)
      .mockImplementationOnce(() => new Promise(resolve => { resolveNew = resolve; }) as never)
      .mockImplementation(async filters => facetsFixture(filters ?? {}) as never);

    await user.click(within(panel).getByRole("button", { name: /2021 1/ }));
    await user.type(screen.getByLabelText("规划期精确筛选"), "政府工作报告");
    await user.click(screen.getByRole("button", { name: "检索目录" }));
    resolveNew(facetsFixture({ year: 2021, fyp: "政府工作报告" }));
    await waitFor(() => expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ year: 2021, fyp: "政府工作报告", ...pin }), expect.any(AbortSignal)));
    resolveOld(facetsFixture({ year: 2021 }));
    await waitFor(() => expect(screen.getByLabelText("已应用筛选条件")).toHaveTextContent("规划期：政府工作报告"));
    expect(screen.getByLabelText("已应用筛选条件")).toHaveTextContent("年份：2021");
    expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ year: 2021, fyp: "政府工作报告", ...pin }), expect.any(AbortSignal));
    expect(screen.queryByText(/版本校验暂不可用/)).not.toBeInTheDocument();

    catalog.list.mockRejectedValueOnce(new ApiError("http", "数据版本冲突", 409));
    catalog.facets.mockImplementation(async filters => {
      const versions = filters?.generation_id === pin.generation_id ? pin : updatedPin;
      return facetsFixture(filters ?? {}, versions) as never;
    });
    await user.click(within(panel).getByRole("button", { name: /2020 1/ }));
    await waitFor(() => expect(catalog.facets).toHaveBeenCalledTimes(5));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("目录数据版本已更新"));
    expect(catalog.list).toHaveBeenLastCalledWith(expect.objectContaining({ year: 2020, fyp: "政府工作报告", ...pin }), expect.any(AbortSignal));
    expect(catalog.facets.mock.calls.at(-1)?.[0]).not.toHaveProperty("generation_id");
    expect(catalog.regions).toHaveBeenLastCalledWith(updatedPin.region_version, expect.any(AbortSignal));
    await waitFor(() => expect(within(panel).getByRole("button", { name: /2020 1/ })).toBeVisible());
  });

  it("rejects facets whose region tree version differs from the pinned response", async () => {
    catalog.regions.mockResolvedValueOnce(regionsFixture(`sha256:${"c".repeat(64)}`) as never);
    render(<PolicyCatalog />);

    expect((await screen.findAllByRole("alert")).some(item => item.textContent?.includes("版本"))).toBe(true);
    expect(catalog.list).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /年份未知/ })).not.toBeInTheDocument();
  });

  it("does not display list items returned from a different pinned version group", async () => {
    const user = userEvent.setup();
    const panel = await readyCatalog();
    catalog.list.mockImplementationOnce(async params => ({
      ...listResult(params),
      dataset: { ...listResult(params).dataset, region_version: `sha256:${"c".repeat(64)}` },
    }) as never);

    await user.click(within(panel).getByRole("button", { name: "2021 1" }));
    expect((await screen.findAllByRole("alert")).some(item => item.textContent?.includes("版本"))).toBe(true);
    expect(screen.queryByRole("button", { name: /促进政策2021年/ })).not.toBeInTheDocument();
  });

  it("uses dataset total_documents for the library count, not page count", async () => {
    await readyCatalog();
    expect(screen.getByText("当前库共有 3 篇政策文献")).toBeVisible();
    expect(screen.queryByText("当前库共有 4 篇政策文献")).not.toBeInTheDocument();
  });
});
