import { expect, test, type BrowserContext } from "@playwright/test";

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

test("the person plans a release: its work and changes, who to tell, what to test and what to prepare", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  // Data persists between local runs, so everything here is found by its own words.
  const suffix = crypto.randomUUID().slice(0, 8);
  const work = `Export button ${suffix}`;
  const pr = `Add the export button ${suffix}`;
  const dated = `November release ${suffix}`;
  const undated = `Next platform release ${suffix}`;
  const tell = `Support wombat${suffix}`;

  // A work item with the PR an agent opened for it, written before the page opens: the page and
  // these requests share one cookie jar, so a session started here while the page starts its own
  // would leave one of them saving with a stale CSRF token.
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  const write = (path: string, data: unknown) =>
    page.request.post("/api/v1" + path, {
      headers: {
        Origin: origin,
        "X-CSRF-Token": csrfToken,
        "Idempotency-Key": crypto.randomUUID(),
      },
      data,
    });
  const created = await write("/work-items", { title: work });
  expect(created.ok(), await created.text()).toBeTruthy();
  const item = await created.json();
  const ref = await write("/github", {
    ownerType: "item",
    ownerId: item.id,
    kind: "pr",
    url: "https://github.com/acme/app/pull/7",
    title: pr,
  });
  expect(ref.ok(), await ref.text()).toBeTruthy();

  // Two releases: one not dated yet, and one with its date.
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Releases", exact: true })
    .click();
  await expect(page).toHaveURL(/#releases$/);
  await page.getByRole("textbox", { name: "New release title" }).fill(undated);
  await page.getByRole("button", { name: "Add release" }).click();
  await expect(
    page.getByRole("heading", { name: undated, level: 1 }),
  ).toBeVisible();
  await page.getByRole("link", { name: "All releases" }).click();
  await page.getByRole("textbox", { name: "New release title" }).fill(dated);
  await page.getByLabel("Release date").fill("2099-11-20");
  await page.getByRole("button", { name: "Add release" }).click();
  await expect(
    page.getByRole("heading", { name: dated, level: 1 }),
  ).toBeVisible();

  // The work item's PR shows under Changes once the release holds the work item.
  await page.getByRole("button", { name: "Add work item" }).click();
  await page.getByRole("textbox", { name: "Work to add search" }).fill(suffix);
  await page
    .getByRole("list", { name: "Work to add matches" })
    .getByRole("button", { name: new RegExp(work) })
    .click();
  const changes = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: /^Changes/ }) });
  await expect(
    changes.getByRole("link", { name: new RegExp(pr) }),
  ).toHaveAttribute("href", "https://github.com/acme/app/pull/7");

  // One check of each kind; telling support is done, and the firewall request waits on another team.
  for (const [label, text, add] of [
    ["New person or group to tell", tell, "Add who to tell"],
    ["New test", "Export a 10,000-row CSV", "Add test"],
    [
      "New preparation",
      "Request the outbound firewall rule",
      "Add preparation",
    ],
  ]) {
    await page.getByRole("textbox", { name: label }).fill(text);
    await page.getByRole("button", { name: add }).click();
    await expect(page.getByRole("article", { name: text })).toBeVisible();
  }
  const told = page.getByRole("article", { name: tell });
  await told.getByRole("combobox", { name: "Status" }).selectOption("done");
  await expect(told.getByRole("combobox", { name: "Status" })).toHaveValue(
    "done",
  );
  const request = page.getByRole("article", {
    name: "Request the outbound firewall rule",
  });
  await request
    .getByRole("combobox", { name: "Status" })
    .selectOption("waiting");
  await expect(request.getByRole("combobox", { name: "Status" })).toHaveValue(
    "waiting",
  );

  // The list leads with the dated release and counts what is still open.
  await page.getByRole("link", { name: "All releases" }).click();
  await page.getByRole("searchbox", { name: "Find releases" }).fill(suffix);
  const row = page
    .getByRole("article")
    .filter({ has: page.getByRole("link", { name: dated }) });
  await expect(
    row.getByText("0 of 1 work item done · 1 to test · 1 to prepare"),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Not dated" })).toBeVisible();

  // The work item lists the release that holds it, and joins the other from its own page.
  await page.goto(`/#work/${item.id}`);
  const releases = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: /^Releases/ }) });
  await expect(releases.getByRole("link", { name: dated })).toBeVisible();
  await releases
    .getByRole("combobox", { name: "Release to add to" })
    .selectOption({ label: `${undated} · Not dated` });
  await releases.getByRole("button", { name: "Add to release" }).click();
  await expect(releases.getByRole("link", { name: undated })).toBeVisible();

  // Search finds a check by its words and opens its release.
  await page.goto("/#today");
  await page
    .getByRole("searchbox", { name: "Search all work" })
    .fill(`wombat${suffix}`);
  await page.getByRole("link", { name: tell }).click();
  await expect(
    page.getByRole("heading", { name: dated, level: 1 }),
  ).toBeVisible();
});
