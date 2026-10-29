import { createDemoResult } from "../data/demo";
import type { DemoScenario } from "../data/demo";
import type { ResearchRequest, RunResult } from "../types";

export type { DemoScenario } from "../data/demo";

const DEMO_DELAY_MS = 900;

export interface ResearchClient {
  submit(request: ResearchRequest, scenario?: DemoScenario): Promise<RunResult>;
}

export const researchClient: ResearchClient = {
  async submit(request, scenario = "success") {
    await new Promise<void>((resolve) => setTimeout(resolve, DEMO_DELAY_MS));
    return createDemoResult(request, scenario);
  },
};
