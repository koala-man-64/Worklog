import { expect, test } from "@playwright/test";

test("each page the person opens counts once, by its name alone", async ({
  page,
}) => {
  // Requests are issued in the order the pages open, so a count made for a move within a page
  // would show here before the next page's.
  const views: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v1/page-views"
    )
      views.push(JSON.parse(request.postData() ?? "{}").page);
  });
  const accepted = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/page-views",
  );
  await page.goto("/#work");
  expect((await accepted).status()).toBe(200);
  await expect.poll(() => views).toEqual(["work"]);
  await page.evaluate(() => {
    location.hash = "#today";
  });
  await expect.poll(() => views).toEqual(["work", "today"]);
  // Another day on Today is not another visit to it.
  await page.evaluate(() => {
    location.hash = "#today/2026-01-05";
  });
  await expect(page.getByText("2026-01-05").first()).toBeVisible();
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "People", exact: true })
    .click();
  await expect.poll(() => views).toEqual(["work", "today", "people"]);
});

test("the Usage page shows the pages just opened, opens an area onto its calls, and counts yours or agents' uses alone on asking", async ({
  page,
}) => {
  const counted = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/page-views",
  );
  await page.goto("/#work");
  expect((await counted).status()).toBe(200);
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Usage", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Usage", exact: true }),
  ).toBeVisible();
  // The server writes its counts every minute, and the page shows those it holds until then:
  // the visit to Work just now shows as a count, not as a page nothing opened.
  const work = page
    .getByRole("region", { name: "Pages" })
    .getByRole("listitem")
    .filter({ hasText: /^Work/ });
  await expect(work).toHaveText(/^Work[\d,.K]+$/);
  await page.getByRole("button", { name: "7 days" }).click();
  await expect(page.getByRole("button", { name: "7 days" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    page.getByRole("heading", { name: "Page views per day" }),
  ).toBeVisible();
  const area = page
    .getByRole("region", { name: "Features by area" })
    .locator("details")
    .filter({ has: page.locator("summary", { hasText: "/work-items" }) });
  await area.locator("summary").click();
  await expect(
    area.getByRole("row", { name: /^GET \/work-items/ }).first(),
  ).toBeVisible();
  await expect(
    area.getByRole("columnheader", { name: "Agents", exact: true }),
  ).toBeVisible();
  const answered = (actor: string) =>
    page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === "/api/v1/usage" &&
        url.searchParams.get("actor") === actor
      );
    });
  // Your uses alone: agents' column goes once the answer comes.
  const yours = answered("person");
  await page.getByRole("button", { name: "You", exact: true }).click();
  expect((await yours).status()).toBe(200);
  await expect(
    area.getByRole("columnheader", { name: "Agents", exact: true }),
  ).toHaveCount(0);
  await expect(
    area.getByRole("columnheader", { name: "You", exact: true }),
  ).toBeVisible();
  // Agents' alone: your column goes, and so do page views, which only you make.
  const theirs = answered("agent");
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  expect((await theirs).status()).toBe(200);
  await expect(
    area.getByRole("columnheader", { name: "You", exact: true }),
  ).toHaveCount(0);
  await expect(
    area.getByRole("columnheader", { name: "Agents", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Only you open pages, so agents have no page views."),
  ).toBeVisible();
});
