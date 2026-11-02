import { expect, test, type Page } from "@playwright/test";

// Staging-only smoke for today's UI, using the real HTTP client with intercepted
// API responses. This must never be interpreted as real Qwen / RAG acceptance.
function prepared(question: string) {
  return {
    status: "ready", mode: "qa", message: "研究范围已准备完成。",
    options: [],
    state: {
      state_id: "staging-qa", revision: 0, expires_at: "2027-01-01T00:00:00Z",
      mode: "qa", original_request: question, topic: null, time_scope: null,
      area: null, dimensions: [], confirmed_conditions: [], pending_conditions: [],
      completed_questions: [], clarification_count: 0, condition_statuses: [],
    },
    request: { task_type: "qa", question, targets: null, dimensions: [] },
    warnings: [],
  };
}

function partialResult() {
  return {
    run_id: "staging-fact-1", session_id: "staging-session", task_type: "qa",
    status: "completed", evidence_status: "insufficient",
    answer: "## 已核验政策事实（非完整研究结论）\n\n该规划提出加强产业基础设施建设。",
    claims: [{
      text: "该规划提出加强产业基础设施建设。",
      chunk_ids: ["chunk-staging"], quotes: ["加强产业基础设施建设"],
      dimension: null, anchors: [],
    }],
    evidence_refs: [{
      planid: "2561", chunk_id: "chunk-staging", parent_id: null,
      title: "测试政策文献", area: "沧州市", fyp: "十五", section_idx: 1,
      section_path: "第一章", char_start: 0, char_end: 20,
      chunk_char_start: 0, chunk_char_end: 20, truncated: false,
      source: "kb_read", observed_ranges: [[0, 20]], complete: true,
      out_of_section: false, admin_level: null, plantype_label: null,
      document_id: null, document_version_id: null, generation_id: null,
      source_hash: null, section_id: null, section_char_start: null,
      section_char_end: null, offset_basis: null, offset_unit: null,
      body_origin: null, contract_version: null, quality_warnings: [],
    }],
    warnings: ["部分研究维度证据不足。"], dropped_chunk_ids: [],
    error: null, structured: null, route: null,
    meta: { research_delivery: { status: "partial", audited_analysis: false } },
  };
}

async function mockQA(page: Page, result: Record<string, unknown>) {
  await page.route("**/api/research/prepare", async (route) => {
    const question = String(route.request().postDataJSON()?.message ?? "");
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify(prepared(question)),
    });
  });
  await page.route("**/api/runs", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify(result),
    });
  });
}

test("current UI shows fact-only research under one 研究结果 title without partial disclaimers", async ({ page }) => {
  await mockQA(page, partialResult());
  await page.goto("/");
  await expect(page.getByRole("textbox", { name: "研究需求" })).toBeVisible();
  await page.getByRole("textbox", { name: "研究需求" }).fill(
    "分析沧州市十五计划中的产业发展措施",
  );
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("heading", { name: "研究结果" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "研究结果（部分）" })).toHaveCount(0);
  await expect(page.getByText("该规划提出加强产业基础设施建设。").first()).toBeVisible();
  await expect(page.getByText("尚未完成的研究分析不得视为结论。", { exact: false })).toHaveCount(0);
  await expect(page.getByText("## 已核验政策事实", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看原文" })).toBeVisible();
  await expect(page.locator('button[data-chunk-id="chunk-staging"]').first()).toBeVisible();
});

test("current UI shows a no-evidence QA refusal without invented citations", async ({ page }) => {
  const result = partialResult();
  result.answer = "本轮未取得足够可引用的政策正文；未检索到不代表相关政策不存在。";
  result.claims = [];
  result.evidence_refs = [];
  result.meta = {};
  await mockQA(page, result);
  await page.goto("/");
  await page.getByRole("textbox", { name: "研究需求" }).fill(
    "天津市2024年对人工智能企业的具体补贴金额是多少？",
  );
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText("未检索到不代表相关政策不存在", { exact: false })).toBeVisible();
  await expect(page.locator("button[data-chunk-id]")).toHaveCount(0);
});

test("current UI selects research modes through the expanded menu", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("button", { name: "研究模式", exact: false }).click();
  await expect(page.getByRole("group", { name: "可选研究模式" })).toBeVisible();
  await page.getByRole("group", { name: "可选研究模式" }).getByRole("button", { name: "文献梳理" }).click();
  await expect(page.getByRole("button", { name: "恢复默认模式" })).toBeVisible();
});
