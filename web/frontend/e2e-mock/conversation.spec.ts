import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const ARTIFACT_DIRECTORY = process.env.PLAYWRIGHT_ARTIFACT_DIR;

async function artifacts(info: TestInfo) {
  const directory = ARTIFACT_DIRECTORY ?? info.outputDir;
  await mkdir(directory, { recursive: true });
  return directory;
}

function state(mode: string, revision = 0, topic: string | null = "数字经济") {
  return {
    state_id: "mock-state",
    revision,
    expires_at: "2027-01-01T00:00:00Z",
    mode,
    original_request: "数字经济政策研究",
    topic,
    time_scope: null,
    area: null,
    dimensions: [],
    confirmed_conditions: revision > 0 ? [{ id: "topic", text: "数字经济" }] : [],
    pending_conditions: [],
    completed_questions: [],
    clarification_count: revision,
    condition_statuses: [],
  };
}

function prep(mode: string, status: "needs_clarification" | "ready" | "blocked", revision = 0, question = "", choices: string[] = [], warnings: string[] = []) {
  const msg = question || (status === "ready" ? "研究范围已准备完成。" : "当前模式暂时无法自动准备。 ");
  return {
    status,
    mode,
    message: msg,
    options: status === "needs_clarification" ? [{ id: `q-${revision}`, question: msg, choices }] : [],
    state: state(mode, revision),
    request: status === "ready" ? {
      task_type: mode,
      question: "正式研究问题：数字经济政策及北京市、十四五时期的支持措施",
      targets: null,
      dimensions: ["支持措施"],
    } : null,
    warnings,
  };
}

function runResult(id: string, taskType = "qa") {
  return {
    run_id: id,
    session_id: `session-${id}`,
    status: "completed",
    evidence_status: "verified",
    answer: `Mock API 研究结果 ${id}：该内容仅用于前端联调。`,
    claims: [{
      text: "Mock API 支持措施结论。",
      chunk_ids: [`chunk-${id}`],
      quotes: [`Mock API 原文引句 ${id}`],
      dimension: "支持措施",
      anchors: [],
    }],
    evidence_refs: [{
      planid: "POLICY-MOCK-1",
      chunk_id: `chunk-${id}`,
      parent_id: null,
      title: `Mock API 政策文献 ${id}`,
      area: "北京市",
      fyp: "十四五",
      admin_level: "市级",
      plantype_label: "实施意见",
      section_idx: 0,
      section_path: "第一章",
      char_start: 0,
      char_end: 12,
      chunk_char_start: 0,
      chunk_char_end: 12,
      truncated: false,
      source: "kb_read",
      observed_ranges: [[0, 12]],
      complete: true,
      out_of_section: false,
      document_id: null,
      document_version_id: null,
      generation_id: null,
      source_hash: null,
      section_id: null,
      section_char_start: null,
      section_char_end: null,
      offset_basis: null,
      offset_unit: null,
      body_origin: null,
      contract_version: null,
      quality_warnings: [],
    }],
    dropped_chunk_ids: [],
    error: null,
    meta: {},
    warnings: ["Mock API：此结果不是模型或知识库执行结果。"],
    task_type: taskType,
    structured: null,
    route: null,
  };
}

async function installRunRoute(page: Page, onRun?: (body: Record<string, unknown>) => void) {
  let runCount = 0;
  await page.route("**/api/runs", async (route) => {
    runCount += 1;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    onRun?.(body);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(runResult(`mock-${runCount}`, String(body.task_type ?? "qa"))) });
  });
  return () => runCount;
}

async function openSidebar(page: Page) {
  const sidebar = page.locator(".app-sidebar");
  const closed = await sidebar.evaluate((element) => element.classList.contains("is-collapsed") || !element.classList.contains("is-mobile-open") && window.matchMedia("(max-width: 768px)").matches);
  if (closed) await page.getByRole("button", { name: "打开侧栏" }).click();
  await expect(page.getByRole("button", { name: "新建研究" })).toBeVisible();
}

async function newConversation(page: Page) {
  await openSidebar(page);
  await page.getByRole("button", { name: "新建研究" }).click();
}

