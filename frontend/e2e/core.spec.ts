import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("search, page preview, task collection and export form a V2 loop", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("./");
  await page.locator(".sideNav").getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByRole("heading", { name: "查找原始资料" })).toBeVisible();
  await page.getByRole("textbox", { name: "搜索原始资料" }).fill("微分方程");
  await page.locator(".primarySearch").getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByRole("button", { name: "微分方程coursenotes.pdf", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "常微分方程复习提纲.txt", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "微分方程coursenotes.pdf", exact: true }).click();

  const inspector = page.getByRole("complementary", { name: "证据检查器" });
  await expect(inspector).toContainText("第 4 / 36 页");
  await expect(inspector.getByRole("img", { name: /第 4 页/ })).toBeVisible();
  await expect(inspector).toContainText("常微分方程的基本概念");
  await inspector.getByRole("button", { name: "加入资料包", exact: true }).click();
  await expect(page.locator(".taskTray")).toContainText("1 条证据");
  await page.getByRole("button", { name: "将 常微分方程复习提纲.txt 加入资料包" }).click();
  await expect(page.locator(".taskTray")).toContainText("2 条证据");

  await page.getByRole("button", { name: "资料包", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "资料包名称" })).toHaveValue("微分方程");
  await expect(page.getByText("微分方程coursenotes.pdf", { exact: true })).toBeVisible();
  await expect(page.getByText("常微分方程复习提纲.txt", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "资料包名称" }).fill("微分方程核对结果");
  await page.getByRole("textbox", { name: "研究目标" }).fill("包含刚刚加入的两条证据");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出研究包", exact: true }).click();
  const exported = await download;
  expect(exported.suggestedFilename()).toBe("微分方程核对结果.zip");
  const exportedPath = await exported.path();
  expect(exportedPath).not.toBeNull();
  const archiveMock = await readFile(exportedPath, "utf8");
  expect(archiveMock).toContain("research.md");
  expect(archiveMock).toContain("references.bib");
  expect(archiveMock).toContain("微分方程核对结果");
});

test("search results contain human evidence and no internal V1 fields", async ({ page }) => {
  await page.goto("./");
  await page.locator(".sideNav").getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByRole("textbox", { name: "搜索原始资料" }).fill("级数");
  await page.locator(".primarySearch").getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByRole("button", { name: "09 级数.pdf", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "09 级数.pdf", exact: true }).getByText(/数项级数、正项级数、幂级数/)).toBeVisible();
  const visible = await page.locator("body").innerText();
  expect(visible).not.toMatch(/exact_name|folder_child|summary_layer|Octopus-Index/);
  expect(visible).not.toContain("锟");
});

test("documents health, reprocess and explicit vision authorization are usable", async ({ page }) => {
  await page.goto("./");
  await page.getByRole("button", { name: "资料", exact: true }).click();
  await expect(page.getByRole("heading", { name: "资料", exact: true })).toBeVisible();
  await expect(page.getByText("正文可读").first()).toBeVisible();
  await page.getByRole("button", { name: "重新处理 09 级数.pdf" }).click();
  await expect(page.getByText("后台处理完成，资料状态已更新。")).toBeVisible();

  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "页面图像授权" })).toBeVisible();
  await page.getByLabel("服务预设").selectOption("glm");
  await page.getByLabel("模型").fill("glm-4.6v");
  await page.getByLabel("API Key").fill("e2e-test-key");
  await page.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(page.getByText("连接成功", { exact: true })).toBeVisible();
  const vision = page.getByRole("checkbox", { name: "允许发送明确选择的单页图像" });
  await expect(vision).not.toBeChecked();
  await vision.check();
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.getByText("设置已保存。", { exact: true })).toBeVisible();
});

test("responsive layouts remain usable without horizontal overflow", async ({ page }) => {
  for (const viewport of [
    { width: 1440, height: 900, name: "1440x900" },
    { width: 1100, height: 720, name: "1100x720" },
    { width: 980, height: 720, name: "980x720" },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("./");
    await expect(page.locator(".appShell")).toBeVisible();
    const metrics = await page.evaluate(() => ({
      bodyWidth: document.body.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      hasVisibleText: document.body.innerText.trim().length > 80,
    }));
    expect(metrics.hasVisibleText).toBe(true);
    expect(metrics.bodyWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1);
    await page.screenshot({ path: `test-results/octopus-v2-${viewport.name}.png`, fullPage: true });
  }
});

test("narrow view uses a page evidence drawer", async ({ page }) => {
  await page.setViewportSize({ width: 980, height: 720 });
  await page.goto("./");
  await page.locator(".sideNav").getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByRole("textbox", { name: "搜索原始资料" }).fill("微分方程");
  await page.locator(".primarySearch").getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByRole("button", { name: "微分方程coursenotes.pdf", exact: true }).click();
  const inspector = page.getByRole("complementary", { name: "证据检查器" });
  await expect(inspector).toHaveClass(/inspectorOpen/);
  await expect(page.getByRole("button", { name: "关闭证据检查器" })).toBeVisible();
});

test("home, templates and archive-member evidence are exposed as research workflows", async ({ page }) => {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "研究工作台" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "来源变化" })).toBeVisible();

  await page.getByRole("button", { name: "资料包", exact: true }).click();
  await page.getByRole("radio", { name: /课程报告/ }).click();
  await expect(page.getByRole("textbox", { name: "资料包名称" })).toHaveValue("新的课程报告");
  await page.getByRole("button", { name: "创建资料包" }).click();
  await expect(page.getByRole("textbox", { name: "核心论点 分组名称" })).toBeVisible();

  await page.locator(".sideNav").getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByRole("textbox", { name: "搜索原始资料" }).fill("研究");
  await page.locator(".primarySearch").getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText("Office 正文").first()).toBeVisible();
  await expect(page.getByText("图片 OCR").first()).toBeVisible();
  await page.getByRole("button", { name: "归档论文.pdf", exact: true }).click();
  const inspector = page.getByRole("complementary", { name: "证据检查器" });
  await expect(inspector).toContainText("ZIP 内文件");
  await expect(inspector).toContainText("课程材料.zip!/论文/归档论文.pdf");
});
