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
    structured: taskType === "compare" ? {
      objects: ["POLICY-A", "POLICY-B"].map((planid) => ({ planid, title: `Mock API ${planid}`, area: null, fyp: null, plantype_label: null })),
      dimensions: ["申报条件"],
      matrix: [{
        dimension: "申报条件",
        cells: ["POLICY-A", "POLICY-B"].map((planid) => ({ cell_id: `cell-${planid}`, planid, status: "insufficient", reason: `Mock API ${planid}：此单元格仅用于前端联调，不提供政策事实。` })),
        difference: { text: "Mock API：证据不足，不作事实比较。", supporting_cells: [], kind: "analysis", status: "insufficient" },
        comparability: { comparable: false, reason: "Mock API：未连接真实证据。" },
      }],
      comparability: { comparable: false, reason: "Mock API：未连接真实证据。" },
      coverage_notes: ["Mock API：此矩阵不是模型或知识库执行结果。"],
      analysis_gaps: [],
    } : null,
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
  if (closed) await page.getByRole("button", { name: "展开侧栏" }).click();
  await expect(page.getByRole("button", { name: "新建研究" })).toBeVisible();
}

async function newConversation(page: Page) {
  await openSidebar(page);
  await page.getByRole("button", { name: "新建研究" }).click();
}

async function selectResearchMode(page: Page, name: string) {
  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("button", { name: /研究模式/ }).click();
  const modes = page.getByRole("group", { name: "可选研究模式" });
  await expect(modes.getByRole("button")).toHaveCount(5);
  await modes.getByRole("button", { name, exact: true }).click();
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
  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("button", { name: /研究模式/ }).click();
  await expect(page.getByRole("group", { name: "可选研究模式" }).getByRole("button")).toHaveCount(5);
  await page.getByRole("button", { name: "关闭更多功能" }).click();
  await expect.poll(() => page.locator(".architecture-scene img").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1000);
  await page.screenshot({ path: join(directory, "home-desktop.png"), fullPage: true });

  const input = page.getByRole("textbox", { name: "研究需求" });
  await input.fill("数字经济");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("heading", { name: "你想了解这个主题的哪个方面？" })).toBeVisible();
  await page.screenshot({ path: join(directory, "conversation-clarification.png"), fullPage: true });
  await page.getByRole("button", { name: "产业政策" }).click();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-1");
  await page.locator(".result-followup-notes summary").click();
  await expect(page.locator(".result-followup-notes")).toContainText("时间条件仅作为请求条件");
  expect(prepareRequests).toHaveLength(2);
  expect(prepareRequests[0]).toMatchObject({ mode: "qa", message: "数字经济" });
  expect(prepareRequests[1]).toMatchObject({ state: { state_id: "mock-state", revision: 0 } });
  expect(runCount()).toBe(1);
  await page.screenshot({ path: join(directory, "conversation-multiturn.png"), fullPage: true });
  await expect(page.getByRole("region", { name: "依据与引句" })).toContainText("Mock API 政策文献 mock-1");
  await page.locator(".qa-source-evidence summary").click();
  await expect(page.locator(".qa-source-evidence")).toContainText("Mock API 原文引句 mock-1");
  await page.getByRole("button", { name: "查看原文" }).click();
  await expect(page.getByRole("main", { name: "政策原文阅读" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Mock API 政策文献 mock-1" })).toBeVisible();
  await page.getByRole("button", { name: "返回研究对话" }).click();
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
  await selectResearchMode(page, "文献梳理");
  await page.getByRole("textbox", { name: "研究需求" }).fill("梳理支持数字经济的政策");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("heading", { name: "后端问题：先确定按哪个范围整理？" })).toBeVisible();
  expect(runCount()).toBe(0);
  await page.getByRole("button", { name: "先按时间范围" }).click();
  await expect(page.getByRole("heading", { name: "后端问题：请确认时间范围和地区。" })).toBeVisible();
  await page.getByRole("button", { name: "十四五时期，北京市" }).click();
  await expect(page.locator(".result-message")).toBeVisible();
  await page.locator(".result-followup-notes summary").click();
  await expect(page.locator(".result-followup-notes")).toContainText("地区条件仅作为研究请求说明");
  expect(prepareRequests).toHaveLength(3);
  expect(prepareRequests[1]).toMatchObject({ message: "先按时间范围", state: { revision: 0 } });
  expect(prepareRequests[2]).toMatchObject({ message: "十四五时期，北京市", state: { revision: 1 } });
  expect(runCount()).toBe(1);
  const { session_id, ...runRequest } = runBodies[0];
  expect(runRequest).toEqual({ task_type: "organize", question: "正式研究问题：数字经济政策及北京市、十四五时期的支持措施", targets: null, dimensions: ["支持措施"] });
  expect(session_id).toEqual(expect.stringMatching(/^guance-/));
  await expect(page.locator(".result-followup-notes")).toContainText("北京市 · 地区仅作为请求条件，不保证精确过滤。");
  await expect(page.locator(".result-followup-notes")).toContainText(/不代表已完成筛选/);
});

