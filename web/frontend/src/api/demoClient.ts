import { createDemoResult } from "../data/demo";
import type { DemoScenario } from "../data/demo";
import { ApiError } from "./http";
import type {
  ConditionExecutionStatus,
  PreparationResponse,
  PreparationState,
  ResearchPreparationRequest,
  ResearchRequest,
  RunResult,
  TaskType,
} from "../types";

const DEMO_DELAY_MS = 900;
const DEMO_PREPARE_DELAY_MS = 150;
let nextStateId = 1;

interface DemoPreparationDraft {
  mode: TaskType;
  revision: number;
  originalRequest: string;
  messages: string[];
}

const preparationDrafts = new Map<string, DemoPreparationDraft>();

const NO_OPTIONS: PreparationResponse["options"] = [];

function isScopeMessage(message: string): boolean {
  return /(?:省|市|区|县|全国范围|不限(?:时间|年份|日期|地区)|不限定(?:时间|年份|日期|地区)|\d{4}|(?:近|过去|最近)\s*(?:\d+|[零〇一二两三四五六七八九十百千]+)\s*(?:年|个月|月)|(?:十一|十二|十三|十四|十五)五|(?:按|按照)[^，。；]+(?:分类|梳理|整理|分析|汇总))/.test(message);
}

function demoTopic(draft: DemoPreparationDraft): string | null {
  const original = draft.originalRequest.trim().replace(/^(?:请)?(?:帮我)?(?:梳理|整理|研究|分析)\s*/, "");
  if (!/^(?:政策|政策梳理|相关政策)?[。！!？?\s]*$/.test(original)) return original;
  return draft.messages.find((message) => message.trim() && !isScopeMessage(message))?.trim() ?? null;
}

function demoPreparationState(
  stateId: string,
  draft: DemoPreparationDraft,
  options: PreparationResponse["options"],
): PreparationState {
  const conversation = [draft.originalRequest, ...draft.messages].join(" ");
  const area = conversation.match(/[\u4e00-\u9fff]{2,8}(?:省|市|区|县|自治州|盟|旗)/)?.[0] ?? null;
  const period = conversation.match(/(?:十一|十二|十三|十四|十五)五(?:年规划)?/g)?.at(-1) ?? null;
  const calendarYear = conversation.match(/\d{4}(?:年|\s*[—–-至到]\s*\d{4}年?)/)?.[0] ?? null;
  const relativeTime = conversation.match(/(?:近|过去|最近)\s*(?:\d+|[零〇一二两三四五六七八九十百千]+)\s*(?:年|个月|月)/)?.[0] ?? null;
  const dimensionText = conversation.match(/(?:按|按照)([^，。；]+?)(?:分类|梳理|整理|分析|汇总|$)/)?.[1];
  const dimensions = [...new Set(dimensionText?.split(/[、，和及与]/).map((item) => item.trim()).filter(Boolean) ?? [])];
  const statuses: ConditionExecutionStatus[] = [];
  if (area) {
    statuses.push({
      condition_id: "area",
      status: "executable_filter",
      value: area,
      reason: "演示状态：范围条件仅用于展示，不代表已执行过滤。",
    });
  }
  if (period || calendarYear || relativeTime) {
    const requestOnlyTime = Boolean(calendarYear || relativeTime);
    statuses.push({
      condition_id: "time_scope",
      status: requestOnlyTime ? "request_only" : "executable_filter",
      value: period ?? calendarYear ?? relativeTime,
      reason: requestOnlyTime
        ? "演示状态：时间条件仅作为请求文本传递，不保证按发布日期精确过滤。"
        : "演示状态：时间条件仅用于展示，不代表已执行过滤。",
    });
  }

  return {
    state_id: stateId,
    revision: draft.revision,
    expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    mode: draft.mode,
    original_request: draft.originalRequest,
    topic: demoTopic(draft),
    time_scope: null,
    area,
    dimensions,
    confirmed_conditions: statuses.filter((item) => item.status !== "pending").map((item) => ({ id: item.condition_id, text: item.value ?? item.reason })),
    pending_conditions: options.map((option) => ({ id: option.id, text: option.question })),
    completed_questions: draft.messages.map((answer, index) => ({ question_id: `demo-${index + 1}`, answer })),
    clarification_count: draft.messages.length,
    condition_statuses: statuses,
  };
}