test("Real client Mock API: QA clarification resumes the token, runs once, and opens the matching evidence", async ({ page }, info) => {
  const directory = await artifacts(info);
  const prepareRequests: Record<string, unknown>[] = [];
  let prepareStep = 0;
  await page.route("**/api/research/prepare", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    prepareRequests.push(body);
    prepareStep += 1;
    if (prepareStep === 1) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(prep("qa", "needs_clarification", 0, "你想了解这个主题的哪个方面？", ["产业政策", "发展现状"])) });
    } else {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(prep("qa", "ready", 1, "研究范围已准备完成。", [], ["Mock API 提醒：时间条件仅作为请求条件。"])) });
    }
  });
  const runCount = await installRunRoute(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByText("Real")).toBeVisible();
  await expect(page.getByRole("tablist", { name: "研究模式" }).getByRole("tab")).toHaveCount(5);
  await expect.poll(() => page.locator(".architecture-scene img").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1000);
  await page.screenshot({ path: join(directory, "home-desktop.png"), fullPage: true });

  const input = page.getByRole("textbox", { name: "研究需求" });
  await input.fill("数字经济");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("heading", { name: "你想了解这个主题的哪个方面？" })).toBeVisible();
  await page.screenshot({ path: join(directory, "conversation-clarification.png"), fullPage: true });
  await page.getByRole("button", { name: "产业政策" }).click();
  await expect(page.getByText(/时间条件仅作为请求条件/)).toBeVisible();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-1");
  expect(prepareRequests).toHaveLength(2);
  expect(prepareRequests[0]).toMatchObject({ mode: "qa", message: "数字经济" });
  expect(prepareRequests[1]).toMatchObject({ state: { state_id: "mock-state", revision: 0 } });
  expect(runCount()).toBe(1);
  await page.screenshot({ path: join(directory, "conversation-multiturn.png"), fullPage: true });
  await page.getByRole("button", { name: /原文/ }).first().click();
  await expect(page.locator(".message-evidence-panel")).toContainText("Mock API 政策文献");
  await expect(page.locator(".message-evidence-panel")).toContainText("Mock API 原文引句");
  expect(errors).toEqual([]);
});

test("Real client Mock API: dynamic Organize questions preserve request_only warnings and run the prepared request", async ({ page }) => {
  const prepareRequests: Record<string, unknown>[] = [];
  const runBodies: Record<string, unknown>[] = [];
  await page.route("**/api/research/prepare", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    prepareRequests.push(body);
    const stateToken = body.state as { revision?: number } | undefined;
    const response = !stateToken
      ? prep("organize", "needs_clarification", 0, "后端问题：先确定按哪个范围整理？", ["先按时间范围", "先按地区范围"])
      : stateToken.revision === 0
        ? prep("organize", "needs_clarification", 1, "后端问题：请确认时间范围和地区。", ["十四五时期，北京市"])
        : prep("organize", "ready", 2, "范围确认完成。", [], ["地区条件仅作为研究请求说明，未保证精确过滤。"]);
    if (response.status === "ready") response.state.condition_statuses = [
      { condition_id: "area", status: "request_only", value: "北京市", reason: "地区仅作为请求条件，不保证精确过滤。" },
      { condition_id: "period", status: "executable_filter", value: "十四五时期", reason: "可用于执行筛选。" },
    ];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) });
  });
  const runCount = await installRunRoute(page, (body) => runBodies.push(body));
  await page.goto("/");
  await page.getByRole("tab", { name: "文献梳理" }).click();
  await page.getByRole("textbox", { name: "研究需求" }).fill("梳理支持数字经济的政策");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("heading", { name: "后端问题：先确定按哪个范围整理？" })).toBeVisible();
  expect(runCount()).toBe(0);
  await page.getByRole("button", { name: "先按时间范围" }).click();
  await expect(page.getByRole("heading", { name: "后端问题：请确认时间范围和地区。" })).toBeVisible();
  await page.getByRole("button", { name: "十四五时期，北京市" }).click();
  await expect(page.getByText(/地区条件仅作为研究请求说明/)).toBeVisible();
  await expect(page.locator(".result-message")).toBeVisible();
  expect(prepareRequests).toHaveLength(3);
  expect(prepareRequests[1]).toMatchObject({ message: "先按时间范围", state: { revision: 0 } });
  expect(prepareRequests[2]).toMatchObject({ message: "十四五时期，北京市", state: { revision: 1 } });
  expect(runCount()).toBe(1);
  expect(runBodies[0]).toEqual({ task_type: "organize", question: "正式研究问题：数字经济政策及北京市、十四五时期的支持措施", targets: null, dimensions: ["支持措施"] });
  await page.locator(".preparation-details").last().locator("summary").click();
  await expect(page.locator(".preparation-details").last()).toContainText("北京市 · 地区仅作为请求条件，不保证精确过滤。");
  await expect(page.locator(".preparation-details").last()).toContainText(/不代表已完成筛选/);
});

