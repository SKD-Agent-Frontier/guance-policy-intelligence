import type {
  CatalogDatasetInfo,
  PolicyAdminLevel,
  PolicyFacetsResponse,
  PolicyRegionsResponse,
  PolicyVersionPin,
  RegionNode,
} from "../types";

const VERSION_PATTERN = /^sha256:[0-9a-f]{64}$/;
const CODE_PATTERN = /^\d{12}$/;
const ADMIN_LEVELS: PolicyAdminLevel[] = ["national", "province", "prefecture", "county", "unclassified"];

export function extractPolicyVersionPin(dataset: CatalogDatasetInfo): PolicyVersionPin | null {
  const mappingVersion = dataset.region_mapping_version;
  if (
    typeof dataset.generation_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(dataset.generation_id)
    || !Number.isInteger(dataset.revision) || dataset.revision < 1
    || typeof dataset.region_version !== "string" || !VERSION_PATTERN.test(dataset.region_version)
    || (mappingVersion !== null && (typeof mappingVersion !== "string" || !VERSION_PATTERN.test(mappingVersion)))
  ) return null;

  return {
    generation_id: dataset.generation_id,
    revision: dataset.revision,
    region_version: dataset.region_version,
    region_mapping_version: mappingVersion ?? "unavailable",
  };
}

export function samePolicyVersionPin(
  left: CatalogDatasetInfo,
  right: CatalogDatasetInfo,
): boolean {
  const leftPin = extractPolicyVersionPin(left);
  const rightPin = extractPolicyVersionPin(right);
  return leftPin !== null && rightPin !== null
    && leftPin.generation_id === rightPin.generation_id
    && leftPin.revision === rightPin.revision
    && leftPin.region_version === rightPin.region_version
    && leftPin.region_mapping_version === rightPin.region_mapping_version
    && left.total_documents === right.total_documents
    && left.partial === right.partial;
}

export function regionTreeMatchesVersion(
  response: Pick<PolicyRegionsResponse, "region_version">,
  pin: Pick<PolicyVersionPin, "region_version">,
): boolean {
  return response.region_version === pin.region_version;
}

function assertRecord(value: unknown, label: string): asserts value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
}

function assertNonNegativeInteger(value: unknown, label: string): asserts value is number {
  if (!Number.isInteger(value) || (value as number) < 0) {
    throw new TypeError(`${label} must be a non-negative integer`);
  }
}

function assertCountedOptions(options: unknown, label: string): asserts options is Array<Record<string, unknown>> {
  if (!Array.isArray(options)) throw new TypeError(`${label} must be an array`);
  for (const [index, option] of options.entries()) {
    assertRecord(option, `${label}[${index}]`);
    if (typeof option.label !== "string") throw new TypeError(`${label}[${index}].label must be a string`);
    assertNonNegativeInteger(option.count, `${label}[${index}].count`);
  }
}

function assertSibling(value: unknown, label: string): void {
  assertRecord(value, label);
  if (typeof value.label !== "string") throw new TypeError(`${label}.label must be a string`);
  assertNonNegativeInteger(value.count, `${label}.count`);
}

function validateTreeNodes(nodes: unknown, expectedCount?: number): number {
  if (!Array.isArray(nodes)) throw new TypeError("regions.data must be an array");
  const codes = new Set<string>();
  let count = 0;
  const visit = (node: unknown, parentTier: RegionNode["tier"] | null): void => {
    assertRecord(node, "region node");
    if (typeof node.code !== "string" || !CODE_PATTERN.test(node.code)) throw new TypeError("region code must be 12 digits");
    if (codes.has(node.code)) throw new TypeError(`duplicate region code: ${node.code}`);
    codes.add(node.code);
    if (typeof node.name !== "string" || typeof node.source_type !== "string") {
      throw new TypeError("region name and source_type must be strings");
    }
    if (node.tier !== "province" && node.tier !== "prefecture" && node.tier !== "county" && node.tier !== "unknown") {
      throw new TypeError("region tier is invalid");
    }
    if (!Array.isArray(node.children)) throw new TypeError("region children must be an array");
    if (parentTier === null && node.tier !== "province") throw new TypeError("root region node must be province tier");
    if (parentTier === "province" && node.tier !== "prefecture" && node.tier !== "county" && node.tier !== "unknown") {
      throw new TypeError("province children must follow the provided tree tier");
    }
    if (parentTier === "prefecture" && node.tier !== "county" && node.tier !== "unknown") {
      throw new TypeError("prefecture children must be county tier");
    }
    if (parentTier === "county" && node.children.length > 0) throw new TypeError("county region nodes cannot have children");
    count += 1;
    for (const child of node.children) visit(child, node.tier as RegionNode["tier"]);
  };
  for (const node of nodes) visit(node, null);
  if (expectedCount !== undefined && count !== expectedCount) {
    throw new TypeError("regions.node_count does not match tree size");
  }
  return count;
}