test("Real client Mock API: autonomous compare preserves session and catalog navigation without a manual ID gate", async ({ page }) => {
  const runBodies: Record<string, unknown>[] = [];
  const unexpectedPrepare: string[] = [];
  await page.route("**/api/research/prepare", async (route) => {
    unexpectedPrepare.push("prepare");
    await route.fulfill({ status: 500, body: "Compare must use the autonomous research runtime." });
  });
  await page.route("**/api/research/resolve-compare", async (route) => {
    unexpectedPrepare.push("resolve");
    await route.fulfill({ status: 500, body: "Frontend may not pre-resolve comparison sources." });
  });
  const runCount = await installRunRoute(page, (body) => runBodies.push(body));
  await page.route("**/api/policies**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      dataset: { generation_id: "mock-generation", revision: 1, total_documents: 0, partial: false, source: "current_published_frozen_release" },
      query: { q: null, area: null, fyp: null, plantype: null, match_mode: null },
      matched_documents: 0, page: 1, page_size: 20, total_pages: 1, items: [],
    }) });
  });
  await page.goto("/");
  await selectResearchMode(page, "政策比较");
  const question = "比较下北京市2015和2016年的政府工作报告";
  await page.getByRole("textbox", { name: "研究需求" }).fill(question);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("main", { name: "研究工作区" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "政策比较矩阵" })).toBeVisible();
  const matrix = page.getByRole("region", { name: "政策比较矩阵" });
  await expect(matrix).toContainText("POLICY-A");
  await expect(matrix).toContainText("POLICY-B");
  await expect(matrix).toContainText("此单元格仅用于前端联调");
  await expect(page.getByRole("textbox", { name: "研究对象编号 1" })).toHaveCount(0);
  expect(unexpectedPrepare).toEqual([]);
  expect(runCount()).toBe(1);
  const { session_id: sessionA, ...requestA } = runBodies[0];
  expect(requestA).toEqual({ task_type: "compare", question });
  expect(sessionA).toEqual(expect.stringMatching(/^guance-/));

  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "文献" }).click();
  await expect(page.getByRole("heading", { name: "政策文献目录" })).toBeVisible();
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "研究" }).click();
  await expect(page.getByRole("heading", { name: "政策比较矩阵" })).toBeVisible();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-1");

  await newConversation(page);
  await selectResearchMode(page, "政策比较");
  const secondQuestion = "比较另一组政策的扶持范围";
  await page.getByRole("textbox", { name: "研究需求" }).fill(secondQuestion);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-2");
  expect(runCount()).toBe(2);
  const { session_id: sessionB, ...requestB } = runBodies[1];
  expect(requestB).toEqual({ task_type: "compare", question: secondQuestion });
  expect(sessionB).toEqual(expect.stringMatching(/^guance-/));
  expect(sessionB).not.toBe(sessionA);

  await openSidebar(page);
  await expect(page.locator(".history-link")).toHaveCount(2);
  await page.locator(".history-link").nth(1).click();
  await expect(page.getByRole("heading", { name: "政策比较矩阵" })).toBeVisible();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-1");
  await expect(page.locator(".result-message")).not.toContainText("mock-2");
  expect(unexpectedPrepare).toEqual([]);
  expect(runCount()).toBe(2);
});