test("Real client Mock API: advanced compare keeps its draft through catalog and sessions, then runs only valid targets", async ({ page }) => {
  const prepareRequests: Record<string, unknown>[] = [];
  const runBodies: Record<string, unknown>[] = [];
  await page.route("**/api/research/prepare", async (route) => {
    prepareRequests.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Advanced handoff must not prepare." }) });
  });
  const runCount = await installRunRoute(page, (body) => runBodies.push(body));
  await page.route("**/api/policies**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/content")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ planid: "POLICY-A", offset: 0, limit: 4000, total_chars: 0, has_more: false, text: "" }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ dataset: { generation_id: "mock-generation", revision: 1, total_documents: 0, partial: false, source: "current_published_frozen_release" }, query: { q: null, area: null, fyp: null, plantype: null, match_mode: null }, matched_documents: 0, page: 1, page_size: 20, total_pages: 1, items: [] }) });
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "政策比较" }).click();
  const question = "比较下北京市2015和2016年的政府工作报告";
  await page.getByRole("textbox", { name: "研究需求" }).fill(question);
  await page.getByRole("button", { name: "发送消息" }).click();

  await expect(page.getByRole("main", { name: "研究工作区" })).toBeVisible();
  await expect(page.getByRole("button", { name: "指定政策对象" })).toBeVisible();
  await expect(page.getByRole("button", { name: "收起高级选择" })).toHaveAttribute("aria-expanded", "true");
  const advancedQuestion = page.getByRole("textbox", { name: "研究问题" });
  await expect(advancedQuestion).toHaveValue(question);
  await expect(page.locator(".message-error")).toHaveCount(0);
  await expect(page.getByText(/UNSUPPORTED_MODE/)).toHaveCount(0);
  expect(prepareRequests).toHaveLength(0);
  expect(runCount()).toBe(0);

  await page.getByRole("button", { name: "指定政策对象" }).click();
  const firstTarget = page.getByRole("textbox", { name: "研究对象编号 1" });
  const secondTarget = page.getByRole("textbox", { name: "研究对象编号 2" });
  await expect(firstTarget).toBeFocused();
  await expect(page.getByText("政策对象 A")).toBeVisible();
  await expect(page.getByText("政策对象 B")).toBeVisible();
  const submit = page.getByRole("button", { name: "使用高级入口执行" });
  await expect(submit).toBeDisabled();
  await firstTarget.fill("POLICY-A");
  await expect(submit).toBeDisabled();
  await secondTarget.fill("POLICY-A");
  await expect(submit).toBeDisabled();
  await secondTarget.fill("invalid/id");
  await expect(submit).toBeDisabled();
  await secondTarget.fill("POLICY-B");
  await expect(submit).toBeEnabled();

  await page.getByRole("button", { name: "添加对象" }).click();
  await page.getByRole("textbox", { name: "研究对象编号 3" }).fill("POLICY-C");
  await page.getByRole("textbox", { name: "自定义维度（选填）" }).fill("申报条件");
  await page.getByRole("button", { name: "添加自定义维度" }).click();
  await expect(submit).toBeEnabled();
  for (let index = 4; index <= 8; index += 1) {
    await page.getByRole("button", { name: "添加对象" }).click();
    await page.getByRole("textbox", { name: `研究对象编号 ${index}` }).fill(`POLICY-${index}`);
  }
  await expect(page.getByRole("textbox", { name: "研究对象编号 8" })).toHaveValue("POLICY-8");
  await expect(page.getByRole("button", { name: "添加对象" })).toBeDisabled();
  for (let index = 8; index >= 3; index -= 1) {
    await page.getByRole("button", { name: `移除研究对象 ${index}` }).click();
  }
  await expect(page.getByRole("textbox", { name: "研究对象编号 3" })).toHaveCount(0);

  await page.getByRole("button", { name: "前往文献目录查找编号" }).click();
  await expect(page.getByRole("heading", { name: "政策文献目录" })).toBeVisible();
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "研究" }).click();
  await expect(page.getByRole("main", { name: "研究工作区" })).toBeVisible();
  await expect(firstTarget).toHaveValue("POLICY-A");
  await expect(secondTarget).toHaveValue("POLICY-B");
  await expect(advancedQuestion).toHaveValue(question);

  await openSidebar(page);
  await page.getByRole("button", { name: "新建研究" }).click();
  await page.getByRole("tab", { name: "政策比较" }).click();
  const secondQuestion = "比较另一组政策的扶持范围";
  await page.getByRole("textbox", { name: "研究需求" }).fill(secondQuestion);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(secondQuestion);
  await expect(page.getByRole("textbox", { name: "研究对象编号 1" })).toHaveValue("");
  await openSidebar(page);
  await page.locator(".history-link").filter({ hasText: question }).click();
  await expect(page.getByRole("textbox", { name: "研究对象编号 1" })).toHaveValue("POLICY-A");
  await expect(page.getByRole("textbox", { name: "研究对象编号 2" })).toHaveValue("POLICY-B");
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(question);
  expect(prepareRequests).toHaveLength(0);
  expect(runCount()).toBe(0);

  await page.getByRole("button", { name: "使用高级入口执行" }).click();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-1");
  expect(prepareRequests).toHaveLength(0);
  expect(runCount()).toBe(1);
  expect(runBodies[0]).toEqual({ task_type: "compare", question, targets: ["POLICY-A", "POLICY-B"], dimensions: ["申报条件"] });
});