function buildDemoPreparation(
  request: ResearchPreparationRequest,
  stateId: string,
  draft: DemoPreparationDraft,
): PreparationResponse {
  const unsupported = !["qa", "organize"].includes(request.mode);
  let options: PreparationResponse["options"] = NO_OPTIONS;
  if (request.mode === "qa" && draft.messages.length === 0 && !/[?？]|什么|如何|为什么|怎样|哪些|请问|查询/.test(request.message)) {
    options = [{
      id: "aspect",
      question: "你想了解这个主题的哪个方面？",
      choices: ["概念与发展", "产业政策", "发展现状", "其他"],
    }];
  } else if (request.mode === "organize") {
    const conversation = [draft.originalRequest, ...draft.messages].join(" ");
    const baseTopic = draft.originalRequest.trim().replace(/^(?:请)?(?:帮我)?(?:梳理|整理|研究|分析)\s*/, "");
    const hasTopic = Boolean(demoTopic(draft)) || !/^(?:政策|政策梳理|相关政策)?[。！!？?\s]*$/.test(baseTopic);
    const hasArea = /(?:省|市|区|县|自治州|盟|旗|全国范围)/.test(conversation);
    const hasTime = /\d{4}|(?:近|过去|最近)\s*(?:\d+|[零〇一二两三四五六七八九十百千]+)\s*(?:年|个月|月)|(?:十一|十二|十三|十四|十五)五|不限(?:时间|年份|日期)|不限定(?:时间|年份|日期)/.test(conversation);
    const hasDimensions = /(?:按|按照)[^，。；]+(?:分类|梳理|整理|分析|汇总)/.test(conversation);
    const hasScope = hasArea || hasTime || hasDimensions;
    if (!hasTopic) {
      options = [{ id: "topic", question: "请说明要整理的政策主题。", choices: [] }];
    } else if (!hasScope) {
      options = [{ id: "scope", question: "希望覆盖哪些地区和时间，并按哪些维度整理？", choices: [] }];
    }
  }

  const status = unsupported ? "blocked" : options.length ? "needs_clarification" : "ready";
  const warnings = /\d{4}|(?:近|过去|最近)\s*(?:\d+|[零〇一二两三四五六七八九十百千]+)\s*(?:年|个月|月)/.test([draft.originalRequest, ...draft.messages].join(" "))
    ? ["不保证按发布日期精确过滤"]
    : [];
  const response: PreparationResponse = {
    status,
    mode: request.mode,
    message: unsupported
      ? `${request.mode} 当前不能通过演示准备接口执行。`
      : options[0]?.question ?? "演示研究请求已准备完成。",
    options,
    state: demoPreparationState(stateId, draft, options),
    request: status === "ready"
      ? {
          task_type: request.mode,
          question: [draft.originalRequest, ...draft.messages].join("；补充："),
          dimensions: demoPreparationState(stateId, draft, options).dimensions.length
            ? demoPreparationState(stateId, draft, options).dimensions
            : undefined,
        }
      : null,
    warnings,
  };
  return response;
}

export const demoClient = {
  async submit(request: ResearchRequest, scenario: DemoScenario = "success"): Promise<RunResult> {
    await new Promise<void>((resolve) => setTimeout(resolve, DEMO_DELAY_MS));
    return createDemoResult(request, scenario);
  },

  async prepare(request: ResearchPreparationRequest): Promise<PreparationResponse> {
    await new Promise<void>((resolve) => setTimeout(resolve, DEMO_PREPARE_DELAY_MS));
    let stateId: string;
    let draft: DemoPreparationDraft;
    if (request.state) {
      stateId = request.state.state_id;
      const existing = preparationDrafts.get(stateId);
      if (!existing || existing.mode !== request.mode || existing.revision !== request.state.revision) {
        throw new ApiError("http", "研究准备状态已失效或冲突", 409);
      }
      draft = { ...existing, revision: existing.revision + 1, messages: [...existing.messages, request.message] };
    } else {
      stateId = `demo-preparation-${nextStateId++}`;
      draft = { mode: request.mode, revision: 0, originalRequest: request.message, messages: [] };
    }
    preparationDrafts.set(stateId, draft);
    return buildDemoPreparation(request, stateId, draft);
  },
};
