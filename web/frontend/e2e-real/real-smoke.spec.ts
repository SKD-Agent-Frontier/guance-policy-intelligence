import { expect, test, type Page, type TestInfo, type Response } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";

test.skip(
  process.env.RUN_REAL_E2E !== "1",
  "Opt-in only: requires running PR 9 API, configured model and published BM25 catalog",
);

type RunResponse = {
  run_id: string;
  status: "completed" | "failed" | "limit_reached";
  evidence_status: "verified" | "insufficient" | "none";
  claims: unknown[];
  evidence_refs: unknown[];
  task_type: string;
  error: { code: string; message: string } | null;
};

type PrepareResponse = {
  status: "needs_clarification" | "ready" | "blocked";
  mode: string;
  options: Array<{ id: string; question: string; choices: string[] }>;
  warnings: string[];
};

function safeRunSummary(result: RunResponse) {
  return {
    run_id_present: Boolean(result.run_id),
    status: result.status,
    evidence_status: result.evidence_status,
    claim_count: result.claims?.length ?? 0,
    evidence_ref_count: result.evidence_refs?.length ?? 0,
    task_type: result.task_type,
    error_code: result.error?.code ?? null,
  };
}

async function waitForPrepare(page: Page): Promise<{ response: Response; body: PrepareResponse }> {
  const response = await page.waitForResponse(
    (candidate) => candidate.url().endsWith("/api/research/prepare") && candidate.request().method() === "POST",
    { timeout: 45_000 },
  );
  expect(response.status(), "prepare HTTP status").toBe(200);
  return { response, body: (await response.json()) as PrepareResponse };
}

async function sendInitial(page: Page, message: string): Promise<PrepareResponse> {
  const pending = waitForPrepare(page);
  await page.getByRole("textbox", { name: "研究需求" }).fill(message);
  await page.getByRole("button", { name: "发送消息" }).click();
  return (await pending).body;
}

function preferredClarificationChoice(question: string, choices: string[]) {
  const questionText = `${question} ${choices.join(" ")}`;
  const preferred = questionText.includes("时间")
    ? /十四五|不限时间|不限制|继续/i
    : questionText.includes("地区") || questionText.includes("范围")
      ? /苏州市|苏州|指定地区/i
      : questionText.includes("分类") || questionText.includes("维度")
        ? /支持对象|政策工具|工具/i
        : /人才|养老|支持政策|其他/i;
  return choices.find((choice) => preferred.test(choice)) ?? choices[0];
}

async function continueFromBackendClarifications(page: Page, first: PrepareResponse, maximum = 4) {
  const statuses = [first.status];
  let response = first;
  for (let round = 0; round < maximum && response.status === "needs_clarification"; round += 1) {
    const firstOption = response.options[0];
    const choices = firstOption?.choices ?? [];
    const choice = preferredClarificationChoice(firstOption?.question ?? "", choices);
    const pending = waitForPrepare(page);
    if (choice) {
      await page.getByRole("button", { name: choice, exact: true }).last().click();
    } else {
      const answer = response.mode === "qa"
        ? "我关注养老服务政策中的人才培养要求。"
        : "限定苏州市十四五时期养老服务相关政策，按支持对象和政策工具分类；其他范围不限。";
      await page.getByRole("textbox", { name: "研究需求" }).fill(answer);
      await page.getByRole("button", { name: "发送消息" }).click();
    }
    response = (await pending).body;
    statuses.push(response.status);
  }
  expect(response.status, "preparation should become ready or explicitly blocked").not.toBe("needs_clarification");
  return { response, statuses };
}

