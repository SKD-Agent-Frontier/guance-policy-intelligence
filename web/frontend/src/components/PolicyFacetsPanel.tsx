import { useState } from "react";
import { ChevronDown } from "lucide-react";
import type {
  PolicyAdminLevel,
  PolicyFacetFilters,
  PolicyFacetOption,
  PolicyFacetsResponse,
  PolicyRegionsResponse,
  PolicyYear,
} from "../types";
import "./PolicyFacetsPanel.css";

type PanelStatus = "loading" | "ready" | "unavailable" | "error";

export interface PolicyFacetsPanelProps {
  filters: PolicyFacetFilters;
  facets: PolicyFacetsResponse | null;
  regions: PolicyRegionsResponse | null; // Preserved for the existing catalog API/version contract.
  status: PanelStatus;
  errorMessage?: string | null;
  onChange: (patch: Partial<PolicyFacetFilters>) => void;
  onRetry: () => void;
}

const ADMIN_LEVELS: PolicyAdminLevel[] = ["national", "province", "prefecture", "county", "unclassified"];
const INITIAL_OPTION_LIMIT = 8;

function ChoiceRow<TValue extends string | number>({
  label, options, selected, missingOption, missingSelected = false,
  onSelect, onSelectMissing,
}: {
  label: string;
  options: PolicyFacetOption<TValue>[];
  selected: TValue | undefined;
  missingOption?: { label: string; count: number };
  missingSelected?: boolean;
  onSelect: (value: TValue | undefined) => void;
  onSelectMissing?: () => void;
}) {
  const [showMore, setShowMore] = useState(false);
  const visibleOptions = showMore ? options : options.slice(0, INITIAL_OPTION_LIMIT);
  return (
    <div className="policy-facets__row">
      <h3 className="policy-facets__label">{label}</h3>
      <div className="policy-facets__choices">
        <button type="button" className={`policy-facets__choice${selected === undefined && !missingSelected ? " is-selected" : ""}`}
          aria-pressed={selected === undefined && !missingSelected} onClick={() => onSelect(undefined)}>全部</button>
        {visibleOptions.map(option => (
          <button type="button" key={String(option.value)}
            className={`policy-facets__choice${selected === option.value ? " is-selected" : ""}`}
            aria-pressed={selected === option.value}
            onClick={() => onSelect(selected === option.value ? undefined : option.value)}>
            <span>{option.label}</span><small>{option.count}</small>
          </button>
        ))}
        {options.length > INITIAL_OPTION_LIMIT && (
          <button type="button" className="policy-facets__more"
            onClick={() => setShowMore(value => !value)}>
            {showMore ? "收起" : `更多（${options.length - INITIAL_OPTION_LIMIT}）`}
          </button>
        )}
        {missingOption && missingOption.count > 0 && (
          <button type="button"
            className={`policy-facets__choice${missingSelected ? " is-selected" : ""}`}
            aria-pressed={missingSelected} onClick={onSelectMissing}>
            <span>其它</span><small>{missingOption.count}</small>
          </button>
        )}
      </div>
    </div>
  );
}

export function PolicyFacetsPanel({
  filters, facets, status, errorMessage, onChange, onRetry,
}: PolicyFacetsPanelProps) {
  // Hover is transient; a click can pin the panel open or explicitly close it.
  const [hovered, setHovered] = useState(false);
  const [manualOpen, setManualOpen] = useState<boolean | null>(null);
  const open = manualOpen ?? hovered;
  const ready = status === "ready" && facets !== null;
  const adminOptions = ADMIN_LEVELS.flatMap(value => {
    const entry = facets?.facets.admin_level.options.find(option => option.value === value);
    if (!entry || (value === "unclassified" && entry.count === 0)) return [];
    return [{ ...entry, label: value === "unclassified" ? "其它" : entry.label }];
  });

  return (
    <section className="policy-facets" aria-label="政策维度筛选"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => { setHovered(false); if (manualOpen !== true) setManualOpen(null); }}>
      <button type="button" className="policy-facets__toggle"
        aria-expanded={open} aria-controls="policy-facets-content"
        onClick={() => setManualOpen(previous => previous === true ? false : true)}>
        <span>按维度查找</span>
        <ChevronDown size={17} aria-hidden="true" className={open ? "is-expanded" : ""} />
      </button>
      {open && (
        <div id="policy-facets-content" className="policy-facets__content">
          {status !== "ready" && (
            <div className={`policy-facets__notice policy-facets__notice--${status}`}
              role={status === "error" ? "alert" : "status"}>
              <span>
                {status === "loading" && "维度筛选加载中…"}
                {status === "unavailable" && (errorMessage || "维度筛选暂不可用，仍可使用下方综合检索。")}
                {status === "error" && (errorMessage || "维度筛选加载失败。")}
              </span>
              {(status === "error" || status === "unavailable") &&
                <button type="button" className="policy-facets__retry" onClick={onRetry}>重新加载</button>}
            </div>
          )}
          {ready && (
            <>
              <ChoiceRow label="行政级别" options={adminOptions}
                selected={filters.admin_level}
                onSelect={value => onChange({
                  admin_level: value,
                  province_code: undefined, prefecture_code: undefined,
                  county_code: undefined, region_status: undefined,
                })} />
              <ChoiceRow label="时间（参考年份）" options={facets.facets.year.options}
                selected={filters.year}
                onSelect={(value: PolicyYear | undefined) => onChange({ year: value })} />
              <ChoiceRow label="规划时期" options={facets.facets.fyp.options}
                selected={filters.fyp} missingOption={facets.facets.fyp.unclassified}
                missingSelected={filters.fyp_status === "unclassified"}
                onSelect={value => onChange({ fyp: value, fyp_status: undefined })}
                onSelectMissing={() => onChange({
                  fyp: undefined,
                  fyp_status: filters.fyp_status === "unclassified" ? undefined : "unclassified",
                })} />
              <ChoiceRow label="政策类型" options={facets.facets.plantype.options}
                selected={filters.plantype} missingOption={facets.facets.plantype.unclassified}
                missingSelected={filters.plantype_status === "unclassified"}
                onSelect={value => onChange({ plantype: value, plantype_status: undefined })}
                onSelectMissing={() => onChange({
                  plantype: undefined,
                  plantype_status: filters.plantype_status === "unclassified" ? undefined : "unclassified",
                })} />
            </>
          )}
        </div>
      )}
    </section>
  );
}
