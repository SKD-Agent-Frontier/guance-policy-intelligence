import { useMemo, useState } from "react";
import type {
  PolicyAdminLevel,
  PolicyFacetFilters,
  PolicyFacetOption,
  PolicyFacetsResponse,
  PolicyRegionsResponse,
  PolicyYear,
  RegionNode,
} from "../types";
import "./PolicyFacetsPanel.css";

type PanelStatus = "loading" | "ready" | "unavailable" | "error";

export interface PolicyFacetsPanelProps {
  filters: PolicyFacetFilters;
  facets: PolicyFacetsResponse | null;
  regions: PolicyRegionsResponse | null;
  status: PanelStatus;
  errorMessage?: string | null;
  onChange: (patch: Partial<PolicyFacetFilters>) => void;
  onRetry: () => void;
}

const ADMIN_LEVELS: PolicyAdminLevel[] = ["national", "province", "prefecture", "county", "unclassified"];
const INITIAL_OPTION_LIMIT = 8;

function optionCount(count: number | undefined) {
  return typeof count === "number" && Number.isInteger(count) && count >= 0 ? String(count) : "待统计";
}

function findRegionPath(nodes: RegionNode[], code: string): RegionNode[] | null {
  for (const node of nodes) {
    if (node.code === code) return [node];
    const childPath = findRegionPath(node.children, code);
    if (childPath) return [node, ...childPath];
  }
  return null;
}

function findRegion(nodes: RegionNode[], code: string | undefined): RegionNode | null {
  if (!code) return null;
  return findRegionPath(nodes, code)?.at(-1) ?? null;
}

function RegionChoice({
  option,
  count,
  selected,
  onClick,
}: {
  option: Pick<RegionNode, "code" | "name" | "tier">;
  count: number | undefined;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`policy-facets__choice${selected ? " is-selected" : ""}`}
      type="button"
      aria-pressed={selected}
      disabled={option.tier === "unknown"}
      onClick={onClick}
      title={option.tier === "unknown" ? "地区层级待维护" : `${option.name} · ${optionCount(count)} 篇`}
    >
      <span>{option.name}</span>
      <small>{option.tier === "unknown" ? "待维护" : optionCount(count)}</small>
    </button>
  );
}

