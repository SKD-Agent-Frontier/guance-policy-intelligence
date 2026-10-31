import { useState } from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PolicyFacetFilters, PolicyFacetsResponse, PolicyRegionsResponse, RegionNode } from "../types";
import { PolicyFacetsPanel } from "./PolicyFacetsPanel";

afterEach(() => cleanup());

const extraProvinces: RegionNode[] = Array.from({ length: 8 }, (_, index) => ({
  code: `900${String(index).padStart(9, "0")}`,
  name: `测试省${index + 1}`,
  source_type: "省",
  tier: "province",
  children: [],
}));

const regionsFixture: PolicyRegionsResponse = {
  region_version: `sha256:${"a".repeat(64)}`,
  artifact_sha256: "b".repeat(64),
  source: "test",
  source_url: "https://example.test/regions",
  retrieved_at_utc: "2026-10-31T00:00:00Z",
  snapshot_as_of: null,
  official_version: null,
  node_count: 18,
  data: [
    {
      code: "110000000000",
      name: "北京市",
      source_type: "直辖市",
      tier: "province",
      children: [
        { code: "110101000000", name: "东城区", source_type: "市辖区", tier: "county", children: [] },
        { code: "110102000000", name: "西城区", source_type: "市辖区", tier: "county", children: [] },
      ],
    },
    {
      code: "130000000000",
      name: "河北省",
      source_type: "省",
      tier: "province",
      children: [
        {
          code: "130100000000",
          name: "石家庄市",
          source_type: "地级市",
          tier: "prefecture",
          children: [{ code: "130102000000", name: "长安区", source_type: "市辖区", tier: "county", children: [] }],
        },
      ],
    },
    {
      code: "420000000000",
      name: "湖北省",
      source_type: "省",
      tier: "province",
      children: [{ code: "429004000000", name: "仙桃市", source_type: "县级市", tier: "county", children: [] }],
    },
    {
      code: "440000000000",
      name: "广东省",
      source_type: "省",
      tier: "province",
      children: [{ code: "440100000000", name: "广州市", source_type: "地级市", tier: "prefecture", children: [] }],
    },
    ...extraProvinces,
  ],
};

const facetsFixture: PolicyFacetsResponse = {
  dataset: {
    generation_id: "generation-test",
    revision: 1,
    total_documents: 506,
    partial: false,
    source: "current_published_frozen_release",
    region_version: regionsFixture.region_version,
    region_mapping_version: `sha256:${"c".repeat(64)}`,
  },
  query: {
    q: null,
    area: null,
    admin_level: null,
    province_code: null,
    prefecture_code: null,
    county_code: null,
    region_status: null,
    year: null,
    fyp: null,
    fyp_status: null,
    plantype: null,
    plantype_status: null,
  } as unknown as PolicyFacetsResponse["query"],
  facets: {
    admin_level: {
      options: [
        { value: "national", label: "国家级", count: 18 },
        { value: "province", label: "省级", count: 43 },
        { value: "prefecture", label: "市级", count: 155 },
        { value: "county", label: "区县级", count: 290 },
        { value: "unclassified", label: "未分类", count: 0 },
      ],
      regions: {
        province_options: [{ code: "110000000000", name: "北京市", source_type: "直辖市", tier: "province", count: 0 }],
        prefecture_options: [{ code: "130100000000", name: "石家庄市", source_type: "地级市", tier: "prefecture", count: 4 }],
        county_options: [{ code: "110101000000", name: "东城区", source_type: "市辖区", tier: "county", count: 0 }],
        unclassified: { label: "地区未分类", count: 12 },
        not_applicable: { label: "不适用地区树", count: 18 },
      },
    },
    year: { options: [{ value: 2021, label: "2021", count: 0 }, { value: "unknown", label: "年份未知", count: 0 }] },
    fyp: {
      options: [{ value: "unclassified", label: "unclassified", count: 3 }],
      unclassified: { label: "未分类", count: 0 },
    },
    plantype: {
      options: [{ value: "专项规划", label: "专项规划", count: 2 }],
      unclassified: { label: "未分类", count: 0 },
    },
  },
};

function renderPanel(filters: PolicyFacetFilters = {}) {
  const onChange = vi.fn();
  const onRetry = vi.fn();
  function Harness() {
    const [currentFilters, setCurrentFilters] = useState(filters);
    return (
      <PolicyFacetsPanel
        filters={currentFilters}
        facets={facetsFixture}
        regions={regionsFixture}
        status="ready"
        onChange={patch => {
          onChange(patch);
          setCurrentFilters(previous => ({ ...previous, ...patch }));
        }}
        onRetry={onRetry}
      />
    );
  }
  render(<Harness />);
  return { onChange, onRetry };
}

function regionRow() {
  return within(screen.getByRole("heading", { name: "地区" }).parentElement!);
}

