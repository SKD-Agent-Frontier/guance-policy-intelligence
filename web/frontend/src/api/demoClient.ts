import { createDemoResult } from "../data/demo";
import type { DemoScenario } from "../data/demo";
import type { ResearchRequest, RunResult } from "../types";

const DEMO_DELAY_MS = 900;

export const demoClient = {
  async submit(request: ResearchRequest, scenario: DemoScenario = "success"): Promise<RunResult> {
    await new Promise<void>((resolve) => setTimeout(resolve, DEMO_DELAY_MS));
    return createDemoResult(request, scenario);
  },
};