async function runRealResearch(page: Page, info: TestInfo, mode: "qa" | "organize", message: string) {
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await expect(page.locator(".global-env-badge")).toHaveText("Real");
  if (mode === "organize") await page.getByRole("tab", { name: "文献梳理" }).click();
  const runResponses: Response[] = [];
  const onResponse = (response: Response) => {
    if (response.url().endsWith("/api/runs") && response.request().method() === "POST") runResponses.push(response);
  };
  page.on("response", onResponse);
  const initial = await sendInitial(page, message);
  expect(initial.mode).toBe(mode);
  const { response: prepared, statuses } = await continueFromBackendClarifications(page, initial);
  await mkdir(info.outputDir, { recursive: true });
  if (prepared.status === "blocked") {
    await expect(page.locator(".prep-blocked")).toBeVisible();
    await writeFile(info.outputPath(`${mode}-summary.json`), JSON.stringify({ prepare_statuses: statuses, ready: false, blocked: true }, null, 2));
    return;
  }

  expect(prepared.status).toBe("ready");
  await expect.poll(() => runResponses.length, { timeout: 220_000 }).toBe(1);
  const [runResponse] = runResponses;
  page.off("response", onResponse);
  expect(runResponse.status(), "run HTTP status").toBe(200);
  const result = (await runResponse.json()) as RunResponse;
  expect(result.task_type).toBe(mode);
  await expect(page.getByText("正式研究结果").last()).toBeVisible({ timeout: 20_000 });
  const resultMessage = page.locator(".result-message").last();
  await expect(resultMessage.locator(".result-status-row")).toBeVisible();
  expect(await page.locator(".global-env-badge").innerText()).toBe("Real");
  await page.screenshot({ path: info.outputPath(`${mode}-result.png`), fullPage: true });
  const runSummary = {
    prepare_statuses: statuses,
    ready: true,
    prepare_warnings_count: prepared.warnings?.length ?? 0,
    http_status: runResponse.status(),
    ...safeRunSummary(result),
    research_business_success: result.status === "completed" && result.error === null,
    citation_verified: result.evidence_status === "verified",
    result_visible: true,
  };
  await writeFile(info.outputPath(`${mode}-summary.json`), JSON.stringify(runSummary, null, 2));

  let citationOpened = false;
  if (result.evidence_refs.length > 0 && await resultMessage.locator(".result-citation").count()) {
    await resultMessage.locator(".result-citation").first().click();
    await expect(resultMessage.locator('.message-evidence-panel [aria-label="证据详情"]')).toBeVisible();
    citationOpened = true;
    await page.screenshot({ path: info.outputPath(`${mode}-evidence.png`), fullPage: true });
  }
  runSummary.citation_opened = citationOpened;
  await writeFile(info.outputPath(`${mode}-summary.json`), JSON.stringify(runSummary, null, 2));
  info.annotations.push({ type: "real-result", description: JSON.stringify(safeRunSummary(result)) });
}

test("Real QA: prepare and show the actual answer and evidence state", async ({ page }, info) => {
  await runRealResearch(
    page,
    info,
    "qa",
    process.env.REAL_QA_QUESTION ?? "苏州市养老服务十四五规划中人才培养有哪些要求？",
  );
});

test("Real Organize: prepare and show the actual research result and evidence state", async ({ page }, info) => {
  await runRealResearch(
    page,
    info,
    "organize",
    process.env.REAL_ORGANIZE_QUESTION ?? "梳理苏州市十四五养老服务相关政策，按支持对象和政策工具分类。",
  );
});

test("Real policy catalog: literal search, metadata detail and paginated original", async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await page.getByRole("button", { name: "文献", exact: true }).click();
  await expect(page.getByRole("heading", { name: "政策文献目录" })).toBeVisible();
  const responsePromise = page.waitForResponse(
    (candidate) => candidate.url().includes("/api/policies?") && candidate.request().method() === "GET",
    { timeout: 30_000 },
  );
  await page.getByLabel("关键词字面检索").fill("数字经济");
  await page.getByRole("button", { name: "检索目录" }).click();
  const listResponse = await responsePromise;
  expect(listResponse.status()).toBe(200);
  await expect(page.getByText(/字面匹配结果不等于全部语义相关政策/)).toBeVisible();
  await expect(page.getByText(/字面匹配 \d+ 篇/)).toBeVisible();
  const policyCard = page.locator(".catalog-policy-card").first();
  await expect(policyCard).toBeVisible();
  const detailPromise = page.waitForResponse(
    (candidate) => /\/api\/policies\/[^/?]+$/.test(candidate.url()) && candidate.request().method() === "GET",
    { timeout: 30_000 },
  );
  await policyCard.click();
  const detailResponse = await detailPromise;
  expect(detailResponse.status()).toBe(200);
  await expect(page.getByRole("heading", { name: /.+/ }).filter({ hasText: /政策|规划|办法|意见|方案/ }).last()).toBeVisible();
  await expect(page.getByRole("region", { name: "政策原文" })).toBeVisible();
  await expect(page.locator(".catalog-original-text")).toBeVisible();
  await mkdir(info.outputDir, { recursive: true });
  await page.screenshot({ path: info.outputPath("catalog-policy-detail.png"), fullPage: true });

  const nextPage = page.getByRole("button", { name: /下一页/ }).last();
  let bodyPageRequests = 1;
  if (await nextPage.isEnabled()) {
    const bodyPromise = page.waitForResponse(
      (candidate) => candidate.url().includes("/content?") && candidate.request().method() === "GET",
      { timeout: 30_000 },
    );
    await nextPage.click();
    const bodyResponse = await bodyPromise;
    expect(bodyResponse.status()).toBe(200);
    bodyPageRequests += 1;
  }
  await writeFile(info.outputPath("catalog-summary.json"), JSON.stringify({
    list_http_status: listResponse.status(),
    detail_http_status: detailResponse.status(),
    content_page_requests: bodyPageRequests,
    original_visible: true,
  }, null, 2));
});