test("Real client Mock API: advanced extract and multi-plan handoff send explicit requests", async ({ page }) => {
  const prepareRequests: Record<string, unknown>[] = [];
  const runBodies: Record<string, unknown>[] = [];
  await page.route("**/api/research/prepare", async (route) => {
    prepareRequests.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Advanced handoff must not prepare." }) });
  });
  const runCount = await installRunRoute(page, (body) => runBodies.push(body));
  await page.goto("/");

  await page.getByRole("tab", { name: "要素提取" }).click();
  const extractQuestion = "提取政策中的支持对象与申请条件";
  await page.getByRole("textbox", { name: "研究需求" }).fill(extractQuestion);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "指定政策对象" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(extractQuestion);
  const submit = page.getByRole("button", { name: "使用高级入口执行" });
  await expect(submit).toBeDisabled();
  expect(prepareRequests).toHaveLength(0);
  expect(runCount()).toBe(0);
  await page.getByRole("textbox", { name: "政策编号" }).fill("DEMO-EXTRACT-1");
  await page.getByRole("button", { name: "支持对象", exact: true }).click();
  await submit.click();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-1");
  expect(runBodies[0]).toEqual({ task_type: "extract", question: extractQuestion, targets: ["DEMO-EXTRACT-1"], dimensions: ["支持对象"] });

  await newConversation(page);
  await page.getByRole("tab", { name: "多方案分析" }).click();
  const plansQuestion = "分析方案政策的适用范围差异";
  await page.getByRole("textbox", { name: "研究需求" }).fill(plansQuestion);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(plansQuestion);
  const plansSubmit = page.getByRole("button", { name: "使用高级入口执行" });
  await expect(plansSubmit).toBeDisabled();
  expect(prepareRequests).toHaveLength(0);
  expect(runCount()).toBe(1);
  await page.getByRole("textbox", { name: "研究对象编号 1" }).fill("PLAN-A");
  await page.getByRole("textbox", { name: "研究对象编号 2" }).fill("PLAN-B");
  await plansSubmit.click();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-2");
  expect(prepareRequests).toHaveLength(0);
  expect(runCount()).toBe(2);
  expect(runBodies[1]).toEqual({ task_type: "compare_plans", question: plansQuestion, targets: ["PLAN-A", "PLAN-B"], dimensions: [] });
});

test("Real client Mock API: 409 requires explicit restart and omits the stale state token", async ({ page }) => {
  const requests: Record<string, unknown>[] = [];
  let calls = 0;
  await page.route("**/api/research/prepare", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(prep("qa", "needs_clarification", 0, "选择研究角度", ["产业政策"])) });
    } else if (calls === 2) {
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "研究准备状态已过期" }) });
    } else {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(prep("qa", "ready", 0)) });
    }
  });
  const runCount = await installRunRoute(page);
  await page.goto("/");
  await page.getByRole("textbox", { name: "研究需求" }).fill("数字经济");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByRole("button", { name: "产业政策" }).click();
  await expect(page.getByRole("button", { name: "重新开始准备研究" })).toBeVisible();
  await expect(page.getByRole("button", { name: "产业政策" })).toBeDisabled();
  expect(runCount()).toBe(0);
  await page.getByRole("button", { name: "重新开始准备研究" }).click();
  await expect(page.getByText(/已清除失效的准备状态/)).toBeVisible();
  await page.getByRole("textbox", { name: "研究需求" }).fill("重新研究数字经济政策");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".result-message")).toBeVisible();
  expect(requests).toHaveLength(3);
  expect(requests[1]).toHaveProperty("state");
  expect(requests[2]).not.toHaveProperty("state");
  expect(runCount()).toBe(1);
});

