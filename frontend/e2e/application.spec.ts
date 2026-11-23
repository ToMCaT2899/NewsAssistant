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
  fixture.delivery.status = "accepted";
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(
    page.getByText("邮件状态：邮件服务器已接受", { exact: true }),
  ).toBeVisible();
});

test("聊天进度遇到 502 自动恢复并显示已发送结果，不重复提交（离线展示夹具）", async ({
  page,
}, info) => {
  await page.route("**/api/v1/status", (route) =>
    route.fulfill({
      json: {
        model_configured: true,
        mail_configured: true,
        news_provider: "offline-display-fixture",
      },
    }),
  );
  await page.route("**/api/v1/subscription", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ json: { ...data, recipient_verified: true } });
  });
  const thread = {
    id: "chat-fixture",
    title: "离线对话",
    created_at: "2026-11-23T01:00:00Z",
  };
  await page.route("**/api/v1/chat/sessions", (route) =>
    route.fulfill({ json: [thread] }),
  );
  let stage = 0;
  let submitted = 0;
  let sent = false;
  await page.route(
    "**/api/v1/chat/sessions/chat-fixture/turns",
    async (route) => {
      if (route.request().method() === "POST") {
        submitted++;
        expect(route.request().postDataJSON()).toEqual({
          message: "重点关注开源工具",
          action: "generate_send",
        });
        expect(route.request().headers()["idempotency-key"]).toBeTruthy();
        sent = true;
        await route.fulfill({ json: { id: "turn-fixture" } });
        return;
      }
      if (!sent) {
        await route.fulfill({ json: [] });
        return;
      }
      stage++;
      if (stage === 2) {
        await route.fulfill({
          status: 502,
          contentType: "text/html",
          body: "Bad Gateway",
        });
        return;
      }
      const done = stage >= 3;
      await route.fulfill({
        json: [
          {
            id: "turn-fixture",
            question: "重点关注开源工具",
            action: "generate_send",
            status: done ? "succeeded" : "running",
            answer: done
              ? "<script>window.pwned=1</script>新的简报已生成。"
              : null,
            error_code: null,
            run_id: "run-fixture",
            created_at: thread.created_at,
            tools: [
              {
                id: "tool-fixture",
                sequence: 1,
                tool_name: "fetch_article",
                arguments: {},
                status: done ? "succeeded" : "running",
                duration_ms: done ? 2200 : 0,
                error_code: null,
              },
            ],
            brief: done
              ? {
                  id: "new-brief",
                  title: "离线新版本样例",
                  overview: "仅用于显示测试，不是真实新闻。",
                  edition: 2,
                  business_date: "2026-11-23",
                  no_news: false,
                  window_start: "2026-11-22T00:00:00Z",
                  window_end: thread.created_at,
                  items: [
                    {
                      headline: "开源工具测试条目",
                      summary: "正文按文本显示",
                      why_it_matters: "工具进度可见",
                      sources: [
                        {
                          title: "测试原文",
                          url: "https://example.com/news",
                          source_name: "fixture",
                          published_at: thread.created_at,
                          evidence_level: "full_text",
                        },
                      ],
                    },
                  ],
                  delivery: {
                    id: "mail-fixture",
                    status: "accepted",
                    attempt_count: 1,
                    last_error_code: null,
                  },
                }
              : null,
          },
        ],
      });
    },
  );
  await signIn(page);
  await page.getByRole("button", { name: "新闻助手", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "新闻助手", exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("发送给新闻助手", { exact: true })
    .fill("重点关注开源工具");
  await page
    .getByLabel("本次操作", { exact: true })
    .selectOption("generate_send");
  await page
    .getByRole("button", { name: "生成并推送", exact: true })
    .last()
    .click();
  await expect(page.getByText("正在调用", { exact: true })).toBeVisible();
  await page.screenshot({
    path: info.outputPath("chat-running.png"),
    fullPage: true,
  });
  await expect(
    page.getByText("进度连接暂时中断", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("上次记录：调用中（待同步）", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "发送消息", exact: true }),
  ).toBeDisabled();
  await expect(page.getByText("调用完成", { exact: true })).toBeVisible();
  await expect(
    page.getByText("进度连接暂时中断", { exact: false }),
  ).toHaveCount(0);
  expect(submitted).toBe(1);
  await expect(
    page.getByRole("heading", { name: "离线新版本样例" }),
  ).toBeVisible();
  await expect(
    page.getByText("QQ 邮件：邮件服务器已接受", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "测试原文", exact: false }),
  ).toHaveAttribute("href", "https://example.com/news");
  expect(await page.evaluate(() => "pwned" in window)).toBe(false);
  await page.screenshot({
    path: info.outputPath("chat-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 375, height: 812 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
  ).toBe(false);
  await page.screenshot({
    path: info.outputPath("chat-mobile.png"),
    fullPage: true,
  });
});

test("连续断线达到重试上限后可手动恢复，只重试进度查询", async ({ page }) => {
  test.setTimeout(60000);
  const thread = {
    id: "disconnected",
    title: "断线恢复测试",
    created_at: "2026-11-23T01:00:00Z",
  };
  await page.route("**/api/v1/chat/sessions", (route) =>
    route.fulfill({ json: [thread] }),
  );
  let reads = 0;
  let writes = 0;
  let recovered = false;
  await page.route(
    "**/api/v1/chat/sessions/disconnected/turns",
    async (route) => {
      if (route.request().method() !== "GET") {
        writes++;
        await route.fulfill({ json: { id: "unexpected-write" } });
        return;
      }
      reads++;
      if (reads > 1 && !recovered) {
        await route.fulfill({
          status: 503,
          contentType: "text/html",
          body: "Unavailable",
        });
        return;
      }
      await route.fulfill({
        json: [
          {
            id: "saved-turn",
            question: "读取我的订阅",
            action: "chat",
            status: recovered ? "succeeded" : "running",
            answer: recovered ? "连接已恢复，任务已完成。" : null,
            created_at: thread.created_at,
            error_code: null,
            run_id: null,
            brief: null,
            tools: [
              {
                id: "read-prefs",
                sequence: 1,
                tool_name: "read_preferences",
                arguments: {},
                status: recovered ? "succeeded" : "running",
                duration_ms: 0,
                error_code: null,
              },
            ],
          },
        ],
      });
    },
  );
  await signIn(page);
  await page.getByRole("button", { name: "新闻助手", exact: true }).click();
  await expect(page.getByText("正在调用", { exact: true })).toBeVisible();
  await expect(
    page.getByText("暂未恢复进度连接，请点击", { exact: false }),
  ).toBeVisible({ timeout: 50000 });
  expect(reads).toBe(8);
  await page.waitForTimeout(1300);
  expect(reads).toBe(8);
  await expect(
    page.getByLabel("发送给新闻助手", { exact: true }),
  ).toBeDisabled();
  recovered = true;
  await page.getByRole("button", { name: "刷新对话", exact: true }).click();
  await expect(
    page.getByText("连接已恢复，任务已完成。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("调用完成", { exact: true })).toBeVisible();
  await expect(
    page.getByLabel("发送给新闻助手", { exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByText("暂未恢复进度连接，请点击", { exact: false }),
  ).toHaveCount(0);
  expect(writes).toBe(0);
});
