import type {
  PolicyContentResponse,
  PolicyDetailResponse,
  PolicyListParams,
  PolicyListResponse,
} from "../types";

const dataset = {
  generation_id: "demo-generation",
  revision: 1,
  total_documents: 1,
  partial: null,
  source: "demo_synthetic" as const,
};

const policy = {
  planid: "DEMO-001",
  title: "演示：数字经济政策示例",
  area: "北京市",
  fyp: "十四五",
  plantype_label: "示范数据",
  body: "本页面展示目录交互效果。此内容为合成演示文本，不来自真实政策库。支持创新企业发展，促进数字经济相关产业协同。",
};

export const demoCatalogClient = {
  async list(params: PolicyListParams = {}, signal?: AbortSignal): Promise<PolicyListResponse> {
    if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
    const page = params.page ?? 1;
    const pageSize = params.page_size ?? 20;
    const query = params.q?.trim() ?? "";
    const titleIndex = query ? policy.title.indexOf(query) : -1;
    const bodyIndex = query ? policy.body.indexOf(query) : -1;
    const matched = (!query || titleIndex >= 0 || bodyIndex >= 0)
      && (!params.area || params.area === policy.area)
      && (!params.fyp || params.fyp === policy.fyp || params.fyp === "第十四个五年规划")
      && (!params.plantype || params.plantype === policy.plantype_label);
    const matches: PolicyListResponse["items"][number]["matches"] = [];
    if (titleIndex >= 0) matches.push({ field: "title", offset: titleIndex, snippet: policy.title });
    if (bodyIndex >= 0) matches.push({ field: "body", offset: bodyIndex, snippet: policy.body });
    const item = {
      planid: policy.planid,
      title: policy.title,
      area: policy.area,
      fyp: policy.fyp,
      plantype_label: policy.plantype_label,
      matches,
    };
    return {
      dataset,
      query: {
        q: query || null,
        area: params.area ?? null,
        fyp: params.fyp ?? null,
        plantype: params.plantype ?? null,
        match_mode: query ? "literal" : null,
      },
      matched_documents: matched ? 1 : 0,
      page,
      page_size: pageSize,
      total_pages: matched ? 1 : 0,
      items: matched && page === 1 ? [item] : [],
    };
  },

  async detail(planid: string, signal?: AbortSignal): Promise<PolicyDetailResponse> {
    if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
    if (planid !== policy.planid) throw new Error("未找到该演示政策");
    return {
      dataset,
      policy: {
        planid: policy.planid,
        title: policy.title,
        area: policy.area,
        fyp: policy.fyp,
        plantype_label: policy.plantype_label,
        body_chars: Array.from(policy.body).length,
        sections: [{ index: 0, level: 1, path: "演示文本", start: 0, end: Array.from(policy.body).length }],
      },
    };
  },

  async content(
    planid: string,
    offset = 0,
    limit = 4000,
    signal?: AbortSignal,
  ): Promise<PolicyContentResponse> {
    if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
    if (planid !== policy.planid) throw new Error("未找到该演示政策");
    const characters = Array.from(policy.body);
    const text = characters.slice(offset, offset + limit).join("");
    return {
      dataset,
      planid,
      title: policy.title,
      offset,
      limit,
      total_chars: characters.length,
      has_more: offset + Array.from(text).length < characters.length,
      text,
    };
  },
};