test("Real client Mock API: catalog sends exact filters, paginates, and reads bounded source pages", async ({ page }, info) => {
  const directory = await artifacts(info);
  const listRequests: URL[] = [];
  const contentRequests: URL[] = [];
  const total = 5000;
  const text = "Mock API 政策原文段落。".repeat(300);
  await page.route("**/api/policies**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/content")) {
      const planid = path.split("/").at(-2) ?? "POLICY-MOCK-1";
      contentRequests.push(url);
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 0);
      const body = Array.from({ length: total }, (_, index) => index % 30 === 0 ? "M" : "文").join("");
      const chunk = Array.from(body).slice(offset, offset + limit).join("");
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ dataset: { generation_id: "mock-generation", revision: 4, total_documents: 2, partial: false, source: "current_published_frozen_release" }, planid, title: "数字经济政策（Mock）", offset, limit, total_chars: total, has_more: offset + Array.from(chunk).length < total, text: chunk }) });
      return;
    }
    if (/\/POLICY-MOCK-[12]$/.test(path)) {
      const planid = path.split("/").at(-1);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ dataset: { generation_id: "mock-generation", revision: 4, total_documents: 2, partial: false, source: "current_published_frozen_release" }, policy: { planid, title: "数字经济政策（Mock）", area: "北京市", fyp: "十四五", plantype_label: "实施意见", body_chars: total, sections: [{ index: 0, level: 1, path: "第一章 总则", start: 0, end: total }] } }) });
      return;
    }
    listRequests.push(url);
    const pageNumber = Number(url.searchParams.get("page") ?? 1);
    const q = url.searchParams.get("q");
    const item = (planid: string, title: string) => ({ planid, title, area: "北京市", fyp: "十四五", plantype_label: "实施意见", matches: q ? [{ field: "title", offset: 0, snippet: title }] : [] });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ dataset: { generation_id: "mock-generation", revision: 4, total_documents: 2, partial: false, source: "current_published_frozen_release" }, query: { q, area: url.searchParams.get("area"), fyp: url.searchParams.get("fyp"), plantype: url.searchParams.get("plantype"), match_mode: q ? "literal" : null }, matched_documents: 2, page: pageNumber, page_size: Number(url.searchParams.get("page_size") ?? 20), total_pages: 2, items: [item(pageNumber === 1 ? "POLICY-MOCK-1" : "POLICY-MOCK-2", pageNumber === 1 ? "数字经济政策（Mock）" : "数字经济促进意见（Mock）")] }) });
  });

  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await page.getByRole("button", { name: "文献", exact: true }).click();
  await expect(page.getByRole("heading", { name: "政策文献目录" })).toBeVisible();
  await page.getByRole("searchbox").fill("数字经济");
  await page.getByPlaceholder("例如：北京市").fill("北京市");
  await page.getByPlaceholder("例如：十四五").fill("十四五");
  await page.getByRole("button", { name: "检索目录" }).click();
  await expect(page.getByText("字面匹配结果不等于全部语义相关政策。")).toBeVisible();
  const applied = listRequests.at(-1)!;
  expect(applied.searchParams.get("q")).toBe("数字经济");
  expect(applied.searchParams.get("area")).toBe("北京市");
  expect(applied.searchParams.get("fyp")).toBe("十四五");
  await page.screenshot({ path: join(directory, "catalog-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "下一页", exact: false }).click();
  await expect(page.getByRole("button", { name: /数字经济促进意见/ })).toBeVisible();
  expect(listRequests.at(-1)?.searchParams.get("page")).toBe("2");

  await page.getByRole("button", { name: /数字经济促进意见/ }).click();
  await expect(page.getByRole("region", { name: "政策原文" })).toBeVisible();
  expect(contentRequests[0]?.searchParams.get("limit")).toBe("4000");
  await page.getByRole("region", { name: "政策原文" }).getByRole("button", { name: /下一页/ }).click();
  await expect.poll(() => contentRequests.length).toBe(2);
  expect(contentRequests[1]?.searchParams.get("offset")).toBe("4000");
  expect(contentRequests[1]?.searchParams.get("limit")).toBe("4000");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.screenshot({ path: join(directory, "home-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "打开侧栏" }).click();
  await expect(page.getByRole("button", { name: "新建研究" })).toBeVisible();
  await expect(page.locator(".app-sidebar").getByRole("button", { name: "文献目录" })).toHaveCount(0);
  await page.locator(".sidebar-scrim").click({ position: { x: 350, y: 400 } });
  await page.waitForFunction(() => {
    const sidebar = document.querySelector(".app-sidebar");
    return sidebar && !sidebar.classList.contains("is-mobile-open") && sidebar.getBoundingClientRect().right <= 0;
  });
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "文献", exact: true }).click();
  await expect(page.getByRole("heading", { name: "政策文献目录" })).toBeVisible();
  await page.screenshot({ path: join(directory, "catalog-mobile.png"), fullPage: true });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.route("**/api/research/prepare", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(prep("qa", "needs_clarification", 0, "移动端 Mock API 追问", ["确认选项"])) });
  });
  await page.getByRole("button", { name: "打开侧栏" }).click();
  await page.getByRole("button", { name: "新建研究" }).click();
  await page.getByRole("textbox", { name: "研究需求" }).fill("移动端工作区检查");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("main", { name: "研究工作区" })).toBeVisible();
  await page.screenshot({ path: join(directory, "workspace-mobile.png"), fullPage: true });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("Real client Mock API: IME Enter, Unicode limit, mode cancellation, and one in-flight send", async ({ page }) => {
  let firstPrepareCount = 0;
  await page.route("**/api/research/prepare", async (route) => {
    firstPrepareCount += 1;
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(prep("qa", "needs_clarification", 0, "Mock API 追问", ["选项"])) });
  });
  const runCount = await installRunRoute(page);
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "研究需求" });
  await input.fill("😀".repeat(4001));
  await expect(page.getByRole("button", { name: "发送消息" })).toBeDisabled();
  await expect(page.getByText(/输入过长/)).toBeVisible();
  await input.fill("数字经济");
  await input.evaluate((textarea: HTMLTextAreaElement) => {
    textarea.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true, cancelable: true }));
    textarea.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
  });
  expect(firstPrepareCount).toBe(0);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "发送消息" })).toBeDisabled();
  await page.getByRole("tab", { name: "文献梳理" }).click();
  await expect(page.getByRole("tab", { name: "文献梳理" })).toHaveAttribute("aria-selected", "true");
  await page.waitForTimeout(300);
  expect(firstPrepareCount).toBe(1);
  expect(runCount()).toBe(0);
  await expect(page.getByRole("heading", { name: "Mock API 追问" })).toHaveCount(0);
});