export function validatePolicyRegionsResponse(value: unknown): PolicyRegionsResponse {
  assertRecord(value, "regions response");
  if (typeof value.region_version !== "string" || !VERSION_PATTERN.test(value.region_version)) {
    throw new TypeError("regions.region_version is invalid");
  }
  if (typeof value.artifact_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.artifact_sha256)) {
    throw new TypeError("regions.artifact_sha256 is invalid");
  }
  for (const field of ["source", "source_url", "retrieved_at_utc"] as const) {
    if (typeof value[field] !== "string") throw new TypeError(`regions.${field} must be a string`);
  }
  if (value.snapshot_as_of !== null && typeof value.snapshot_as_of !== "string") {
    throw new TypeError("regions.snapshot_as_of must be a string or null");
  }
  if (value.official_version !== null && typeof value.official_version !== "string") {
    throw new TypeError("regions.official_version must be a string or null");
  }
  assertNonNegativeInteger(value.node_count, "regions.node_count");
  validateTreeNodes(value.data, value.node_count);
  return value as unknown as PolicyRegionsResponse;
}

export function validatePolicyFacetsResponse(value: unknown): PolicyFacetsResponse {
  assertRecord(value, "facets response");
  assertRecord(value.dataset, "facets.dataset");
  const pin = extractPolicyVersionPin(value.dataset as unknown as CatalogDatasetInfo);
  if (!pin) throw new TypeError("facets.dataset must contain a complete valid version group");
  assertNonNegativeInteger(value.dataset.total_documents, "facets.dataset.total_documents");
  if (typeof value.dataset.partial !== "boolean" && value.dataset.partial !== null) {
    throw new TypeError("facets.dataset.partial must be boolean or null");
  }

  assertRecord(value.query, "facets.query");
  assertRecord(value.facets, "facets.facets");
  const facets = value.facets as Record<string, unknown>;
  const admin = facets.admin_level;
  assertRecord(admin, "facets.admin_level");
  const regionFacets = admin.regions;
  assertRecord(regionFacets, "facets.admin_level.regions");
  const year = facets.year;
  const fyp = facets.fyp;
  const plantype = facets.plantype;
  assertRecord(year, "facets.year");
  assertRecord(fyp, "facets.fyp");
  assertRecord(plantype, "facets.plantype");

  const adminOptions = admin.options;
  assertCountedOptions(adminOptions, "facets.admin_level.options");
  if (adminOptions.length !== ADMIN_LEVELS.length || ADMIN_LEVELS.some(level => !adminOptions.some(option => option.value === level))) {
    throw new TypeError("facets.admin_level.options must include all five administrative levels");
  }
  assertCountedOptions(year.options, "facets.year.options");
  if (!(year.options as Array<Record<string, unknown>>).some(option => option.value === "unknown")) {
    throw new TypeError("facets.year.options must include the unknown-year option");
  }
  for (const dimension of ["fyp", "plantype"] as const) {
    const facet = dimension === "fyp" ? fyp : plantype;
    assertCountedOptions(facet.options, `facets.${dimension}.options`);
    assertSibling(facet.unclassified, `facets.${dimension}.unclassified`);
  }
  const regions = regionFacets;
  for (const field of ["province_options", "prefecture_options", "county_options"] as const) {
    const options = regions[field];
    if (!Array.isArray(options)) throw new TypeError(`facets.admin_level.regions.${field} must be an array`);
    for (const [index, option] of options.entries()) {
      assertRecord(option, `facets.admin_level.regions.${field}[${index}]`);
      if (typeof option.code !== "string" || !CODE_PATTERN.test(option.code)
        || typeof option.name !== "string" || typeof option.source_type !== "string"
        || !["province", "prefecture", "county"].includes(String(option.tier))) {
        throw new TypeError(`facets.admin_level.regions.${field}[${index}] has invalid region fields`);
      }
      assertNonNegativeInteger(option.count, `facets.admin_level.regions.${field}[${index}].count`);
    }
  }
  assertSibling(regions.unclassified, "facets.admin_level.regions.unclassified");
  assertSibling(regions.not_applicable, "facets.admin_level.regions.not_applicable");
  return value as unknown as PolicyFacetsResponse;
}
