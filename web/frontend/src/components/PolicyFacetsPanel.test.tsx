import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PolicyFacetFilters, PolicyFacetsResponse } from "../types";
import { PolicyFacetsPanel } from "./PolicyFacetsPanel";

afterEach(() => cleanup());

const facets: PolicyFacetsResponse = {
  dataset: {
    generation_id: "generation-test", revision: 1, total_documents: 3, partial: false,
    source: "current_published_frozen_release",
    region_version: `sha256:${"a".repeat(64)}`,
    region_mapping_version: `sha256:${"b".repeat(64)}`,
  },
  query: {
    q: null, area: null, admin_level: null, province_code: null, prefecture_code: null,
    county_code: null, region_status: null, year: null, fyp: null, fyp_status: null,
    plantype: null, plantype_status: null,
  },
  facets: {
    admin_level: {
      options: [
        { value: "national", label: "国家级", count: 2 },
        { value: "province", label: "省级", count: 1 },
        { value: "prefecture", label: "市级", count: 0 },
        { value: "county", label: "区县级", count: 0 },
        { value: "unclassified", label: "未分类", count: 0 },
      ],
      regions: { province_options: [], prefecture_options: [], county_options: [],
        unclassified: { label: "地区未分类", count: 0 },
        not_applicable: { label: "不适用地区树", count: 2 } },
    },
    year: { options: [{ value: 2021, label: "2021", count: 1 }, { value: "unknown", label: "年份未知", count: 0 }] },
    fyp: { options: [{ value: "十四五", label: "十四五", count: 1 }], unclassified: { label: "未分类", count: 1 } },
    plantype: { options: [{ value: "专项规划", label: "专项规划", count: 1 }], unclassified: { label: "未分类", count: 0 } },
  },
};

function renderPanel(initial: PolicyFacetFilters = {}, data = facets) {
  const onChange = vi.fn();
  function Harness() {
    const [filters, setFilters] = useState(initial);
    return <PolicyFacetsPanel filters={filters} facets={data} regions={null} status="ready"
      onChange={patch => { onChange(patch); setFilters(old => ({ ...old, ...patch })); }}
      onRetry={vi.fn()} />;
  }
  render(<Harness />);
  return { onChange };
}

