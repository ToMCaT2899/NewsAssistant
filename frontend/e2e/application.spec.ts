import { expect, test } from "@playwright/test";

async function signIn(page: import("@playwright/test").Page) {
  const password = process.env.E2E_PASSWORD;
  if (!password)
    throw new Error(
      "Run with python scripts/browser_smoke.py; no default credentials",
    );
  await page.goto("/");
  await page
    .getByLabel("邮箱", { exact: true })
    .fill("browser-test@example.com");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的简报" })).toBeVisible();
}

test("real API login, subscription save and queued task; no live model or mail", async ({
  page,
}, info) => {
  await signIn(page);
  await expect(page.getByText("你的第一份简报，等待开始。")).toBeVisible();
  await page.getByRole("button", { name: "订阅偏好", exact: true }).click();
  await page.getByLabel("身份与关注背景").fill("关注开源模型的 Python 开发者");
  await page.getByLabel("感兴趣的话题").fill("开源模型，AI 编程");
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("订阅已保存");
  await page.getByRole("button", { name: "立即整理", exact: true }).click();
  await expect(page.getByRole("heading", { name: "整理进度" })).toBeVisible();
  await expect(page.getByText("等待生成", { exact: true })).toBeVisible();
  await page.locator(".task-row").first().click();
  await expect(page.getByRole("heading", { name: "执行记录" })).toBeVisible();
  await page.screenshot({
    path: info.outputPath("desktop-task.png"),
    fullPage: true,
  });
});

test.describe("mobile layout", () => {
  test.use({ viewport: { width: 375, height: 812 } });
  test("subscription remains usable without horizontal overflow", async ({
    page,
  }, info) => {
    await signIn(page);
    await page.getByRole("button", { name: "订阅偏好", exact: true }).click();
    await expect(page.getByLabel("身份与关注背景")).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    expect(overflow).toBe(false);
    await page.screenshot({
      path: info.outputPath("mobile-subscription.png"),
      fullPage: true,
    });
  });
});

test("offline display fixture: escaped article, evidence label and explicit unknown-mail retry", async ({
  page,
}, info) => {
  const fixture = {
    id: "display-fixture",
    title: "离线展示样例（仅测试）",
    overview: "此内容仅用于页面测试，不是真实新闻。",
    business_date: "2026-11-22",
    no_news: false,
    created_at: "2026-11-22T00:00:00.000Z",
    window_start: "2026-11-21T00:00:00.000Z",
    window_end: "2026-11-22T00:00:00.000Z",
    items: [
      {
        headline: "<script>window.pwned=1</script>",
        summary: "正文以文本呈现。",
        why_it_matters: "验证引用阅读与安全渲染。",
        sources: [
          {
            title: "测试来源",
            url: "https://example.com/source",
            source_name: "fixture",
            published_at: "2026-11-21T10:00:00.000Z",
            evidence_level: "search_excerpt",
          },
        ],
      },
    ],
    delivery: {
      id: "mail-fixture",
      status: "unknown",
      attempt_count: 1,
      last_error_code: "SMTP_RESULT_UNKNOWN",
    },
  };
  await page.route(/\/api\/v1\/briefs(?:\/[^?]+)?(?:\?.*)?$/, async (route) => {
    const detail = route.request().url().endsWith("/display-fixture");
    await route.fulfill({ json: detail ? fixture : [fixture] });
  });
  let retries = 0;
  await page.route("**/api/v1/deliveries/mail-fixture/retry", async (route) => {
    const body = route.request().postDataJSON() as {
      confirm_unknown: boolean;
      reason: string;
    };
    expect(body.confirm_unknown).toBe(true);
    expect(body.reason.length).toBeGreaterThanOrEqual(10);
    retries++;
    fixture.delivery.status = "pending";
    await route.fulfill({ json: { id: "mail-fixture", status: "pending" } });
  });
  await signIn(page);
  await expect(
    page.getByText("离线展示样例（仅测试）", { exact: true }).last(),
  ).toBeVisible();
  await expect(page.locator(".news-item h3")).toHaveText(
    "<script>window.pwned=1</script>",
  );
  expect(await page.evaluate(() => "pwned" in window)).toBe(false);
  await expect(page.getByText("仅摘要证据", { exact: true })).toBeVisible();
  const retry = page.getByRole("button", { name: "重新发送", exact: true });
  await expect(retry).toBeDisabled();
  await page.getByLabel("我已核查，并确认重新发送").check();
  await page
    .getByLabel("核查与重试原因")
    .fill("已核查测试发件箱，明确允许重发。");
  await expect(retry).toBeEnabled();
  await page.screenshot({
    path: info.outputPath("offline-reading-fixture.png"),
    fullPage: true,
  });
  await retry.click();
  await expect(
    page.getByText("邮件状态：等待发送", { exact: true }),
  ).toBeVisible();
  expect(retries).toBe(1);
});
