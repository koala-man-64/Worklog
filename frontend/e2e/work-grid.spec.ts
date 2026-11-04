import { expect, test } from "@playwright/test";

test("the work list is a sheet whose rows scroll under a frozen header row and title column", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  expect(boot.ok()).toBeTruthy();
  const { csrfToken } = await boot.json();
  const prefix = "Sheet" + Date.now();
  for (let i = 0; i < 30; i++) {
    const result = await page.request.post("/api/v1/work-items", {
      headers: {
        Origin: origin,
        "X-CSRF-Token": csrfToken,
        "Idempotency-Key": crypto.randomUUID(),
      },
      data: { title: prefix + " " + String(i).padStart(2, "0") },
    });
    expect(result.ok()).toBeTruthy();
  }
  await page.goto("/#work");
  await page.getByRole("textbox", { name: "Filter work by text" }).fill(prefix);
  const sheet = page.getByRole("table", { name: "Work items" });
  const rows = sheet.locator(".work-row");
  await expect(rows).toHaveCount(30);
  await expect(page.getByText("30 work items", { exact: true })).toBeVisible();
  // A row is one line of controls tall, and the sheet scrolls rather than the page.
  const rowHeight = await rows
    .first()
    .evaluate((row) => row.getBoundingClientRect().height);
  expect(rowHeight).toBeLessThanOrEqual(48);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollHeight <= innerHeight,
    ),
  ).toBeTruthy();
  const body = page.locator(".work-grid-body");
  const header = sheet.getByRole("columnheader", { name: "Title" });
  const top = (await header.boundingBox())!.y;
  await body.evaluate((el) => el.scrollTo(0, el.scrollHeight));
  await expect(rows.last()).toBeInViewport();
  expect((await header.boundingBox())!.y).toBeCloseTo(top, 0);
  // Narrower than the sheet, it scrolls sideways and each row keeps its title in view.
  await page.setViewportSize({ width: 1000, height: 800 });
  await body.evaluate((el) => el.scrollTo(0, 0));
  const title = sheet.getByRole("rowheader").first();
  const left = (await title.boundingBox())!.x;
  await body.evaluate((el) => el.scrollTo(el.scrollWidth, 0));
  expect(await body.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
  expect((await title.boundingBox())!.x).toBeCloseTo(left, 0);
  // A phone restacks each row instead, and nothing runs past the screen.
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(rows.first()).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