test("Real client Mock API: extract and multi-plan use autonomous requests and distinct sessions", async ({ page }) => {
  const unexpectedPrepare: string[] = [];
  await page.route("**/api/research/prepare", async (route) => {
    unexpectedPrepare.push("prepare");
    await route.fulfill({ status: 500, body: "Autonomous mode must bypass frontend preparation." });
  });
  const runBodies: Record<string, unknown>[] = [];
  const runCount = await installRunRoute(page, body => runBodies.push(body));
  await page.goto("/");

  await selectResearchMode(page, "要素提取");
  const extractQuestion = "提取编号 DEMO-EXTRACT-1 政策中的支持对象与申请条件";
  await page.getByRole("textbox", { name: "研究需求" }).fill(extractQuestion);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-1");
  await expect(page.getByRole("textbox", { name: "政策编号" })).toHaveCount(0);
  expect(runCount()).toBe(1);
  const { session_id: extractSession, ...extractRequest } = runBodies[0];
  expect(extractRequest).toEqual({ task_type: "extract", question: extractQuestion });
  expect(extractSession).toEqual(expect.stringMatching(/^guance-/));

  await newConversation(page);
  await selectResearchMode(page, "多方案分析");
  const plansQuestion = "比较 PLAN-A 和 PLAN-B 两项政策的适用范围差异";
  await page.getByRole("textbox", { name: "研究需求" }).fill(plansQuestion);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".result-message")).toContainText("Mock API 研究结果 mock-2");
  await expect(page.getByRole("textbox", { name: "研究对象编号 1" })).toHaveCount(0);
  expect(runCount()).toBe(2);
  const { session_id: planSession, ...planRequest } = runBodies[1];
  expect(planRequest).toEqual({ task_type: "compare_plans", question: plansQuestion });
  expect(planSession).toEqual(expect.stringMatching(/^guance-/));
  expect(planSession).not.toBe(extractSession);
  expect(unexpectedPrepare).toEqual([]);
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
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ dataset: { generation_id: "mock-generation", revision: 4, total_documents: 2, partial: false, source: "current_published_frozen_release" }, policy: { planid, title: "数字经济政策（Mock）", area: "北京市", fyp: "第十四个五年规划", year: 2021, plantype_label: "实施意见", body_chars: total, sections: [{ index: 0, level: 1, path: "第一章 总则", start: 0, end: total }] } }) });
      return;
    }
    listRequests.push(url);
    const pageNumber = Number(url.searchParams.get("page") ?? 1);
    const q = url.searchParams.get("q");
    const item = (planid: string, title: string) => ({ planid, title, area: "北京市", fyp: "第十四个五年规划", year: 2021, plantype_label: "实施意见", matches: q ? [{ field: "title", offset: 0, snippet: title }] : [] });
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ dataset: { generation_id: "mock-generation", revision: 4, total_documents: 2, partial: false, source: "current_published_frozen_release" }, query: { q, area: url.searchParams.get("area"), fyp: url.searchParams.get("fyp"), plantype: url.searchParams.get("plantype"), match_mode: q ? "literal" : null }, matched_documents: 2, page: pageNumber, page_size: Number(url.searchParams.get("page_size") ?? 20), total_pages: 2, items: [item(pageNumber === 1 ? "POLICY-MOCK-1" : "POLICY-MOCK-2", pageNumber === 1 ? "数字经济政策（Mock）" : "数字经济促进意见（Mock）")] }) });
  });

  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await page.getByRole("button", { name: "文献", exact: true }).click();
  await expect(page.getByRole("heading", { name: "政策文献目录" })).toBeVisible();
  await expect(page.locator(".catalog-header .library-heading-kicker")).toHaveText("POLICY LIBRARY");
  await expect(page.getByText("当前库共有 2 篇政策文献")).toBeVisible();
  await expect(page.locator(".catalog-policy-card")).toHaveCount(0);
  await page.screenshot({ path: join(directory, "catalog-initial-blank.png"), fullPage: true });
  await page.getByRole("searchbox").fill("数字经济");
  await page.getByPlaceholder("例如：北京市").fill("北京市");
  await page.getByPlaceholder("例如：十四五").fill("十四五");
  await page.getByRole("button", { name: "检索目录" }).click();
  await expect(page.getByText("字面匹配结果不等于全部语义相关政策。")).toBeVisible();
  const applied = listRequests.at(-1)!;
  expect(applied.searchParams.get("q")).toBe("数字经济");
  expect(applied.searchParams.get("area")).toBe("北京市");
  expect(applied.searchParams.get("fyp")).toBe("十四五");
  const firstPolicyCard = page.locator(".catalog-policy-card").first();
  const firstTitleMark = firstPolicyCard.locator(".catalog-policy-title mark");
  const firstSnippetMark = firstPolicyCard.locator(".catalog-snippet mark");
  await expect(firstTitleMark).toHaveText("数字经济");
  await expect(firstSnippetMark).toHaveText("数字经济");
  await expect(firstTitleMark).toHaveCSS("color", "rgb(178, 41, 36)");
  await expect(firstSnippetMark).toHaveCSS("color", "rgb(178, 41, 36)");
  await expect(firstPolicyCard.locator(".catalog-policy-meta")).toContainText("十四五");
  await expect(firstPolicyCard.locator(".catalog-policy-meta")).not.toContainText("第十四个五年规划");
  await page.screenshot({ path: join(directory, "catalog-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "下一页", exact: false }).click();
  await expect(page.getByRole("button", { name: /数字经济促进意见/ })).toBeVisible();
  expect(listRequests.at(-1)?.searchParams.get("page")).toBe("2");

  const policyCard = page.getByRole("button", { name: /数字经济促进意见/ });
  await expect(policyCard.locator(".catalog-policy-title mark")).toHaveText("数字经济");
  await expect(policyCard.locator(".catalog-snippet mark")).toHaveText("数字经济");
  await expect(policyCard.locator(".catalog-policy-title mark")).toHaveCSS("color", "rgb(178, 41, 36)");
  await expect(policyCard.locator(".catalog-snippet mark")).toHaveCSS("color", "rgb(178, 41, 36)");
  await expect(policyCard.locator(".catalog-policy-meta")).toContainText("十四五");
  await expect(policyCard.locator(".catalog-policy-meta")).not.toContainText("第十四个五年规划");
  await policyCard.hover();
  const preview = page.getByRole("complementary", { name: "政策详情预览" });
  await expect(preview).toBeVisible();
  await expect(preview).toContainText("十四五");
  await expect(preview).not.toContainText("第十四个五年规划");
  await expect(page.getByRole("region", { name: "政策原文" }).locator(".catalog-rich-text")).not.toBeEmpty();
  await page.screenshot({ path: join(directory, "catalog-hover-preview.png"), fullPage: true });
  await policyCard.click();
  await expect(page.getByRole("main", { name: "政策原文阅读" })).toBeVisible();
  await expect(page.getByRole("searchbox")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "章节目录" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await expect(page.locator(".app-sidebar")).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect.poll(() => page.locator(".global-topbar").evaluate((header) => header.getBoundingClientRect().top)).toBe(0);
  // The back action must remain pinned just beneath the sticky global navigation
  // even while the policy text is scrolled to the end.
  const stickyBack = page.getByRole("button", { name: "返回检索结果" });
  await expect(stickyBack).toHaveCSS("position", "sticky");
  await expect.poll(() => stickyBack.evaluate((button) => Math.round(button.getBoundingClientRect().top))).toBe(64);
  await expect(stickyBack).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.getByRole("region", { name: "政策原文" })).toBeVisible();
  expect(contentRequests[0]?.searchParams.get("limit")).toBe("4000");
  await page.getByRole("region", { name: "政策原文" }).getByRole("button", { name: /下一页/ }).click();
  await expect.poll(() => contentRequests.length).toBe(2);
  expect(contentRequests[1]?.searchParams.get("offset")).toBe("4000");
  expect(contentRequests[1]?.searchParams.get("limit")).toBe("4000");
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.screenshot({ path: join(directory, "policy-reader-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "返回检索结果" }).click();
  await expect(page.getByRole("searchbox")).toHaveValue("数字经济");
  await expect(page.getByRole("button", { name: /数字经济促进意见/ })).toBeVisible();
  await expect(page.locator(".catalog-page-count")).toHaveText("第 2 / 2 页");

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
  await expect(page.locator(".catalog-policy-card")).toHaveCount(0);
  await page.getByRole("button", { name: "检索目录" }).click();
  await page.getByRole("button", { name: /数字经济政策（Mock）/ }).click();
  await expect(page.getByRole("main", { name: "政策原文阅读" })).toBeVisible();
  await expect(page.getByRole("searchbox")).toHaveCount(0);
  await page.screenshot({ path: join(directory, "policy-reader-mobile.png"), fullPage: true });
  await expect(page.getByRole("button", { name: "返回检索结果" })).toHaveCSS("top", "56px");
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

test("Real client Mock API: IME Enter, Unicode limit, single-flight request and safe mode change", async ({ page }) => {
  let prepareCount = 0;
  await page.route("**/api/research/prepare", async (route) => {
    prepareCount += 1;
    await new Promise((resolve) => setTimeout(resolve, 550));
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
  expect(prepareCount).toBe(0);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "发送消息" })).toBeDisabled();
  // The current composer intentionally blocks mode changes during a request;
  // this prevents a stale preparation from being assigned to a new mode.
  await expect(page.getByRole("button", { name: "更多功能" })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "Mock API 追问" })).toBeVisible();
  expect(prepareCount).toBe(1);
  expect(runCount()).toBe(0);
  await selectResearchMode(page, "文献梳理");
  await expect(page.getByRole("button", { name: "恢复默认模式" })).toBeVisible();
  await expect(page.getByRole("button", { name: "选项" })).toBeDisabled();
  expect(prepareCount).toBe(1);
  expect(runCount()).toBe(0);
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
  const firstResult = page.locator(".result-message").first();
  await expect(firstResult.getByRole("region", { name: "依据与引句" })).toContainText("Mock API 政策文献 separate-1");
  await firstResult.locator(".qa-source-evidence summary").click();
  await expect(firstResult.locator(".qa-source-evidence")).toContainText("Mock API 原文引句 separate-1");
  await expect(firstResult).not.toContainText("separate-2");
});


test("Real client Mock API: explicit comparison scope reaches autonomous agent without frontend policy IDs", async ({ page }) => {
  const bodyRequests: Record<string, unknown>[] = [];
  const resolutionRequests: Record<string, unknown>[] = [];
  const question = "比较浙江和江苏十四五数字经济发展目标的异同";
  await page.route("**/api/research/resolve-compare", async route => {
    resolutionRequests.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ status: 500, body: "Policy resolution belongs to the research backend." });
  });
  const runCount = await installRunRoute(page, body => bodyRequests.push(body));
  await page.goto("/");
  await selectResearchMode(page, "政策比较");
  await page.getByRole("textbox", { name: "研究需求" }).fill(question);
  await page.getByRole("button", { name: "发送消息" }).click();

  await expect(page.getByRole("heading", { name: "政策比较矩阵" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "研究对象编号 1" })).toHaveCount(0);
  expect(runCount()).toBe(1);
  expect(resolutionRequests).toHaveLength(0);
  const { session_id, ...request } = bodyRequests[0];
  expect(request).toEqual({ task_type: "compare", question });
  expect(session_id).toEqual(expect.stringMatching(/^guance-/));
  // Scope terms remain intact; frontend must not manufacture planids or
  // restrict dimensions based on unverified heuristics.
  expect(question).toContain("浙江");
  expect(question).toContain("江苏");
  expect(question).toContain("十四五");
  expect(question).toContain("发展目标");
});

test("Landing page: modes and templates float without shifting the hero or background", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await selectResearchMode(page, "要素提取");

  const measure = async () => page.evaluate(() => {
    const top = (selector: string) => {
      const element = document.querySelector(selector);
      if (!element) throw new Error(`Missing layout anchor: ${selector}`);
      return Math.round(element.getBoundingClientRect().top);
    };
    return {
      title: top(".intro-block h1"),
      fields: top(".composer-fields"),
      background: top(".architecture-scene"),
      scrollY: window.scrollY,
    };
  });
  const original = await measure();
  const scene = page.locator(".architecture-scene");
  const sceneHeight = await scene.evaluate(element => element.getBoundingClientRect().height);
  expect(sceneHeight).toBeGreaterThanOrEqual(367);
  expect(sceneHeight).toBeLessThanOrEqual(371);
  const artwork = scene.locator("img");
  await expect(artwork).toHaveAttribute("src", "/research-mountain-architecture.webp");
  await expect(artwork).toHaveCSS("object-fit", "fill");
  await expect.poll(() => artwork.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1000);

  await page.getByRole("button", { name: "更多功能" }).click();
  const root = page.getByRole("region", { name: "更多功能菜单" });
  await expect(root).toBeVisible();
  await expect(root).toHaveCSS("position", "absolute");
  await expect(root).toHaveCSS("overflow-y", "auto");
  expect(await measure()).toEqual(original);
  expect(await root.evaluate(element => element.getBoundingClientRect().bottom)).toBeLessThanOrEqual(900);

  await page.getByRole("button", { name: /研究模式/ }).click();
  const modes = page.getByRole("group", { name: "可选研究模式" });
  await expect(modes.getByRole("button")).toHaveCount(5);
  await expect(page.getByRole("region", { name: "研究模式选择" })).toHaveCSS("position", "absolute");
  expect(await measure()).toEqual(original);

  await page.getByRole("button", { name: "返回更多功能" }).click();
  await page.getByRole("button", { name: "指令模板" }).click();
  const templates = page.getByRole("region", { name: "指令模板列表" });
  await expect(templates).toBeVisible();
  await expect(templates).toHaveCSS("position", "absolute");
  await expect(page.getByText("选择模板后将自动填入输入框，你可以继续修改后发送。")).toHaveCount(0);
  expect(await measure()).toEqual(original);

  await page.getByRole("button", { name: "关闭更多功能" }).click();
  expect(await measure()).toEqual(original);
});
