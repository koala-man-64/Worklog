import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

const externalRequests = new WeakMap<BrowserContext, string[]>();
test.beforeEach(async ({ context, baseURL }) => {
  const blocked: string[] = [];
  externalRequests.set(context, blocked);
  const origin = new URL(baseURL!).origin;
  await context.route(/^https?:\/\//, (route) => {
    if (new URL(route.request().url()).origin === origin)
      return route.continue();
    blocked.push(route.request().url());
    return route.abort("internetdisconnected");
  });
});
test.afterEach(async ({ context }) => {
  expect(
    externalRequests.get(context),
    "No external runtime resources",
  ).toEqual([]);
});
test.use({ permissions: ["clipboard-read", "clipboard-write"] });

async function addWork(page: Page, title: string) {
  await page.getByRole("textbox", { name: "New work item" }).fill(title);
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  const row = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: title, exact: true }) });
  await expect(row).toBeVisible();
  return row;
}
async function record(row: Locator, outcome: string, worked: boolean) {
  if (worked) {
    await row.getByLabel("Worked on", { exact: true }).click();
    await expect(row.getByLabel("Worked on", { exact: true })).toBeChecked();
  }
  await row
    .getByRole("textbox", { name: "Outcome / next action" })
    .fill(outcome);
  await row.getByRole("button", { name: "Save now" }).click();
  await expect(row.getByRole("status")).toHaveText("Saved");
}
// The status heading over the summary's list that holds this title, or null when it isn't there.
const groupOf = (summary: Locator, title: string) =>
  summary.evaluate((section, wanted) => {
    const strong = [...section.querySelectorAll("li strong")].find(
      (node) => node.textContent === wanted,
    );
    return strong?.closest("ul")?.previousElementSibling?.textContent ?? null;
  }, title);

test("the day's summary groups the work done by status and copies it for a standup", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  // Other specs plan work on the same day, so everything here is found by its own words.
  const suffix = crypto.randomUUID().slice(0, 8);
  const shipped = `Summary shipped ${suffix}`;
  const stuck = `Summary stuck ${suffix}`;
  const skipped = `Summary skipped ${suffix}`;
  const shippedRow = await addWork(page, shipped);
  await record(shippedRow, `Merged and deployed ${suffix}`, true);
  await shippedRow
    .getByRole("combobox", { name: "Status" })
    .selectOption("done");
  const stuckRow = await addWork(page, stuck);
  await record(stuckRow, `Waiting on credentials ${suffix}`, true);
  await stuckRow
    .getByRole("combobox", { name: "Status" })
    .selectOption("blocked");
  await record(await addWork(page, skipped), `Not reached ${suffix}`, false);

  const summary = page.getByRole("region", { name: "The day’s summary" });
  await expect.poll(() => groupOf(summary, shipped)).toBe("Done");
  await expect.poll(() => groupOf(summary, stuck)).toBe("Blocked");
  await expect(summary).toContainText(`Merged and deployed ${suffix}`);
  await expect(summary).not.toContainText(skipped);

  await summary.getByRole("button", { name: "Copy summary" }).click();
  await expect(summary.getByRole("status")).toHaveText("Copied");
  // Windows' clipboard hands text back with CRLF line breaks.
  const markdown = (
    await page.evaluate(() => navigator.clipboard.readText())
  ).replace(/\r\n/g, "\n");
  expect(markdown).toContain(
    `- **${shipped}**\n\n  Merged and deployed ${suffix}`,
  );
  expect(markdown).toContain(
    `- **${stuck}**\n\n  Waiting on credentials ${suffix}`,
  );
  expect(markdown).not.toContain(skipped);
  const html = await page.evaluate(async () => {
    const [item] = await navigator.clipboard.read();
    return (await item.getType("text/html")).text();
  });
  // The browser may add attributes to the HTML it hands back; the markup stays.
  expect(html).toMatch(new RegExp(`<strong[^>]*>${shipped}</strong>`));
  expect(html).toMatch(/<h3[^>]*>Blocked<\/h3>/);
});