describe("PolicyFacetsPanel compact behavior", () => {
  it("starts closed and reveals four dimension rows inline on click, without a region row", async () => {
    renderPanel();
    const toggle = screen.getByRole("button", { name: "按维度检索" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("heading", { name: "时间（参考年份）" })).not.toBeInTheDocument();
    await userEvent.setup().click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    for (const title of ["行政级别", "时间（参考年份）", "规划时期", "政策类型"]) {
      expect(screen.getByRole("heading", { name: title })).toBeVisible();
    }
    expect(screen.queryByRole("heading", { name: "地区" })).not.toBeInTheDocument();
    expect(screen.queryByText("地区未分类")).not.toBeInTheDocument();
  });

  it("opens on hover, closes on leave and can be explicitly closed with a click", async () => {
    renderPanel();
    const toggle = screen.getByRole("button", { name: "按维度检索" });
    const panel = screen.getByRole("region", { name: "政策维度筛选" });
    fireEvent.mouseEnter(panel);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.mouseLeave(panel);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.setup().click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await userEvent.setup().click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("shows missing values as 其他 at the end and hides zero-count 其他", async () => {
    renderPanel();
    await userEvent.setup().click(screen.getByRole("button", { name: "按维度检索" }));
    const rows = (heading: string) => within(screen.getByRole("heading", { name: heading }).parentElement!);
    const fyp = rows("规划时期");
    expect(fyp.getAllByRole("button").at(-1)).toHaveTextContent("其他");
    expect(fyp.getByRole("button", { name: "其他 1" })).toBeEnabled();
    expect(rows("政策类型").queryByRole("button", { name: /其他/ })).not.toBeInTheDocument();
    expect(rows("行政级别").queryByRole("button", { name: /其他/ })).not.toBeInTheDocument();
  });

  it("places a positive count administrative 其他 at the end", async () => {
    const custom: PolicyFacetsResponse = {
      ...facets, facets: { ...facets.facets,
        admin_level: { ...facets.facets.admin_level, options: facets.facets.admin_level.options.map(o =>
          o.value === "unclassified" ? { ...o, count: 2 } : o) },
      },
    };
    renderPanel({}, custom);
    await userEvent.setup().click(screen.getByRole("button", { name: "按维度检索" }));
    const row = within(screen.getByRole("heading", { name: "行政级别" }).parentElement!);
    expect(row.getAllByRole("button").at(-1)).toHaveTextContent("其他");
  });

  it("orders historical planning periods newest first, keeping non-period metadata and missing last", async () => {
    const custom: PolicyFacetsResponse = {
      ...facets, facets: { ...facets.facets,
        fyp: {
          options: [
            { value: "政府工作报告", label: "政府工作报告", count: 95 },
            { value: "第十一个五年规划", label: "第十一个五年规划", count: 22 },
            { value: "第十三个五年规划", label: "第十三个五年规划", count: 64 },
            { value: "第十五个五年规划", label: "第十五个五年规划", count: 12 },
            { value: "第十二个五年规划", label: "第十二个五年规划", count: 34 },
            { value: "第十四个五年规划", label: "第十四个五年规划", count: 262 },
            { value: "十五", label: "十五", count: 3 },
          ],
          unclassified: { label: "未分类", count: 17 },
        },
      },
    };
    const { onChange } = renderPanel({}, custom);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "按维度检索" }));
    const row = within(screen.getByRole("heading", { name: "规划时期" }).parentElement!);
    const names = row.getAllByRole("button").map(button => button.querySelector("span")?.textContent ?? button.textContent?.trim());
    expect(names).toEqual([
      "全部",
      "第十五个五年规划",
      "第十四个五年规划",
      "第十三个五年规划",
      "第十二个五年规划",
      "第十一个五年规划",
      "十五",
      "政府工作报告",
      "其他",
    ]);
    await user.click(row.getByRole("button", { name: "第十四个五年规划 262" }));
    expect(onChange).toHaveBeenLastCalledWith({ fyp: "第十四个五年规划", fyp_status: undefined });
  });

  it("shows the explicit policy type 其他 only after 总体规划纲要", async () => {
    const custom: PolicyFacetsResponse = {
      ...facets, facets: { ...facets.facets,
        plantype: {
          options: [
            { value: "专项规划", label: "专项规划", count: 409 },
            { value: "其他", label: "其他", count: 10 },
            { value: "总体规划纲要", label: "总体规划纲要", count: 87 },
          ],
          unclassified: { label: "未分类", count: 0 },
        },
      },
    };
    const { onChange } = renderPanel({}, custom);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "按维度检索" }));
    const row = within(screen.getByRole("heading", { name: "政策类型" }).parentElement!);
    expect(row.getAllByRole("button").map(button => button.querySelector("span")?.textContent ?? button.textContent?.trim()))
      .toEqual(["全部", "专项规划", "总体规划纲要", "其他"]);
    await user.click(row.getByRole("button", { name: "其他 10" }));
    expect(onChange).toHaveBeenLastCalledWith({ plantype: "其他", plantype_status: undefined });
  });

  it("keeps distinct year, fyp and type filters", async () => {
    const { onChange } = renderPanel();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "按维度检索" }));
    await user.click(screen.getByRole("button", { name: "2021 1" }));
    expect(onChange).toHaveBeenLastCalledWith({ year: 2021 });
    await user.click(screen.getByRole("button", { name: "十四五 1" }));
    expect(onChange).toHaveBeenLastCalledWith({ fyp: "十四五", fyp_status: undefined });
    await user.click(screen.getByRole("button", { name: "专项规划 1" }));
    expect(onChange).toHaveBeenLastCalledWith({ plantype: "专项规划", plantype_status: undefined });
  });

  it("preserves legacy region code cleanup when administrative level changes", async () => {
    const { onChange } = renderPanel({ province_code: "110000000000", county_code: "110101000000" });
    await userEvent.setup().click(screen.getByRole("button", { name: "按维度检索" }));
    await userEvent.setup().click(screen.getByRole("button", { name: "国家级 2" }));
    expect(onChange).toHaveBeenLastCalledWith({
      admin_level: "national", province_code: undefined, prefecture_code: undefined,
      county_code: undefined, region_status: undefined,
    });
  });

  it("keeps load failure messages inside the expanded panel", async () => {
    render(<PolicyFacetsPanel filters={{}} facets={null} regions={null} status="unavailable"
      errorMessage="维度筛选暂不可用" onChange={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.queryByText("维度筛选暂不可用")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "按维度检索" }));
    expect(screen.getByText("维度筛选暂不可用")).toBeVisible();
  });
});
