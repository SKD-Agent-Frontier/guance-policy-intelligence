import { demoClient } from "./demoClient";
import { realClient } from "./realClient";
import { isRealMode } from "./config";
import type { DemoScenario } from "../data/demo";
import type { ResearchRequest, RunResult } from "../types";

export { ApiError, describeFailure } from "./realClient";
export type { ApiFailureKind } from "./realClient";
export { isRealMode, researchMode, apiBaseUrl } from "./config";
export type { DemoScenario } from "../data/demo";

export interface ResearchClient {
  submit(request: ResearchRequest, scenario?: DemoScenario): Promise<RunResult>;
}

/** Real 模式只走真实 HTTP；Demo 模式只走本地模拟，两者不互相回退。 */
export const researchClient: ResearchClient = {
  submit(request, scenario = "success") {
    return isRealMode ? realClient.submit(request) : demoClient.submit(request, scenario);
  },
};