test("Current main preparation API: clarify a single-topic QA request without starting a run", async ({ page }, info) => {
  await page.goto("/");
  const runRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/runs") && request.method() === "POST") runRequests.push(request.url());
  });
  const baseUrl = process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8013";
  const initial = await page.evaluate(async ({ apiBaseUrl }) => {
    const response = await fetch(`${apiBaseUrl}/api/research/prepare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "qa", message: "数字经济" }),
    });
    return { httpStatus: response.status, body: await response.json() };
  }, { apiBaseUrl: baseUrl }) as { httpStatus: number; body: PrepareResponse & { state: { state_id: string; revision: number }; request: unknown | null } };
  expect(initial.httpStatus).toBe(200);
  expect(initial.body.status).toBe("needs_clarification");
  expect(initial.body.options.length).toBeGreaterThan(0);

  const continuation = await page.evaluate(async ({ apiBaseUrl, state }) => {
    const response = await fetch(`${apiBaseUrl}/api/research/prepare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "qa", message: "我想了解产业支持政策。", state }),
    });
    return { httpStatus: response.status, body: await response.json() };
  }, {
    apiBaseUrl: baseUrl,
    state: { state_id: initial.body.state.state_id, revision: initial.body.state.revision },
  }) as { httpStatus: number; body: PrepareResponse & { state: { revision: number }; request: unknown | null } };
  expect(continuation.httpStatus).toBe(200);
  expect(continuation.body.state.revision).toBeGreaterThan(initial.body.state.revision);
  expect(["needs_clarification", "ready"]).toContain(continuation.body.status);
  expect(runRequests).toHaveLength(0);
  await writeFile(info.outputPath("main-qa-clarification-summary.json"), JSON.stringify({
    prepare_http_statuses: [initial.httpStatus, continuation.httpStatus],
    prepare_statuses: [initial.body.status, continuation.body.status],
    initial_question_count: initial.body.options.length,
    revision_advanced: continuation.body.state.revision > initial.body.state.revision,
    run_requests: runRequests.length,
  }, null, 2));
});

test("Current main preparation API: continue dynamic organize questions to ready without running research", async ({ page }, info) => {
  await page.goto("/");
  const runRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/runs") && request.method() === "POST") runRequests.push(request.url());
  });
  const baseUrl = process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8013";
  let current = await page.evaluate(async ({ apiBaseUrl }) => {
    const response = await fetch(`${apiBaseUrl}/api/research/prepare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "organize", message: "梳理支持数字经济的政策" }),
    });
    return { httpStatus: response.status, body: await response.json() };
  }, { apiBaseUrl: baseUrl }) as { httpStatus: number; body: PrepareResponse & { state: { state_id: string; revision: number }; request: unknown | null } };
  expect(current.httpStatus).toBe(200);
  expect(current.body.status).toBe("needs_clarification");
  const statuses = [current.body.status];
  const revisions = [current.body.state.revision];
  for (let turn = 0; turn < 6 && current.body.status === "needs_clarification"; turn += 1) {
    const answer = "限定苏州市十四五养老服务相关政策，按支持对象和政策工具分类；其他范围不限。";
    current = await page.evaluate(async ({ apiBaseUrl, state, message }) => {
      const response = await fetch(`${apiBaseUrl}/api/research/prepare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "organize", message, state }),
      });
      return { httpStatus: response.status, body: await response.json() };
    }, {
      apiBaseUrl: baseUrl,
      state: { state_id: current.body.state.state_id, revision: current.body.state.revision },
      message: answer,
    }) as typeof current;
    expect(current.httpStatus).toBe(200);
    statuses.push(current.body.status);
    revisions.push(current.body.state.revision);
  }
  expect(current.body.status).toBe("ready");
  expect(current.body.request).not.toBeNull();
  expect(revisions.every((revision, index) => index === 0 || revision > revisions[index - 1])).toBe(true);
  expect(runRequests).toHaveLength(0);
  await writeFile(info.outputPath("main-organize-ready-summary.json"), JSON.stringify({
    prepare_http_status: current.httpStatus,
    prepare_statuses: statuses,
    revisions,
    request_ready: current.body.request !== null,
    warnings_count: current.body.warnings.length,
    run_requests: runRequests.length,
  }, null, 2));
});
