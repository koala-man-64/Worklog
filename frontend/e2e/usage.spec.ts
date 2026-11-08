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
