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

test("the person writes a reference page that agents read but cannot change", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  // Data persists between local runs, so everything here is found by its own words.
  const suffix = crypto.randomUUID().slice(0, 8);
  const title = `Release checks ${suffix}`;
  const clue = `pangolin${suffix}`;
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Resources", exact: true })
    .click();
  await expect(page).toHaveURL(/#resources$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Resources", exact: true }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: "New page title" }).fill(title);
  await page.getByRole("button", { name: "Add page", exact: true }).click();
  // The new page opens in its editor.
  const body = page.getByRole("textbox", { name: "Page (Markdown)" });
  await expect(body).toBeVisible();
  await expect(page).toHaveURL(/#resources\/[0-9a-f-]{36}$/);
  const id = page.url().split("/").at(-1)!;
  await page.getByRole("textbox", { name: "Summary" }).fill("Before every tag");
  await body.fill(
    `## Checks\n\n- Run the ${clue} suite twice\n\nSee [Afterwards](#afterwards).\n\n## Afterwards\n\nWatch the pipeline.`,
  );
  await page.getByRole("button", { name: "Done", exact: true }).click();
  const pane = page.getByRole("region", { name: "Page" });
  await expect(
    pane.getByRole("button", { name: "Edit page", exact: true }),
  ).toBeVisible();
  await expect(
    pane.getByRole("heading", { level: 2, name: "Checks", exact: true }),
  ).toBeVisible();
  await expect(pane.getByText(`Run the ${clue} suite twice`)).toBeVisible();
  // A link to one of the page's own headings opens that section of this page.
  await pane.getByRole("link", { name: "Afterwards", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`#resources/${id}/afterwards$`));
  await expect(
    pane.getByRole("heading", { name: "Afterwards", exact: true }),
  ).toBeInViewport();
  // The saved page survives a reload, and the list finds it by its text.
  await page.reload();
  await page.getByRole("searchbox", { name: "Find pages" }).fill(clue);
  const listed = page
    .getByRole("region", { name: "Pages" })
    .getByRole("link", { name: new RegExp(title) });
  await expect(listed).toHaveAttribute("href", "#resources/" + id);
  await expect(listed).toContainText("Before every tag");

  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  const issued = await page.request.post("/api/v1/credentials", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: { label: "Resource reader " + suffix },
  });
  const { token } = await issued.json();
  const agent = { Authorization: "Bearer " + token };
  const read = await page.request.get(`/api/v1/resources/${id}`, {
    headers: agent,
  });
  expect(read.ok(), await read.text()).toBeTruthy();
  const shown = await read.json();
  expect(shown.body).toContain(`Run the ${clue} suite twice`);
  // Context names pages without their text; local runs leave many, so find this one by its words.
  const context = await (
    await page.request.get("/api/v1/context", { headers: agent })
  ).json();
  expect(context.resources.total).toBeGreaterThan(0);
  expect(context.resources.items[0]).not.toHaveProperty("body");
  const found = await (
    await page.request.get(`/api/v1/resources?q=${clue}`, { headers: agent })
  ).json();
  expect(found.items.map((r: { id: string }) => r.id)).toEqual([id]);
  const refused = await page.request.patch(`/api/v1/resources/${id}`, {
    headers: { ...agent, "Idempotency-Key": crypto.randomUUID() },
    data: { revision: shown.revision, body: "Changed by an agent" },
  });
  expect(refused.status()).toBe(403);
  expect((await refused.json()).error.code).toBe("agent_scope");

  // Search across Worklog opens the page on Resources.
  await page.getByRole("searchbox", { name: "Search all work" }).fill(clue);
  await page
    .getByRole("article")
    .filter({ hasText: "resource" })
    .getByRole("link", { name: title })
    .click();
  await expect(page).toHaveURL(new RegExp(`#resources/${id}$`));
  // Archived, the page leaves the list and offers only recovery.
  await pane.getByRole("button", { name: "Archive", exact: true }).click();
  await expect(
    pane.getByRole("button", { name: "Recover", exact: true }),
  ).toBeVisible();
  await expect(
    pane.getByRole("button", { name: "Edit page", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("searchbox", { name: "Find pages" }).fill(clue);
  await expect(
    page.getByRole("region", { name: "Pages" }).getByRole("link", {
      name: new RegExp(title),
    }),
  ).toHaveCount(0);
  await pane.getByRole("button", { name: "Recover", exact: true }).click();
  await expect(listed).toBeVisible();
});
