import { expect, test, type BrowserContext, type Page } from "@playwright/test";

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

/** Calls the API as the person on this page's session, or as an agent with its token. */
async function api(page: Page, origin: string) {
  // Bootstrap after the page has its session, so both share one CSRF token.
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  return async (
    method: "POST" | "PATCH",
    path: string,
    data: unknown,
    token?: string,
  ) => {
    const response = await page.request.fetch("/api/v1" + path, {
      method,
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        ...(token
          ? { Authorization: "Bearer " + token }
          : { Origin: origin, "X-CSRF-Token": csrfToken }),
      },
      data,
    });
    expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
    return response.json();
  };
}

test("a reminder set on a work item's page shows there, on Today and in the sidebar until it is done", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  // Data persists between local runs, so everything here is found by its own words.
  const suffix = crypto.randomUUID().slice(0, 8);
  const title = `Ask Ana about the rollout ${suffix}`;
  await page.goto("/");
  const call = await api(page, origin);
  const item = await call("POST", "/work-items", {
    title: `Rollout plan ${suffix}`,
  });
  await page.goto(`/#work/${item.id}`);
  const section = page.locator("section.reminder-section");
  await expect(
    section.getByText("No open reminders for this work item."),
  ).toBeVisible();
  // Today, all day: it is due at once.
  await section
    .getByRole("textbox", { name: "New reminder", exact: true })
    .fill(title);
  await section.getByRole("button", { name: "Add reminder" }).click();
  await expect(section.getByRole("link", { name: title })).toBeVisible();
  // The sidebar says how many are due, and its link keeps the name "Reminders".
  await expect(
    page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("link", { name: "Reminders", exact: true }),
  ).toHaveAccessibleDescription(/^\d+ due$/);

  await page.goto("/#today");
  const today = page.getByRole("region", { name: /^Reminders \d+$/ });
  const row = today.getByRole("listitem").filter({ hasText: title });
  await expect(row).toContainText(`Today · All day · Rollout plan ${suffix}`);
  await row.getByRole("button", { name: `Mark ${title} done` }).click();
  await expect(today.getByRole("link", { name: title })).toHaveCount(0);

  // The Reminders page still has it, done; reopened and snoozed, it waits for tomorrow.
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: /^Reminders/ })
    .click();
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await page.getByRole("searchbox", { name: "Find reminders" }).fill(suffix);
  await page
    .getByRole("region", { name: "Reminder list" })
    .getByRole("link", { name: new RegExp(title) })
    .click();
  const pane = page.getByRole("region", { name: "Reminder", exact: true });
  await expect(pane.getByRole("heading", { name: title })).toBeVisible();
  await pane.getByRole("button", { name: "Reopen" }).click();
  await pane.getByRole("button", { name: "Snooze to tomorrow" }).click();
  await expect(pane.getByText(/Coming up · Tomorrow · All day/)).toBeVisible();
  await expect(pane.getByRole("button", { name: "Snooze 1 hour" })).toHaveCount(
    0,
  );
  await expect(
    pane.getByRole("link", { name: `Rollout plan ${suffix}` }),
  ).toHaveAttribute("href", `#work/${item.id}`);
});

test("an agent sets a reminder about a ticket, and the person sees who set it", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID().slice(0, 8);
  const title = `Merge the parser fix ${suffix}`;
  await page.goto("/");
  const call = await api(page, origin);
  const ticket = await call("POST", "/tickets", {
    title: `Parser drops rows ${suffix}`,
  });
  const { token } = await call("POST", "/credentials", {
    label: `Reminder agent ${suffix}`,
  });
  const set = await call(
    "POST",
    "/reminders",
    {
      title,
      date: "2020-01-02",
      time: "09:30",
      targetType: "ticket",
      targetId: ticket.key,
    },
    token,
  );
  expect(set.author.kind).toBe("agent");
  await page.goto(`/#tickets/${ticket.id}`);
  const row = page
    .locator("section.reminder-section")
    .getByRole("listitem")
    .filter({ hasText: title });
  await expect(row).toContainText("Overdue ·");
  await expect(row).toContainText(`Set by Reminder agent ${suffix}`);
  await page.goto(`/#reminders/${set.id}`);
  const pane = page.getByRole("region", { name: "Reminder", exact: true });
  await expect(
    pane.getByRole("link", {
      name: `${ticket.key} Parser drops rows ${suffix}`,
    }),
  ).toHaveAttribute("href", `#tickets/${ticket.id}`);
  await expect(pane.getByText(`Set by Reminder agent ${suffix}`)).toBeVisible();
});
