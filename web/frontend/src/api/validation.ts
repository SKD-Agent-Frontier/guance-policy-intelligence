import { ApiError } from "./http";
import type { PreparationResponse, TaskType } from "../types";

const TASK_TYPES = new Set<TaskType>(["qa", "extract", "organize", "compare", "compare_plans"]);
const PREPARATION_STATUSES = new Set(["needs_clarification", "ready", "blocked"]);
const CONDITION_STATUSES = new Set(["executable_filter", "request_only", "unrestricted", "pending"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function isConditionArray(value: unknown): boolean {
  return Array.isArray(value) && value.every((item) => isRecord(item)
    && typeof item.id === "string" && typeof item.text === "string");
}

export function validatePreparationResponse(value: unknown, expectedMode: TaskType): PreparationResponse {
  const malformed = () => new ApiError("malformed", "研究准备服务返回的内容格式无效。");
  if (!isRecord(value)
      || !PREPARATION_STATUSES.has(String(value.status))
      || !TASK_TYPES.has(value.mode as TaskType)
      || value.mode !== expectedMode
      || typeof value.message !== "string"
      || !Array.isArray(value.options)
      || !value.options.every((option) => isRecord(option)
        && typeof option.id === "string"
        && typeof option.question === "string"
        && isStringArray(option.choices))
      || !isStringArray(value.warnings)) {
    throw malformed();
  }

  const state = value.state;
  if (!isRecord(state)
      || typeof state.state_id !== "string"
      || state.state_id.length === 0
      || !Number.isInteger(state.revision)
      || (state.revision as number) < 0
      || state.mode !== expectedMode
      || typeof state.expires_at !== "string"
      || typeof state.original_request !== "string"
      || !isNullableString(state.topic)
      || !isNullableString(state.area)
      || !isStringArray(state.dimensions)
      || !isConditionArray(state.confirmed_conditions)
      || !isConditionArray(state.pending_conditions)
      || !Array.isArray(state.completed_questions)
      || !state.completed_questions.every((item) => isRecord(item)
        && typeof item.question_id === "string" && typeof item.answer === "string")
      || !Number.isInteger(state.clarification_count)
      || !Array.isArray(state.condition_statuses)
      || !state.condition_statuses.every((item) => isRecord(item)
        && typeof item.condition_id === "string"
        && CONDITION_STATUSES.has(String(item.status))
        && isNullableString(item.value)
        && typeof item.reason === "string")) {
    throw malformed();
  }

  if (state.time_scope !== null) {
    const timeScope = state.time_scope;
    if (!isRecord(timeScope)
        || !isNullableString(timeScope.start)
        || !isNullableString(timeScope.end)
        || !["exact", "year", "range", "unspecified"].includes(String(timeScope.precision))
        || !isNullableString(timeScope.timezone)) {
      throw malformed();
    }
  }

  if (value.status === "ready") {
    const request = value.request;
    if (value.options.length !== 0
        || !isRecord(request)
        || request.task_type !== expectedMode
        || typeof request.question !== "string"
        || (request.targets !== undefined && request.targets !== null && !isStringArray(request.targets))
        || (request.dimensions !== undefined && request.dimensions !== null && !isStringArray(request.dimensions))) {
      throw malformed();
    }
  } else if (value.request !== null) {
    throw malformed();
  }
  return value as unknown as PreparationResponse;
}
