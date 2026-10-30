import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

async function artifactDirectory(info: TestInfo) {
  const directory = process.env.PLAYWRIGHT_ARTIFACT_DIR ?? info.outputDir;
  await mkdir(directory, { recursive: true });
  return directory;
}

async function expandSidebar(page: Page) {
  const sidebar = page.locator(".app-sidebar");
  if (await sidebar.evaluate((element) => element.classList.contains("is-collapsed")) || await sidebar.evaluate((element) => getComputedStyle(element).transform.includes("-"))) {
    await page.getByRole("button", { name: "打开侧栏" }).click();
  }
  await expect(page.getByRole("button", { name: "新建研究" })).toBeVisible();
}

async function startNew(page: Page) {
  await expandSidebar(page);
  await page.getByRole("button", { name: "新建研究" }).click();
  await expect(page.getByRole("textbox", { name: "研究需求" })).toBeVisible();
}

async function submitQuestion(page: Page, text: string) {
  await page.getByRole("textbox", { name: "研究需求" }).fill(text);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("main", { name: "研究工作区" })).toBeVisible();
}

test("Demo: global navigation, sidebar, and catalog stay available across views", async ({ page }, info) => {
  const directory = await artifactDirectory(info);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await expect(page.getByText("Demo 数据")).toBeVisible();
  await expect(page.getByRole("tablist", { name: "研究模式" }).getByRole("tab")).toHaveCount(5);
  await expect.poll(() => page.locator(".architecture-scene img").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1000);
  await page.screenshot({ path: join(directory, "home-desktop.png"), fullPage: true });

  await expandSidebar(page);
  await expect(page.getByRole("button", { name: "返回首页" })).toHaveAttribute("title", "返回首页");
  await submitQuestion(page, "什么是数字经济政策？");
  await expect(page.getByText(/演示研究请求已准备完成/)).toBeVisible();
  await expect(page.locator(".result-message")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "返回首页" })).toBeVisible();

  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "文献", exact: true }).click();
  await expect(page.getByRole("heading", { name: "政策文献目录" })).toBeVisible();
  await expect(page.getByText("当前库共有 1 篇政策文献")).toBeVisible();
  await expect(page.locator(".catalog-policy-card")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "数据", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "返回首页" })).toBeVisible();
  await page.screenshot({ path: join(directory, "catalog-demo.png"), fullPage: true });
  await expect(page.getByText(/合成演示文本/)).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const mobileModes = page.getByRole("tablist", { name: "研究模式" });
  await expect(mobileModes.getByRole("tab")).toHaveCount(5);
  await expect(mobileModes.getByRole("tab", { name: "政策问答" })).toHaveAttribute("aria-selected", "true");
  await page.screenshot({ path: join(directory, "home-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "打开侧栏" }).click();
  await expect(page.getByRole("button", { name: "新建研究" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "新建研究" })).toBeHidden();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("Demo: natural language, long text, IME Enter, dynamic clarification, and local history", async ({ page }, info) => {
  const directory = await artifactDirectory(info);
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "研究需求" });
  await expect(input).toHaveAttribute("placeholder", "今天想研究些什么呢？");
  await input.fill("数字经济".repeat(1600));
  await expect(input).toHaveCSS("height", "200px");
  await expect(input).toHaveCSS("overflow-y", "auto");
  await input.fill("数字经济");
  await input.evaluate((textarea: HTMLTextAreaElement) => {
    textarea.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true, cancelable: true }));
    textarea.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
  });
  await expect(page.getByRole("textbox", { name: "研究需求" })).toBeVisible();
  await expect(page.getByRole("button", { name: "发送消息" })).toBeEnabled();
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("heading", { name: "你想了解这个主题的哪个方面？" })).toBeVisible();
  await page.screenshot({ path: join(directory, "conversation-clarification-demo.png"), fullPage: true });
  await page.getByRole("button", { name: "产业政策" }).click();
  await expect(page.getByText(/研究范围已准备完成/)).toBeVisible();
  await expect(page.locator(".result-message")).toBeVisible({ timeout: 10_000 });

  await expandSidebar(page);
  await expect(page.locator(".history-link")).toHaveCount(1);
  await startNew(page);
  await expect(page.locator(".history-link")).toHaveCount(2);
  await page.getByRole("button", { name: /数字经济/ }).first().click();
  await expect(page.getByRole("heading", { name: "数字经济" })).toBeVisible();
  await page.screenshot({ path: join(directory, "conversation-multiturn-demo.png"), fullPage: true });
});

