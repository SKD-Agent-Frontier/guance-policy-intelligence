import { expect, test, type Page, type TestInfo } from "@playwright/test";

test.skip(
  process.env.RUN_REAL_E2E !== "1",
  "Opt-in only: requires running local FastAPI, school model and BM25",
);

type RunResponse = {
  run_id: string;
  status: "completed" | "failed" | "limit_reached";
  evidence_status: "verified" | "insufficient" | "none";
  claims: unknown[];
  evidence_refs: unknown[];
  task_type: string;
};

async function submitInBrowser(
  page: Page,
  info: TestInfo,
  taskType: "qa" | "compare",
): Promise<RunResponse> {
  const awaited = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/runs") &&
      response.request().method() === "POST",
    { timeout: 220_000 },
  );
  await page.getByRole("button", { name: "开始研究" }).click();
  await expect(page.locator(".loading-panel")).toBeVisible();
  const response = await awaited;
  expect(response.status(), "HTTP gateway must succeed").toBe(200);
  const data = (await response.json()) as RunResponse;
  expect(data.run_id).toBeTruthy();
  expect(data.task_type).toBe(taskType);
  expect(["completed", "failed", "limit_reached"]).toContain(data.status);
  await expect(page.locator(".loading-panel")).toBeHidden({ timeout: 15_000 });
  await expect(page.locator(".demo-badge")).toHaveCount(0);
  await page.screenshot({ path: info.outputPath(`${taskType}-result.png`), fullPage: true });

  // Absence of validated claims is a business outcome, not a browser failure.
  if ((data.evidence_refs?.length ?? 0) > 0) {
    // Scope to results: history buttons may contain the word "原文" in their title.
    const citation = page.locator(".workspace-content .result-citation").first();
    if (await citation.count()) {
      await citation.click();
      await expect(page.getByRole("complementary", { name: "引用证据" })).toContainText(
        "证据详情",
      );
      await page.screenshot({
        path: info.outputPath(`${taskType}-evidence.png`),
        fullPage: true,
      });
    } else {
      info.annotations.push({
        type: "business-warning",
        description: "Evidence references were returned but citation control was not visible",
      });
      throw new Error("Evidence references returned without an operable citation control");
    }
  } else {
    info.annotations.push({
      type: "business-warning",
      description: "No evidence_refs in this actual Agent response; citation not testable",
    });
  }
  return data;
}

test("Real QA: browser request, result and evidence", async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await page.screenshot({ path: info.outputPath("home.png"), fullPage: true });
  await page.getByLabel("研究问题").fill(
    process.env.REAL_QA_QUESTION ?? "20699号政策中养老服务人才培养有哪些要求？",
  );
  await submitInBrowser(page, info, "qa");
});

test("Real Compare: two policy targets, matrix and evidence", async ({ page }, info) => {
  const first = process.env.REAL_COMPARE_A ?? "37982";
  const second = process.env.REAL_COMPARE_B ?? "38366";
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await page.getByRole("tab", { name: "政策比较" }).click();
  await page.getByLabel("研究对象编号 1").fill(first);
  await page.getByLabel("研究对象编号 2").fill(second);
  const data = await submitInBrowser(page, info, "compare");
  if (data.status === "completed") {
    await expect(page.locator(".results-content, .results-fallback").first()).toBeVisible();
  }
});
