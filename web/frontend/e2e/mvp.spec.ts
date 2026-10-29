import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

async function openNewResearch(page: import("@playwright/test").Page) {
  const newResearch = page.getByRole("button", { name: "新建研究" });
  if (await newResearch.count()) {
    await newResearch.first().click();
    await expect(page.getByRole("tablist", { name: "研究模式" })).toBeVisible();
  }
}

async function submitAndWait(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "开始研究" }).click();
  await expect(page.getByRole("status")).toContainText("正在准备研究结果");
  await expect(page.locator(".loading-panel")).toBeHidden({ timeout: 10_000 });
  await expect(page.locator(".results-content, .results-fallback").first()).toBeVisible();
}

async function chooseMode(page: import("@playwright/test").Page, mode: string) {
  await page.getByRole("tab", { name: mode }).click();
}

async function expectNoRootOverflow(page: import("@playwright/test").Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test("runs all five research modes, follows citations, and keeps session history", async ({ page }, testInfo) => {
  const artifactDirectory = process.env.PLAYWRIGHT_ARTIFACT_DIR ?? testInfo.outputDir;
  await mkdir(artifactDirectory, { recursive: true });
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await expectNoRootOverflow(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await expectNoRootOverflow(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoRootOverflow(page);
  await page.screenshot({ path: join(artifactDirectory, "home-390.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.screenshot({ path: join(artifactDirectory, "home-1440.png"), fullPage: true });

  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim().toLowerCase())).toBe("#b22924");
  await expect(page.locator("h1")).toHaveCSS("font-family", /STZhongsong|华文中宋/);
  await expect(page.locator("body")).toHaveCSS("font-family", /SimHei/);
  await expect(page.locator("body")).not.toContainText(/中国科学院大学|中国科学院|\bUCAS\b|校徽/);
  await expect(page.locator(".brand-mark img, img[alt*='校徽'], img[src*='logo' i], img[src*='badge' i]")).toHaveCount(0);
  const architecture = page.locator('.architecture-scene img[src="/research-architecture.png"]');
  await expect(architecture).toBeVisible();
  await expect.poll(() => architecture.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1000);
  const researchInput = page.getByLabel("研究问题");
  await researchInput.fill("示例：颜色规范检查");
  await expect(page.getByRole("button", { name: "开始研究" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "开始研究" })).toHaveCSS("background-color", "rgb(178, 41, 36)");
  await researchInput.fill("");

  await page.getByLabel("研究问题").fill("示例：演示人才政策支持方式");
  await submitAndWait(page);
  await expect(page.locator(".demo-badge")).toHaveText("演示数据");
  await page.getByRole("button", { name: /原文/ }).first().click();
  await expect(page.getByRole("complementary", { name: "引用证据" })).toContainText("证据详情");
  await page.getByRole("button", { name: "关闭证据面板" }).click();
  await page.getByRole("button", { name: /原文/ }).first().click();
  await expect(page.getByRole("complementary", { name: "引用证据" })).toContainText("证据详情");
  const scenarios: { mode: string; question: string; fill: () => Promise<void> }[] = [
    {
      mode: "要素提取",
      question: "示例：提取人才支持维度",
      fill: async () => {
        await page.getByLabel("政策编号").fill("DEMO-A");
        await page.getByLabel("补充要求").fill("示例：提取人才支持维度");
      },
    },
    {
      mode: "文献梳理",
      question: "示例：梳理人才支持",
      fill: async () => { await page.getByLabel("研究主题").fill("示例：梳理人才支持"); },
    },
    {
      mode: "政策比较",
      question: "比较以下政策对象：DEMO-A；DEMO-B",
      fill: async () => {
        await page.getByLabel("研究对象编号 1").fill("DEMO-A");
        await page.getByLabel("研究对象编号 2").fill("DEMO-B");
      },
    },
    {
      mode: "多方案分析",
      question: "示例：分析 DEMO-A 与 DEMO-B",
      fill: async () => {
        await page.getByLabel("研究对象编号 1").fill("DEMO-A");
        await page.getByLabel("研究对象编号 2").fill("DEMO-B");
        await page.getByLabel("自定义分析要求").fill("示例：分析 DEMO-A 与 DEMO-B");
      },
    },
  ];

  for (const item of scenarios) {
    await openNewResearch(page);
    await chooseMode(page, item.mode);
    await item.fill();
    await submitAndWait(page);
    await expect(page.getByRole("heading", { name: item.question, exact: false })).toBeVisible();
    if (item.mode === "政策比较") {
      await expectNoRootOverflow(page);
      await page.getByRole("button", { name: /原文/ }).first().click();
      await expect(page.getByRole("complementary", { name: "引用证据" })).toContainText("证据详情");
      await page.screenshot({ path: join(artifactDirectory, "workspace-comparison.png"), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await expectNoRootOverflow(page);
      await page.screenshot({ path: join(artifactDirectory, "mobile-390.png"), fullPage: true });
      await page.getByRole("button", { name: "关闭证据面板" }).click();
      await page.screenshot({ path: join(artifactDirectory, "mobile-390-closed.png"), fullPage: true });
      await page.setViewportSize({ width: 1440, height: 960 });
    }
  }

  await expect(page.locator(".history-link")).toHaveCount(5);
  await page.getByRole("button", { name: "研究历史" }).click();
  await expect(page.getByRole("dialog", { name: "研究历史" })).toBeVisible();
  await page.getByRole("dialog", { name: "研究历史" })
    .getByRole("button", { name: /示例：演示人才政策支持方式/ }).click();
  await expect(page.getByRole("heading", { name: "示例：演示人才政策支持方式" })).toBeVisible();
  expect(browserErrors).toEqual([]);

  await expectNoRootOverflow(page);
});

test("keeps the reference homepage geometry and photographic architecture at supported sizes", async ({ page }, testInfo) => {
  const artifactDirectory = process.env.PLAYWRIGHT_ARTIFACT_DIR ?? testInfo.outputDir;
  await mkdir(artifactDirectory, { recursive: true });
  const sizes = [
    { width: 305, height: 410, name: "home-305x410" },
    { width: 390, height: 540, name: "home-390x540" },
    { width: 390, height: 844, name: "home-390x844" },
    { width: 1440, height: 960, name: "home-1440x960" },
    { width: 1920, height: 1080, name: "home-1920x1080" },
  ];
  const widthChecks: { width: number; documentWidth: number }[] = [];

  for (const size of sizes) {
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.goto("/");
    await page.screenshot({ path: join(artifactDirectory, `${size.name}.png`) });
    widthChecks.push({
      width: size.width,
      documentWidth: await page.evaluate(() => document.documentElement.scrollWidth),
    });
  }

  await page.setViewportSize({ width: 305, height: 410 });
  const geometry = await page.evaluate(() => {
    const rect = (selector: string) => {
      const element = document.querySelector(selector);
      if (!element) return null;
      const bounds = element.getBoundingClientRect();
      return {
        x: Number(bounds.x.toFixed(1)),
        y: Number(bounds.y.toFixed(1)),
        width: Number(bounds.width.toFixed(1)),
        height: Number(bounds.height.toFixed(1)),
      };
    };
    return {
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      page: rect(".home-page"),
      navigation: rect(".home-topbar"),
      heading: rect(".intro-block h1"),
      search: rect(".research-composer"),
      architecture: rect(".architecture-scene"),
      modes: rect('[role="tablist"]'),
    };
  });
  await writeFile(
    join(artifactDirectory, "home-geometry-305.json"),
    JSON.stringify({ geometry, widthChecks }, null, 2),
  );

  for (const check of widthChecks) expect(check.documentWidth).toBe(check.width);
  expect(geometry.documentWidth).toBe(305);
  expect(geometry.page?.width).toBe(305);
  expect(geometry.navigation?.height).toBeCloseTo(58, 0);
  expect(geometry.heading?.y).toBeGreaterThanOrEqual(68);
  expect(geometry.heading?.y).toBeLessThanOrEqual(82);
  expect(geometry.search?.y).toBeGreaterThanOrEqual(163);
  expect(geometry.search?.y).toBeLessThanOrEqual(187);
  expect(geometry.architecture?.y).toBeGreaterThanOrEqual(195);
  expect(geometry.architecture?.y).toBeLessThanOrEqual(210);
  expect(geometry.architecture?.y + (geometry.architecture?.height ?? 0)).toBeCloseTo(361, 0);
  expect(geometry.modes?.y).toBeGreaterThanOrEqual(368);
  expect(geometry.modes?.y).toBeLessThanOrEqual(410);

  const architecture = page.locator('.architecture-scene img[src="/research-architecture.png"]');
  await expect(architecture).toBeVisible();
  await expect.poll(() => architecture.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1000);
  const navigation = page.getByRole("navigation", { name: "主导航" });
  await navigation.getByRole("button", { name: "研究" }).click();
  await expect(page.getByRole("tab", { name: "政策问答" })).toHaveAttribute("aria-selected", "true");
  await navigation.getByRole("button", { name: "文献" }).click();
  await expect(page.getByRole("tab", { name: "文献梳理" })).toHaveAttribute("aria-selected", "true");

  await navigation.getByRole("button", { name: "数据" }).click();
  await expect(page.getByRole("dialog", { name: "研究历史" })).toBeVisible();
  await page.getByRole("dialog", { name: "研究历史" }).getByRole("button", { name: "完成" }).click();
  await navigation.getByRole("button", { name: "关于" }).click();
  await expect(page.getByRole("dialog", { name: "关于管策智研" })).toBeVisible();
  await page.getByRole("dialog", { name: "关于管策智研" }).getByRole("button", { name: "完成" }).click();
  await page.getByRole("button", { name: "打开演示设置" }).click();
  await expect(page.getByRole("dialog", { name: "演示设置" })).toBeVisible();
  await page.getByRole("dialog", { name: "演示设置" }).getByRole("button", { name: "完成" }).click();

  const narrowSizes = [
    { width: 305, height: 410 },
    { width: 390, height: 540 },
  ];
  const formFields: { mode: string; labels: string[] }[] = [
    { mode: "政策问答", labels: ["研究问题"] },
    { mode: "要素提取", labels: ["政策编号", "补充要求"] },
    { mode: "文献梳理", labels: ["研究主题"] },
    { mode: "政策比较", labels: ["研究对象编号 1", "研究对象编号 2"] },
    { mode: "多方案分析", labels: ["研究对象编号 1", "研究对象编号 2", "自定义分析要求"] },
  ];

  for (const size of narrowSizes) {
    await page.setViewportSize(size);
    await page.goto("/");
    for (const item of formFields) {
      await page.getByRole("tab", { name: item.mode }).click();
      const modeBar = await page.getByRole("tablist").boundingBox();
      expect(modeBar).not.toBeNull();
      for (const label of item.labels) {
        const field = page.getByLabel(label);
        await expect(field).toBeVisible();
        await field.scrollIntoViewIfNeeded();
        await field.click();
        await expect(field).toBeFocused();
        const bounds = await field.boundingBox();
        const visibleModeBar = await page.getByRole("tablist").boundingBox();
        expect(bounds).not.toBeNull();
        expect(visibleModeBar).not.toBeNull();
        if (bounds && visibleModeBar) {
          expect(bounds.y + bounds.height).toBeLessThanOrEqual(visibleModeBar.y);
        }
      }
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
  }

  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  const literatureTab = page.getByRole("tab", { name: "文献梳理" });
  await literatureTab.focus();
  await literatureTab.press("Enter");
  await expect(literatureTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".workspace")).toHaveCount(0);
  const settingsButton = page.getByRole("button", { name: "打开演示设置" });
  await settingsButton.focus();
  await settingsButton.press("Enter");
  await expect(page.getByRole("dialog", { name: "演示设置" })).toBeVisible();
  await page.getByRole("dialog", { name: "演示设置" }).getByRole("button", { name: "完成" }).click();
  await page.getByRole("tab", { name: "政策问答" }).click();
  const question = page.getByLabel("研究问题");
  await question.fill("示例：普通Enter提交验证");
  await question.press("Enter");
  await expect(page.getByRole("status")).toContainText("正在准备研究结果");
  await expect(page.locator(".loading-panel")).toBeHidden({ timeout: 10_000 });
  await expect(page.locator(".history-link")).toHaveCount(1);

  await page.locator('.workspace-sidebar button[title="新建研究"]').click();
  await page.getByRole("tab", { name: "要素提取" }).click();
  await page.getByLabel("政策编号").fill("DEMO-A");
  await page.getByLabel("补充要求").fill("提取财政支持、人才支持两个维度");
  await page.getByLabel("补充要求").press("Control+Enter");
  await expect(page.getByRole("status")).toContainText("正在准备研究结果");
  await expect(page.locator(".loading-panel")).toBeHidden({ timeout: 10_000 });
  await expect(page.locator(".history-link")).toHaveCount(2);

  await page.getByRole("button", { name: "收起侧栏" }).click();
  await expect(page.locator(".workspace-sidebar")).toHaveClass(/is-collapsed/);
  await page.getByRole("button", { name: "展开侧栏" }).click();
  await expect(page.locator(".workspace-sidebar")).not.toHaveClass(/is-collapsed/);

  await page.locator('.workspace-sidebar button[title="新建研究"]').click();
  const composingQuestion = page.getByLabel("研究问题");
  await composingQuestion.fill("示例：输入法组合态Enter验证");
  await composingQuestion.evaluate((input: HTMLInputElement) => {
    input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    input.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
        code: "Enter",
        isComposing: true,
        bubbles: true,
        cancelable: true,
      }),
    );
    input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
  });
  await expect(page.locator(".workspace")).toHaveCount(0);
  await expect(composingQuestion).toHaveValue("示例：输入法组合态Enter验证");
});

test("validates duplicate identifiers and supports eight comparison targets", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "政策比较" }).click();
  await page.getByLabel("研究对象编号 1").fill("DEMO-A");
  await page.getByLabel("研究对象编号 2").fill("DEMO-A");
  await expect(page.getByRole("alert")).toContainText("不同的政策编号");
  await expect(page.getByRole("button", { name: "开始研究" })).toBeDisabled();
  await page.getByLabel("研究对象编号 2").press("Control+Enter");
  await expect(page.getByRole("tablist", { name: "研究模式" })).toBeVisible();
  await expect(page.locator(".workspace")).toHaveCount(0);

  await page.getByLabel("研究对象编号 2").fill("DEMO-B");
  for (const target of ["DEMO-C", "DEMO-D", "DEMO-E", "DEMO-F", "DEMO-G", "DEMO-H"]) {
    await page.getByRole("button", { name: "添加对象" }).click();
    const count = await page.locator(".target-row").count();
    await page.getByLabel(`研究对象编号 ${count}`).fill(target);
  }
  await expect(page.locator(".target-row")).toHaveCount(8);
  await expect(page.getByRole("button", { name: "添加对象" })).toBeDisabled();
  await submitAndWait(page);
  await expect(page.locator(".comparison-table thead th")).toHaveCount(10);
  await expect(page.locator(".comparison-table tbody tr")).toHaveCount(3);
});

