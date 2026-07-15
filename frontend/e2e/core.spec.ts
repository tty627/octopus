import { expect, test } from "@playwright/test";

test("search evidence and task pack form a complete local-first loop", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "今天要从资料里完成什么？" })).toBeVisible();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "AI 服务" })).toBeVisible();
  await page.getByRole("textbox", { name: "API Key", exact: true }).fill("test-api-key");
  await page.getByRole("checkbox", { name: /在当前资料空间启用 AI/ }).check();
  await page.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(page.getByText("连接成功", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.getByText("AI 设置已保存。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByLabel("启用 AI 任务辅助")).toBeChecked();
  await page.getByLabel("查找资料或描述任务").fill("准备新能源项目季度汇报");
  await page.getByRole("button", { name: "搜索", exact: true }).last().click();
  await expect(page.getByRole("region", { name: "AI 建议" })).toBeVisible();
  await expect(page.getByText("项目季度进展汇报.pdf").first()).toBeVisible();
  await expect(page.getByText(/本地结果已就绪|找到 6 项资料/)).toBeVisible();
  await page.getByRole("button", { name: "项目季度进展汇报.pdf" }).first().click();
  await expect(page.getByRole("complementary", { name: "证据检查器" })).toContainText("第 4 页");
  await page.locator(".inspectorActions").getByRole("button", { name: "加入任务包", exact: true }).click();
  await expect(page.getByText(/1 项资料/).last()).toBeVisible();
  await page.getByRole("button", { name: "任务包", exact: true }).click();
  await expect(page.getByLabel("任务包名称")).toHaveValue("准备新能源项目季度汇报");
  await expect(page.getByText("项目季度进展汇报.pdf").last()).toBeVisible();
  await expect(page.getByText(/已保存|保存中/).first()).toBeVisible();
});

test("responsive layouts remain usable without overflow", async ({ page }) => {
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
    await page.screenshot({ path: `test-results/octopus-${viewport.name}.png`, fullPage: true });
  }
});

test("narrow view uses an evidence drawer", async ({ page }) => {
  await page.setViewportSize({ width: 980, height: 720 });
  await page.goto("./");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByLabel("查找资料或描述任务").fill("预算审批");
  await page.getByRole("button", { name: "搜索", exact: true }).last().click();
  await page.getByText("项目预算总表.xlsx").first().click();
  const inspector = page.getByRole("complementary", { name: "证据检查器" });
  await expect(inspector).toHaveClass(/inspectorOpen/);
  await expect(page.getByRole("button", { name: "关闭证据检查器" })).toBeVisible();
});
