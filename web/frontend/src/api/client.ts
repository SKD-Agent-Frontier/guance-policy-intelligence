import { demoClient } from "./demoClient";
import { realClient } from "./realClient";
import { isRealMode } from "./config";
import type { DemoScenario } from "../data/demo";
import type { CompareResolution, PreparationResponse, ResearchPreparationRequest, ResearchRequest, RunResult } from "../types";

export { ApiError, describeFailure } from "./realClient";
export type { ApiFailureKind } from "./realClient";
export { validatePreparationResponse } from "./realClient";
export { isRealMode, researchMode, apiBaseUrl } from "./config";
export type { DemoScenario } from "../data/demo";

export interface ResearchClient {
  resolveCompare(question: string): Promise<CompareResolution>;
  submit(request: ResearchRequest, scenario?: DemoScenario): Promise<RunResult>;
  prepare(request: ResearchPreparationRequest): Promise<PreparationResponse>;
}

/** Real 模式只走真实 HTTP；Demo 模式只走本地模拟，两者不互相回退。 */
export const researchClient: ResearchClient = {
  resolveCompare(question) {
    return isRealMode ? realClient.resolveCompare(question) : Promise.resolve({
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