test("preserves a draft on return home and clears it for new research", async ({ page }) => {
  await page.goto("/");
  const question = "示例：检查问题草稿保留";
  await page.getByLabel("研究问题").fill(question);
  await page.getByRole("tab", { name: "文献梳理" }).click();
  await page.getByLabel("研究主题").fill("示例：整理草稿主题");
  await page.getByRole("tab", { name: "政策问答" }).click();
  await expect(page.getByLabel("研究问题")).toHaveValue(question);

  await submitAndWait(page);
  await page.locator('.workspace-sidebar button[title="返回首页"]').click();
  await expect(page.getByLabel("研究问题")).toHaveValue(question);

  await submitAndWait(page);
  await page.locator('.workspace-sidebar button[title="新建研究"]').click();
  await expect(page.getByLabel("研究问题")).toHaveValue("");
});

test("shows loading and all four non-success result states", async ({ page }) => {
  await page.goto("/");
  const states = [
    { label: "证据不足", expected: "证据不足，当前仅展示部分示例" },
    { label: "无可用证据", expected: "本轮没有可展示的结果" },
    { label: "执行失败", expected: "演示请求失败" },
    { label: "达到执行限制", expected: "演示已达到处理上限" },
  ];

  for (const state of states) {
    await page.getByRole("button", { name: "设置" }).click();
    await page.getByRole("dialog", { name: "演示设置" }).getByLabel(state.label).check();
    await page.getByRole("button", { name: "完成" }).click();
    await page.getByLabel("研究问题").fill(`示例：检查${state.label}状态`);
    await submitAndWait(page);
    await expect(page.locator(".results-content, .results-fallback").first()).toContainText(state.expected);
    await openNewResearch(page);
  }
});