function ChoiceRow<TValue extends string | number>({
  label,
  options,
  selected,
  allLabel,
  missingOption,
  missingSelected = false,
  onSelect,
  onSelectMissing,
  disabled,
}: {
  label: string;
  options: PolicyFacetOption<TValue>[];
  selected: TValue | undefined;
  allLabel?: string;
  missingOption?: { label: string; count: number };
  missingSelected?: boolean;
  onSelect: (value: TValue | undefined) => void;
  onSelectMissing?: () => void;
  disabled: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const visibleOptions = expanded ? options : options.slice(0, INITIAL_OPTION_LIMIT);
  const hasMore = options.length > INITIAL_OPTION_LIMIT;
  return (
    <div className="policy-facets__row">
      <h3 className="policy-facets__label">{label}</h3>
      <div className="policy-facets__choices">
        {allLabel && (
          <button
            className={`policy-facets__choice${selected === undefined && !missingSelected ? " is-selected" : ""}`}
            type="button"
            aria-pressed={selected === undefined && !missingSelected}
            disabled={disabled}
            onClick={() => onSelect(undefined)}
          >
            <span>{allLabel}</span>
          </button>
        )}
        {missingOption && (
          <button
            className={`policy-facets__choice${missingSelected ? " is-selected" : ""}`}
            type="button"
            aria-pressed={missingSelected}
            disabled={disabled}
            onClick={onSelectMissing}
          >
            <span>{missingOption.label}</span><small>{optionCount(missingOption.count)}</small>
          </button>
        )}
        {visibleOptions.map(option => (
          <button
            className={`policy-facets__choice${selected === option.value ? " is-selected" : ""}`}
            key={String(option.value)}
            type="button"
            aria-pressed={selected === option.value}
            disabled={disabled}
            onClick={() => onSelect(selected === option.value ? undefined : option.value)}
          >
            <span>{option.label}</span><small>{optionCount(option.count)}</small>
          </button>
        ))}
        {hasMore && (
          <button className="policy-facets__more" type="button" onClick={() => setExpanded(value => !value)}>
            {expanded ? "收起" : `更多（${options.length - INITIAL_OPTION_LIMIT}）`}
          </button>
        )}
        {options.length === 0 && !missingOption && <span className="policy-facets__empty">暂无可选项</span>}
      </div>
    </div>
  );
}

function RegionGroup({
  label,
  nodes,
  selectedCode,
  parentSelected,
  countByCode,
  onChoose,
}: {
  label: string;
  nodes: RegionNode[];
  selectedCode: string | undefined;
  parentSelected: boolean;
  countByCode: Map<string, number>;
  onChoose: (node: RegionNode) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const visibleNodes = expanded ? nodes : nodes.slice(0, INITIAL_OPTION_LIMIT);
  return (
    <div className="policy-facets__region-group">
      <span className="policy-facets__region-group-label">{label}</span>
      <div className="policy-facets__choices">
        {visibleNodes.map(node => (
          <RegionChoice
            key={node.code}
            option={node}
            count={countByCode.get(node.code)}
            selected={selectedCode === node.code}
            onClick={() => onChoose(node)}
          />
        ))}
        {nodes.length > INITIAL_OPTION_LIMIT && (
          <button className="policy-facets__more" type="button" onClick={() => setExpanded(value => !value)}>
            {expanded ? "收起" : `更多（${nodes.length - INITIAL_OPTION_LIMIT}）`}
          </button>
        )}
        {nodes.length === 0 && (
          <span className="policy-facets__empty">
            {parentSelected || label === "省" ? "暂无可选项" : "请先选择上级地区"}
          </span>
        )}
      </div>
    </div>
  );
}

export function PolicyFacetsPanel({
  filters,
  facets,
  regions,
  status,
  errorMessage,
  onChange,
  onRetry,
}: PolicyFacetsPanelProps) {
  const ready = status === "ready" && facets !== null;

  const adminOptions = useMemo(() => {
    if (!facets) return [];
    const byValue = new Map(facets.facets.admin_level.options.map(option => [option.value, option]));
    return ADMIN_LEVELS.flatMap(level => {
      const option = byValue.get(level);
      return option ? [option] : [];
    });
  }, [facets]);

  const tree = regions?.data ?? [];
  const selectedProvince = findRegion(tree, filters.province_code);
  const selectedPrefecture = findRegion(tree, filters.prefecture_code);
  const regionOptions = facets?.facets.admin_level.regions;
  const regionCounts = useMemo(() => new Map<string, number>([
    ...(regionOptions?.province_options ?? []),
    ...(regionOptions?.prefecture_options ?? []),
    ...(regionOptions?.county_options ?? []),
  ].map(option => [option.code, option.count])), [regionOptions]);
  const provinceOptions = tree.filter(node => node.tier === "province" || node.tier === "unknown");
  const prefectureOptions = selectedProvince?.children.filter(node => node.tier === "prefecture") ?? [];
  const countyOptions = selectedPrefecture
    ? selectedPrefecture.children.filter(node => node.tier === "county")
    : selectedProvince?.children.filter(node => node.tier === "county") ?? [];
  const maintenanceOptions = selectedPrefecture
    ? selectedPrefecture.children.filter(node => node.tier === "unknown")
    : selectedProvince?.children.filter(node => node.tier === "unknown") ?? [];
  const hiddenForNational = filters.admin_level === "national";

  const chooseAdmin = (value: PolicyAdminLevel | undefined) => {
    const resetRegion = value === "national" || value === "unclassified";
    const clearLowerRegions = value === "province";
    const clearCounties = value === "prefecture";
    onChange({
      admin_level: value,
      ...(resetRegion ? {
        province_code: undefined,
        prefecture_code: undefined,
        county_code: undefined,
        region_status: undefined,
      } : clearLowerRegions ? {
        prefecture_code: undefined,
        county_code: undefined,
        ...(filters.region_status === "not_applicable" ? { region_status: undefined } : {}),
      } : clearCounties ? {
        county_code: undefined,
        ...(filters.region_status === "not_applicable" ? { region_status: undefined } : {}),
      } : (value !== undefined && filters.region_status === "not_applicable") ? { region_status: undefined } : {}),
    });
  };

  const chooseRegion = (node: RegionNode) => {
    if (node.tier === "province") {
      onChange({ province_code: filters.province_code === node.code ? undefined : node.code, prefecture_code: undefined, county_code: undefined, region_status: undefined });
      return;
    }
    if (node.tier === "unknown") return;
    const path = findRegionPath(tree, node.code);
    if (!path) return;
    const province = path.find(part => part.tier === "province");
    const prefecture = path.find(part => part.tier === "prefecture");
    if (node.tier === "prefecture") {
      const alreadySelected = filters.prefecture_code === node.code;
      onChange({
        province_code: province?.code,
        prefecture_code: alreadySelected ? undefined : node.code,
        county_code: undefined,
        region_status: undefined,
      });
      return;
    }
    const alreadySelected = filters.county_code === node.code;
    onChange({
      province_code: province?.code,
      prefecture_code: prefecture?.code,
      county_code: alreadySelected ? undefined : node.code,
      region_status: undefined,
    });
  };

  const selectYear = (value: PolicyYear | undefined) => onChange({ year: value });
  const selectFyp = (value: string | undefined) => onChange({ fyp: value, fyp_status: undefined });
  const selectPlantype = (value: string | undefined) => onChange({ plantype: value, plantype_status: undefined });

  return (
    <section className="policy-facets" aria-label="政策维度筛选" aria-busy={status === "loading"}>
      {status !== "ready" && (
        <div className={`policy-facets__notice policy-facets__notice--${status}`} role={status === "error" ? "alert" : "status"}>
          <span>
            {status === "loading" && "维度筛选加载中…"}
            {status === "unavailable" && (errorMessage || "维度筛选暂不可用，仍可使用下方综合搜索。")}
            {status === "error" && (errorMessage || "维度筛选加载失败。")}
          </span>
          {(status === "unavailable" || status === "error") && (
            <button type="button" className="policy-facets__retry" onClick={onRetry}>重新加载</button>
          )}
        </div>
      )}

      {ready && (
        <>
          <h2 className="policy-facets__title">按维度查找</h2>
          <ChoiceRow
            label="行政级别"
            options={adminOptions}
            selected={filters.admin_level}
            allLabel="全部"
            disabled={!ready}
            onSelect={value => chooseAdmin(value)}
          />

          {!hiddenForNational && (
            <div className="policy-facets__row">
              <h3 className="policy-facets__label">地区</h3>
              <div className="policy-facets__region-content">
                {filters.admin_level !== "national" && (
                  <button
                    className={`policy-facets__choice${filters.region_status === "unclassified" ? " is-selected" : ""}`}
                    type="button"
                    aria-pressed={filters.region_status === "unclassified"}
                    onClick={() => onChange({ region_status: filters.region_status === "unclassified" ? undefined : "unclassified", province_code: undefined, prefecture_code: undefined, county_code: undefined })}
                  >
                    <span>{regionOptions?.unclassified.label ?? "地区未分类"}</span>
                    <small>{optionCount(regionOptions?.unclassified.count)}</small>
                  </button>
                )}
                {!filters.region_status && tree.length === 0 && (
                  <span className="policy-facets__empty">完整地区树尚未加载</span>
                )}
                <RegionGroup label="省" nodes={provinceOptions} selectedCode={filters.province_code} parentSelected countByCode={regionCounts} onChoose={chooseRegion} />
                {filters.admin_level !== "province" && (
                  <RegionGroup label="地" nodes={selectedProvince ? prefectureOptions : []} selectedCode={filters.prefecture_code} parentSelected={Boolean(selectedProvince)} countByCode={regionCounts} onChoose={chooseRegion} />
                )}
                {(filters.admin_level === undefined || filters.admin_level === "unclassified" || filters.admin_level === "county") && (
                  <RegionGroup label="县" nodes={selectedProvince ? countyOptions : []} selectedCode={filters.county_code} parentSelected={Boolean(selectedProvince)} countByCode={regionCounts} onChoose={chooseRegion} />
                )}
                {selectedProvince && maintenanceOptions.length > 0 && (
                  <RegionGroup label="待维护" nodes={maintenanceOptions} selectedCode={undefined} parentSelected countByCode={regionCounts} onChoose={chooseRegion} />
                )}
              </div>
            </div>
          )}

          <ChoiceRow
            label="时间（参考年份）"
            options={facets.facets.year.options}
            selected={filters.year}
            allLabel="全部"
            disabled={!ready}
            onSelect={selectYear}
          />

          <ChoiceRow
            label="规划时期"
            options={facets.facets.fyp.options}
            selected={filters.fyp}
            allLabel="全部"
            missingOption={facets.facets.fyp.unclassified}
            missingSelected={filters.fyp_status === "unclassified"}
            disabled={!ready}
            onSelect={selectFyp}
            onSelectMissing={() => onChange({ fyp: undefined, fyp_status: filters.fyp_status === "unclassified" ? undefined : "unclassified" })}
          />

          <ChoiceRow
            label="政策类型"
            options={facets.facets.plantype.options}
            selected={filters.plantype}
            allLabel="全部"
            missingOption={facets.facets.plantype.unclassified}
            missingSelected={filters.plantype_status === "unclassified"}
            disabled={!ready}
            onSelect={selectPlantype}
            onSelectMissing={() => onChange({ plantype: undefined, plantype_status: filters.plantype_status === "unclassified" ? undefined : "unclassified" })}
          />
        </>
      )}
    </section>
  );
}