describe("PolicyFacetsPanel", () => {
  it("shows all filter dimensions and keeps real zero counts distinct from missing counts", () => {
    renderPanel();
    expect(screen.getByRole("heading", { name: "按维度查找" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "时间（参考年份）" })).toBeVisible();
    const user = userEvent.setup();
    return user.click(regionRow().getByRole("button", { name: /北京市/ })).then(() => {
      expect(regionRow().getByRole("button", { name: "东城区 0" })).toBeVisible();
      expect(regionRow().getByRole("button", { name: "西城区 待统计" })).toBeVisible();
    });
  });

  it("clears lower region selections when changing province and administrative level", async () => {
    const { onChange } = renderPanel({
      admin_level: "county",
      province_code: "110000000000",
      prefecture_code: "130100000000",
      county_code: "110101000000",
    });
    const user = userEvent.setup();
    await user.click(regionRow().getByRole("button", { name: /河北省/ }));
    expect(onChange).toHaveBeenCalledWith({
      province_code: "130000000000",
      prefecture_code: undefined,
      county_code: undefined,
      region_status: undefined,
    });

    await user.click(screen.getByRole("button", { name: "省级 43" }));
    expect(onChange).toHaveBeenLastCalledWith({
      admin_level: "province",
      prefecture_code: undefined,
      county_code: undefined,
    });
    await user.click(screen.getByRole("button", { name: "市级 155" }));
    expect(onChange).toHaveBeenLastCalledWith({ admin_level: "prefecture", county_code: undefined });
  });

  it("shows an empty prefecture group for a municipality while keeping its county choices", async () => {
    const { onChange } = renderPanel({ admin_level: "county" });
    const user = userEvent.setup();
    await user.click(regionRow().getByRole("button", { name: /北京市/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ province_code: "110000000000" }));
    const prefectureGroup = regionRow().getByText("地").parentElement!;
    expect(within(prefectureGroup).getByText("暂无可选项")).toBeVisible();
    expect(regionRow().getByRole("button", { name: /东城区 0/ })).toBeEnabled();
  });

  it.each([
    ["北京市", "东城区", "110101000000"],
    ["湖北省", "仙桃市", "429004000000"],
  ])("selects a real direct county under %s without inventing a prefecture", async (province, county, countyCode) => {
    const { onChange } = renderPanel({ admin_level: "county" });
    const user = userEvent.setup();
    await user.click(regionRow().getByRole("button", { name: new RegExp(province) }));
    await user.click(regionRow().getByRole("button", { name: new RegExp(county) }));
    expect(onChange).toHaveBeenLastCalledWith({
      province_code: province === "北京市" ? "110000000000" : "420000000000",
      prefecture_code: undefined,
      county_code: countyCode,
      region_status: undefined,
    });
  });

  it("keeps unknown-tier nodes visible but unavailable until their tier is maintained", () => {
    const nodes = regionsFixture.data.map(node => node.code === "110000000000"
      ? { ...node, children: [...node.children, { code: "110103000000", name: "待维护地区", source_type: "新类型", tier: "unknown" as const, children: [] }] }
      : node);
    render(
      <PolicyFacetsPanel
        filters={{ province_code: "110000000000" }}
        facets={facetsFixture}
        regions={{ ...regionsFixture, data: nodes }}
        status="ready"
        onChange={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    const unknownNode = regionRow().getByRole("button", { name: "待维护地区 待维护" });
    expect(unknownNode).toBeDisabled();
  });

  it("makes region-unclassified available for province-level records and hides the region row for national", async () => {
    const province = renderPanel({ admin_level: "province" });
    const user = userEvent.setup();
    await user.click(regionRow().getByRole("button", { name: "地区未分类 12" }));
    expect(province.onChange).toHaveBeenCalledWith({
      region_status: "unclassified",
      province_code: undefined,
      prefecture_code: undefined,
      county_code: undefined,
    });
    cleanup();
    renderPanel({ admin_level: "national" });
    expect(screen.queryByRole("heading", { name: "地区" })).not.toBeInTheDocument();
  });

  it("expands region options after selecting the visible list", async () => {
    renderPanel();
    const user = userEvent.setup();
    expect(regionRow().queryByRole("button", { name: /测试省8/ })).not.toBeInTheDocument();
    await user.click(regionRow().getByRole("button", { name: "更多（4）" }));
    expect(regionRow().getByRole("button", { name: /测试省8/ })).toBeVisible();
  });

  it("changes time, planning period, and policy type independently", async () => {
    const { onChange } = renderPanel({ q: "关键词", admin_level: "county" });
    for (const allButton of screen.getAllByRole("button", { name: "全部" })) {
      expect(allButton).not.toHaveTextContent("待统计");
    }
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "2021 0" }));
    expect(onChange).toHaveBeenLastCalledWith({ year: 2021 });
    await user.click(screen.getByRole("button", { name: "unclassified 3" }));
    expect(onChange).toHaveBeenLastCalledWith({ fyp: "unclassified", fyp_status: undefined });
    await user.click(screen.getByRole("button", { name: "专项规划 2" }));
    expect(onChange).toHaveBeenLastCalledWith({ plantype: "专项规划", plantype_status: undefined });
  });
});