test("Demo: all five modes stay reachable and advanced handoff accepts explicit policy targets", async ({ page }) => {
  await page.goto("/");
  const tabs = page.getByRole("tablist", { name: "研究模式" });
  for (const label of ["政策问答", "要素提取", "文献梳理", "政策比较", "多方案分析"]) {
    const tab = tabs.getByRole("tab", { name: label });
    await tab.click();
    await expect(tab).toHaveAttribute("aria-selected", "true");
  }

  await page.getByRole("tab", { name: "要素提取" }).click();
  const extractQuestion = "提取指定政策的支持要素";
  await page.getByRole("textbox", { name: "研究需求" }).fill(extractQuestion);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "指定政策对象", exact: true })).toBeVisible();
  await expect(page.locator(".message-error")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "收起高级选择" })).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(extractQuestion);
  await page.getByLabel("政策编号").fill("DEMO-A");
  await page.getByRole("button", { name: "使用高级入口执行" }).click();
  await expect(page.locator(".result-message")).toBeVisible({ timeout: 10_000 });

  await startNew(page);
  await page.getByRole("tab", { name: "政策比较" }).click();
  const compareQuestion = "比较指定政策对象";
  await page.getByRole("textbox", { name: "研究需求" }).fill(compareQuestion);
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByRole("button", { name: "指定政策对象", exact: true })).toBeVisible();
  await expect(page.locator(".message-error")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "收起高级选择" })).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(compareQuestion);
  await page.getByLabel("研究对象编号 1").fill("DEMO-A");
  await page.getByLabel("研究对象编号 2").fill("DEMO-B");
  await page.getByRole("button", { name: "添加对象" }).click();
  await page.getByLabel("研究对象编号 3").fill("DEMO-C");
  await page.getByRole("button", { name: "移除研究对象 3" }).click();
  await expect(page.getByLabel("研究对象编号 3")).toHaveCount(0);
  await page.getByRole("button", { name: "使用高级入口执行" }).click();
  await expect(page.locator(".result-message")).toBeVisible({ timeout: 10_000 });
});

test("Demo: catalog shows literal search results and paged source content", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "文献", exact: true }).click();
  await expect(page.getByRole("heading", { name: "政策文献目录" })).toBeVisible();
  await page.getByRole("searchbox").fill("数字经济");
  await page.getByRole("button", { name: "检索目录" }).click();
  await expect(page.getByText("字面匹配结果不等于全部语义相关政策。")).toBeVisible();
  await expect(page.getByRole("button", { name: /演示：数字经济政策示例/ })).toBeVisible();
  await page.getByRole("button", { name: /演示：数字经济政策示例/ }).click();
  await expect(page.getByRole("heading", { name: "政策原文" })).toBeVisible();
  await expect(page.getByRole("main", { name: "政策原文阅读" })).toBeVisible();
  await expect(page.getByRole("searchbox")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await expect(page.getByRole("region", { name: "政策原文" }).getByText(/合成演示文本/)).toBeVisible();
  await page.getByRole("button", { name: "返回检索结果" }).click();
  await expect(page.getByRole("searchbox")).toHaveValue("数字经济");
  await expect(page.getByRole("button", { name: /演示：数字经济政策示例/ })).toBeVisible();
});
