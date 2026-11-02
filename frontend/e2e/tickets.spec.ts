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

test("the person files a ticket, an agent works and closes it, and search finds it later", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  // Data persists between local runs, so everything here is found by its own words.
  const suffix = crypto.randomUUID().slice(0, 8);
  const title = `Backup holds a lock ${suffix}`;
  const clue = `wombat${suffix}`;
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Tickets", exact: true })
    .click();
  await expect(page).toHaveURL(/#tickets$/);
  await page.getByRole("textbox", { name: "New ticket title" }).fill(title);
  await page
    .getByRole("combobox", { name: "New ticket kind" })
    .selectOption("incident");
  await page.getByRole("button", { name: "File ticket", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "New ticket title" }),
  ).toHaveValue("");
  await page.getByRole("textbox", { name: "Find tickets" }).fill(suffix);
  const link = page.getByRole("link", { name: new RegExp(title) });
  await expect(link).toBeVisible();
  const href = await link.getAttribute("href");
  const id = href!.split("/")[1];

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
    data: { label: "Ticket agent " + suffix },
  });
  const { token } = await issued.json();
  const agent = { Authorization: "Bearer " + token };
  const commented = await page.request.post(`/api/v1/tickets/${id}/comments`, {
    headers: { ...agent, "Idempotency-Key": crypto.randomUUID() },
    data: { text: `Reproduced: the ${clue} snapshot waits on the writer.` },
  });
  expect(commented.ok(), await commented.text()).toBeTruthy();
  const current = await (
    await page.request.get(`/api/v1/tickets/${id}`, { headers: agent })
  ).json();
  const closed = await page.request.patch(`/api/v1/tickets/${id}`, {
    headers: { ...agent, "Idempotency-Key": crypto.randomUUID() },
    data: {
      revision: current.revision,
      status: "done",
      resolution: "Take the snapshot with the online backup API.",
    },
  });
  expect(closed.ok(), await closed.text()).toBeTruthy();

  await page.goto("/" + href);
  await expect(
    page.getByRole("heading", { name: new RegExp(title) }),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Status" })).toHaveValue(
    "done",
  );
  await expect(
    page.getByRole("textbox", { name: "Resolution (needed to mark Done)" }),
  ).toHaveValue("Take the snapshot with the online backup API.");
  const comments = page.locator(".ticket-comments");
  await expect(comments.getByText("Ticket agent " + suffix)).toBeVisible();
  await expect(comments.getByText(new RegExp(clue))).toBeVisible();

  // Done tickets leave the active list but stay findable as history.
  await page.goto("/#tickets");
  await page.getByRole("textbox", { name: "Find tickets" }).fill(suffix);
  await expect(page.getByText("No tickets match these filters.")).toBeVisible();
  await page
    .getByRole("combobox", { name: "Filter tickets by status" })
    .selectOption("done");
  await expect(
    page.getByRole("link", { name: new RegExp(title) }),
  ).toBeVisible();
  await page.getByRole("searchbox", { name: "Search all work" }).fill(clue);
  const result = page.locator(".search-result", { hasText: "ticket_comment" });
  await expect(result).toBeVisible();
  await result.getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(`#tickets/${id}$`));
  await expect(
    page.getByRole("heading", { name: new RegExp(title) }),
  ).toBeVisible();
});