test("Real client Mock API: two results keep separate evidence links after switching sessions", async ({ page }) => {
  const readyRequest = prep("qa", "ready");
  await page.route("**/api/research/prepare", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(readyRequest) }));
  let count = 0;
  await page.route("**/api/runs", async (route) => {
    count += 1;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(runResult(`separate-${count}`)) });
  });
  await page.goto("/");
  await page.getByRole("textbox", { name: "研究需求" }).fill("研究主题 A");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".result-message")).toHaveCount(1);
  await page.getByRole("textbox", { name: "研究需求" }).fill("研究主题 A 的补充分析");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".result-message")).toHaveCount(2);
  await newConversation(page);
  await page.getByRole("textbox", { name: "研究需求" }).fill("研究主题 B");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".result-message")).toHaveCount(1);
  await openSidebar(page);
  const sessions = page.locator(".history-link");
  await expect(sessions).toHaveCount(2);
  await sessions.nth(1).click();
  await expect(page.locator(".result-message")).toHaveCount(2);
  await page.getByRole("button", { name: /原文/ }).first().click();
  await expect(page.locator(".message-evidence-panel")).toContainText("Mock API 政策文献 separate-1");
  await expect(page.locator(".message-evidence-panel")).toContainText("Mock API 原文引句 separate-1");
  await expect(page.locator(".message-evidence-panel")).not.toContainText("separate-2");
});
