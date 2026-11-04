import { expect, test } from "@playwright/test";

test("choosing the search result for the page already open closes search on that page", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
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
  // A word of this run's own, so search finds only the item made here.
  const title = `Wombat${Date.now()} renews the vendor contract`;
  const created = await page.request.post("/api/v1/work-items", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: { title },
  });
  expect(created.ok()).toBeTruthy();
  const item = await created.json();

  await page.goto(`/#work/${item.id}`);
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  const box = page.getByRole("searchbox", { name: "Search all work" });
  await box.fill(title.split(" ")[0]);
  const results = page.getByRole("heading", { name: "Search", exact: true });
  await expect(results).toBeVisible();
  // The result points at the page already open, so choosing it changes no hash.
  await page.getByRole("link", { name: title }).click();
  await expect(results).toBeHidden();
  await expect(box).toHaveValue("");
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
});
