export type ResearchMode = "real" | "demo";

const rawMode = (import.meta.env.VITE_RESEARCH_MODE ?? "real").toLowerCase();

if (rawMode !== "real" && rawMode !== "demo") {
  throw new Error("VITE_RESEARCH_MODE must be real or demo, got: " + rawMode);
}

export const researchMode: ResearchMode = rawMode;
export const isRealMode = researchMode === "real";

export const apiBaseUrl: string = (
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

/** Agent 单次执行上限 180 秒；前端超时必须留出网络与序列化余量。 */
export const REAL_REQUEST_TIMEOUT_MS = 210000;