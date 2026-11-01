import { demoClient } from "./demoClient";
import { realClient } from "./realClient";
import { isRealMode } from "./config";
import type { DemoScenario } from "../data/demo";
import type { CompareResolution, ResearchResolution, TaskType, PreparationResponse, ResearchPreparationRequest, ResearchRequest, RunResult } from "../types";

export { ApiError, describeFailure } from "./realClient";
export type { ApiFailureKind } from "./realClient";
export { validatePreparationResponse } from "./realClient";
export { isRealMode, researchMode, apiBaseUrl } from "./config";
export type { DemoScenario } from "../data/demo";

export interface ResearchClient {
  resolveCompare(question: string): Promise<CompareResolution>;
  resolveResearch(mode: TaskType, question: string): Promise<ResearchResolution>;
  submit(request: ResearchRequest, scenario?: DemoScenario): Promise<RunResult>;
  prepare(request: ResearchPreparationRequest): Promise<PreparationResponse>;
}

/** Real 模式只走真实 HTTP；Demo 模式只走本地模拟，两者不互相回退。 */
export const researchClient: ResearchClient = {
  resolveResearch(mode, question) {
    if (isRealMode) return realClient.resolveResearch(mode, question);
    // Demo never claims these are real policies or consults a published catalog.
    const count = mode === "extract" ? 1 : 2;
    return Promise.resolve<ResearchResolution>({
      status: "ready",
      message: "演示模式：使用合成文献执行，不代表真实政策定位。",
      request: {
        task_type: mode, question,
        targets: Array.from({ length: count }, (_, index) => `DEMO-${index + 1}`),
      },
      policies: [],
    });
  },
  resolveCompare(question) {
    return isRealMode ? realClient.resolveCompare(question) : Promise.resolve<CompareResolution>({
      status: "needs_clarification", message: "演示模式不连接真实政策库，不能自动定位政策文献。请切换到真实模式进行比较。",
      targets: [], policies: [], dimensions: [],
    });
  },
  submit(request, scenario = "success") {
    return isRealMode ? realClient.submit(request) : demoClient.submit(request, scenario);
  },
  prepare(request) {
    return isRealMode ? realClient.prepare(request) : demoClient.prepare(request);
  },
};
