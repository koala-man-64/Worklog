import {
  expect,
  test,
  type BrowserContext,
  type Locator,
} from "@playwright/test";

// Every browser scenario runs with non-local HTTP traffic denied. This verifies
// the installed app without disabling the user's network connection.
const externalRequests = new WeakMap<BrowserContext, string[]>();
test.beforeEach(async ({ context, baseURL }) => {
  const blocked: string[] = [];
  externalRequests.set(context, blocked);
  const localOrigin = new URL(baseURL!).origin;
  await context.route(/^https?:\/\//, (route) => {
    const origin = new URL(route.request().url()).origin;
    if (origin === localOrigin) return route.continue();
    blocked.push(origin);
    return route.abort("internetdisconnected");
  });
});
test.afterEach(async ({ context }) => {
  expect(
    externalRequests.get(context),
    "No external runtime resources",
  ).toEqual([]);
});
test("daily capture, outcome persistence and keyboard planning controls", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const title = "Browser check " + Date.now();
  await page.getByRole("textbox", { name: "New work item" }).fill(title);
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  await expect(
    page.getByRole("link", { name: title, exact: true }),
  ).toBeVisible();
  const row = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: title, exact: true }) });
  await row.getByLabel("Worked on", { exact: true }).click();
  await expect(row.getByLabel("Worked on", { exact: true })).toBeChecked();
  await row
    .getByRole("textbox", { name: "Outcome / next action" })
    .fill("Verified through the browser");
  await row.getByRole("button", { name: "Save now" }).click();
  await expect(row.getByRole("status")).toHaveText("Saved");
  await page.reload();
  await expect(
    page
      .locator(".day-entry")
      .filter({ has: page.getByRole("link", { name: title, exact: true }) })
      .getByRole("textbox", { name: "Outcome / next action" }),
  ).toHaveValue("Verified through the browser");
  await page.getByRole("link", { name: title, exact: true }).click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
});

test("offline settings, backup, export, and theme controls", async ({
  page,
}) => {
  await page.goto("/#settings");
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Create backup", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Backup created." }),
  ).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page
    .getByRole("link", { name: "Export everything (.zip)", exact: true })
    .click();
  expect((await downloaded).suggestedFilename()).toBe("worklog-export.zip");
  await page.getByLabel("Appearance").selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByLabel("Appearance").selectOption("light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
test("roadmap immutable revisions and mobile navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#roadmaps");
  const roadmapHash = await page.evaluate(() => location.hash);
  await page.getByRole("link", { name: "Skip to content" }).focus();
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => location.hash)).toBe(roadmapHash);
  await expect(page.locator("main")).toBeFocused();
  const title = "Roadmap browser " + Date.now();
  await page
    .getByRole("textbox", { name: "Roadmap title", exact: true })
    .fill(title);
  await page
    .getByRole("button", { name: "Create roadmap", exact: true })
    .click();
  await page.getByRole("link", { name: title, exact: true }).click();
  await page.getByRole("button", { name: "Edit structure" }).click();
  await page.getByRole("button", { name: "Add step", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Step 1 title", exact: true })
    .fill("First safe step");
  await page.getByRole("button", { name: "Save new version" }).click();
  await expect(
    page.getByText("First safe step", { exact: true }),
  ).toBeVisible();
  const views = page.getByRole("navigation", { name: "Roadmap views" });
  const fits = () =>
    page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  await views.getByRole("link", { name: "Status", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Status" })
      .getByRole("link", { name: "First safe step", exact: true }),
  ).toBeVisible();
  expect(await fits()).toBeTruthy();
  // A chat tracked from the roadmap's Chats view opens there, beside the list.
  await views.getByRole("link", { name: "Chats", exact: true }).click();
  const chatList = page.getByRole("region", {
    name: "Agent chats",
    exact: true,
  });
  await chatList
    .getByRole("textbox", { name: "Agent chat title", exact: true })
    .fill("Plan review");
  await chatList
    .getByRole("textbox", { name: "Agent chat ID", exact: true })
    .fill("e2e-" + crypto.randomUUID());
  await chatList.getByRole("button", { name: "Add chat", exact: true }).click();
  await expect(page).toHaveURL(/\/chats\/[0-9a-f-]{36}$/);
  const chatHistory = page.getByRole("region", { name: "Chat history" });
  await expect(
    chatHistory.getByRole("heading", { name: "Plan review", exact: true }),
  ).toBeVisible();
  await expect(
    chatHistory.getByText(/No transcript on this machine has this chat ID/),
  ).toBeVisible();
  expect(await fits()).toBeTruthy();
  await views.getByRole("link", { name: "History", exact: true }).click();
  await expect(page.getByText("Version 2", { exact: true })).toBeVisible();
  expect(await fits()).toBeTruthy();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
});
test("scratchpad, paths, attachments and archive recovery persist", async ({
  page,
}) => {
  await page.goto("/#work");
  const title = "Context browser " + Date.now();
  await page.getByRole("textbox", { name: "New work item" }).fill(title);
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  await page.getByRole("textbox", { name: "Filter work by text" }).fill(title);
  await page.getByRole("link", { name: title, exact: true }).click();
  await page
    .getByRole("button", { name: "Reference a local file", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Local path", exact: true })
    .fill("C:\\Projects\\example.txt");
  await page.getByRole("button", { name: "Add path", exact: true }).click();
  await expect(
    page.getByText("C:\\Projects\\example.txt", { exact: true }),
  ).toBeVisible();
  const scratchpad = page.locator(".scratchpad");
  await scratchpad
    .getByRole("textbox", { name: "Scratchpad (Markdown)", exact: true })
    .fill("## Durable context\n\n- [x] Captured");
  await scratchpad.getByRole("button", { name: "Save now" }).click();
  await expect(scratchpad.getByRole("status")).toHaveText("Saved");
  await page
    .getByRole("button", { name: "Attach a copy", exact: true })
    .click();
  await page.getByLabel("Attach a file").setInputFiles({
    name: "evidence.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Local evidence"),
  });
  await expect(page.getByRole("link", { name: "evidence.txt" })).toBeVisible();
  await page.reload();
  await expect(
    scratchpad.getByRole("textbox", {
      name: "Scratchpad (Markdown)",
      exact: true,
    }),
  ).toHaveValue("## Durable context\n\n- [x] Captured");
  await page
    .getByRole("button", { name: "Archive", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Recover", exact: true }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Recover", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "Archive", exact: true }).first(),
  ).toBeVisible();
});
test("agent proposal remains unapplied until explicit human approval", async ({
  page,
}) => {
  await page.goto("/#settings");
  await page
    .getByRole("textbox", { name: "Credential label" })
    .fill("Browser test agent");
  await page
    .getByRole("button", { name: "Create credential", exact: true })
    .click();
  const token = await page.locator(".token code").innerText();
  await page.getByRole("button", { name: "I have saved it" }).click();
  await expect(page.locator(".token")).toHaveCount(0);
  const title = "Proposed browser " + Date.now();
  const headers = {
    Authorization: "Bearer " + token,
    "Idempotency-Key": crypto.randomUUID(),
  };
  const created = await page.request.post("/api/v1/proposals", {
    headers,
    data: {
      title,
      rationale: "A bounded proposal for human review",
      operations: [
        {
          action: "item.create",
          payload: { title, description: "Approved through review" },
        },
      ],
    },
  });
  expect(created.ok()).toBeTruthy();
  const proposal = await created.json();
  const submitted = await page.request.post(
    `/api/v1/proposals/${proposal.id}/submit`,
    {
      headers: { ...headers, "Idempotency-Key": crypto.randomUUID() },
      data: { revision: proposal.revision },
    },
  );
  expect(submitted.ok()).toBeTruthy();
  await page.goto("/#work");
  await page.getByRole("textbox", { name: "Filter work by text" }).fill(title);
  await expect(
    page.getByRole("link", { name: title, exact: true }),
  ).toHaveCount(0);
  await page.goto("/#review");
  await page.getByRole("link", { name: title, exact: true }).click();
  await expect(page.getByText("item.create", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Approve proposal", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Confirm approve", exact: true })
    .click();
  await expect(page.getByText(/approved, revision/)).toBeVisible();
  await page.goto("/#work");
  await page.getByRole("textbox", { name: "Filter work by text" }).fill(title);
  await expect(
    page.getByRole("link", { name: title, exact: true }),
  ).toBeVisible();
  // The new item's history names the proposal that created it, and its agent lists it.
  await page.getByRole("link", { name: title, exact: true }).click();
  await page.getByText("Applied proposals (1)").click();
  const entry = page
    .getByRole("list", { name: "Applied proposals" })
    .getByRole("listitem");
  await expect(entry).toHaveCount(1);
  await expect(entry).toContainText("Change 1 of 1: item.create");
  await expect(
    entry.getByRole("link", { name: title, exact: true }),
  ).toHaveAttribute("href", "#review/" + proposal.id);
  await entry.getByRole("link", { name: "Browser test agent" }).click();
  await expect(
    page
      .getByRole("list", { name: "Proposals from this agent" })
      .getByRole("link", { name: title, exact: true }),
  ).toBeVisible();
});
test("quick capture distinguishes work already performed from a plan", async ({
  page,
}) => {
  await page.goto("/");
  const title = "Already performed " + Date.now();
  await page
    .getByRole("combobox", { name: "Capture mode" })
    .selectOption("log");
  await page.getByRole("textbox", { name: "New work item" }).fill(title);
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  const row = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: title, exact: true }) });
  await expect(row.getByLabel("Worked on", { exact: true })).toBeChecked();
  await expect(row.getByLabel("Planned", { exact: true })).not.toBeChecked();
});
test("work pages stay bounded and neighbor reorder works across a page boundary", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  await page.goto("/");
  const prefix = "Paging" + Date.now();
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  expect(boot.ok()).toBeTruthy();
  const { csrfToken } = await boot.json();
  for (let i = 0; i < 105; i++) {
    const result = await page.request.post("/api/v1/work-items", {
      headers: {
        Origin: origin,
        "X-CSRF-Token": csrfToken,
        "Idempotency-Key": crypto.randomUUID(),
      },
      data: { title: prefix + " " + String(i).padStart(3, "0") },
    });
    expect(result.ok()).toBeTruthy();
  }
  const offsets: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/work-items")
      offsets.push(url.searchParams.get("offset") ?? "0");
  });
  await page.goto("/#work");
  await page.getByRole("textbox", { name: "Filter work by text" }).fill(prefix);
  await expect(page.locator(".work-row")).toHaveCount(100);
  expect(offsets.every((offset) => offset === "0")).toBeTruthy();
  await page.getByRole("button", { name: "Next work", exact: true }).click();
  await expect(page.locator(".work-row")).toHaveCount(5);
  const moved = page.locator(".work-row").first();
  const before = await moved.locator(".item-title").innerText();
  await moved
    .getByRole("button", { name: "Move " + before + " up", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(
    page.locator(".work-row").first().locator(".item-title"),
  ).not.toHaveText(before);
  await expect(page.getByRole("alert")).toHaveCount(0);
});
test("native day drag reorders actual entries without losing an outcome", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 1600 });
  await page.goto("/");
  const prefix = "Drag" + Date.now();
  const first = prefix + " first";
  const second = prefix + " second";
  for (const title of [first, second]) {
    await page.getByRole("textbox", { name: "New work item" }).fill(title);
    await page.getByRole("button", { name: "Add work", exact: true }).click();
    await expect(
      page.getByRole("link", { name: title, exact: true }),
    ).toBeVisible();
  }
  const source = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: second, exact: true }) });
  await source
    .getByRole("textbox", { name: "Outcome / next action" })
    .fill("Keep this outcome");
  await source.getByRole("button", { name: "Save now" }).click();
  await expect(source.getByRole("status")).toHaveText("Saved");
  await page.reload();
  await expect(
    source.getByRole("textbox", { name: "Outcome / next action" }),
  ).toHaveValue("Keep this outcome");
  const target = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: first, exact: true }) });
  await source
    .getByRole("img", {
      name: "Drag " + second + " to reorder",
      exact: true,
    })
    .dragTo(
      target.getByRole("img", {
        name: "Drag " + first + " to reorder",
        exact: true,
      }),
    );
  await expect(page.locator(".day-entry .item-title").last()).toHaveText(first);
  await page.reload();
  await expect(page.locator(".day-entry .item-title").last()).toHaveText(first);
  await expect(
    page
      .locator(".day-entry")
      .filter({ has: page.getByRole("link", { name: second, exact: true }) })
      .getByRole("textbox", { name: "Outcome / next action" }),
  ).toHaveValue("Keep this outcome");
});
test("an entry's scratchpad on Today is its work item's own", async ({
  page,
}) => {
  await page.goto("/");
  const title = "Scratchpad " + Date.now();
  await page.getByRole("textbox", { name: "New work item" }).fill(title);
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  const entry = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: title, exact: true }) });
  await entry.getByRole("tab", { name: "Scratchpad", exact: true }).click();
  await entry
    .getByRole("textbox", { name: "Scratchpad (Markdown)", exact: true })
    .fill("Written on Today");
  const pad = entry.locator(".scratchpad");
  await pad.getByRole("button", { name: "Save now" }).click();
  await expect(pad.getByRole("status")).toHaveText("Saved");
  await entry.getByRole("link", { name: title, exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Scratchpad (Markdown)", exact: true }),
  ).toHaveValue("Written on Today");
});
test("a Today entry collapses to its heading row and stays collapsed after a reload", async ({
  page,
}) => {
  await page.goto("/");
  const title = "Collapse " + Date.now();
  await page.getByRole("textbox", { name: "New work item" }).fill(title);
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  const entry = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: title, exact: true }) });
  const outcome = entry.getByRole("textbox", {
    name: "Outcome / next action",
    includeHidden: true,
  });
  await expect(outcome).toBeVisible();
  await entry
    .getByRole("button", { name: "Collapse " + title, exact: true })
    .click();
  // Hidden, not removed: the field is still there for its draft to save.
  await expect(outcome).toBeAttached();
  await expect(outcome).toBeHidden();
  await expect(
    entry.getByRole("button", { name: "Reschedule " + title, exact: true }),
  ).toBeVisible();
  await page.reload();
  const expand = entry.getByRole("button", {
    name: "Expand " + title,
    exact: true,
  });
  await expect(expand).toHaveAttribute("aria-expanded", "false");
  await expect(outcome).toBeHidden();
  await expand.click();
  await expect(outcome).toBeVisible();
});
test("stack rank moves work from its row, its picker and its page", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const prefix = "Ranked " + Date.now();
  const [a, b, c] = ["A", "B", "C"].map((name) => prefix + " " + name);
  // Created before the page loads, so the filtered list shows all three at once.
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  for (const title of [a, b, c]) {
    const created = await page.request.post("/api/v1/work-items", {
      headers: {
        Origin: origin,
        "X-CSRF-Token": csrfToken,
        "Idempotency-Key": crypto.randomUUID(),
      },
      data: { title },
    });
    expect(created.ok()).toBeTruthy();
  }
  await page.goto("/#work");
  await page.getByRole("textbox", { name: "Filter work by text" }).fill(prefix);
  const titles = page.locator(".work-row .item-title");
  await expect(titles).toHaveText([a, b, c]);
  await page
    .getByRole("button", { name: `Move ${a} down`, exact: true })
    .click();
  await expect(titles).toHaveText([b, a, c]);
  // The picker takes C to the top of all work, so above A and B whatever else is ranked.
  await page
    .getByRole("button", {
      name: new RegExp(`^Change stack rank of ${c}, now`),
    })
    .click();
  const input = page.getByRole("spinbutton", {
    name: `New stack rank for ${c}`,
  });
  await input.fill("1");
  await input.press("Enter");
  await expect(titles).toHaveText([c, b, a]);
  await expect(input).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: `Change stack rank of ${c}, now 1`,
      exact: true,
    }),
  ).toBeVisible();
  // Waiting work is unfinished: it keeps its place in the ranking.
  const waiting = page
    .locator(".work-row")
    .filter({ has: page.getByRole("link", { name: b, exact: true }) })
    .getByRole("combobox", { name: "Status" });
  await waiting.selectOption("waiting");
  await expect(waiting).toHaveValue("waiting");
  await expect(titles).toHaveText([c, b, a]);
  await expect(
    page.getByRole("button", {
      name: new RegExp(`^Change stack rank of ${b}, now \\d+$`),
    }),
  ).toBeVisible();
  // Done work leaves the ranking and follows the ranked work.
  await page
    .locator(".work-row")
    .filter({ has: page.getByRole("link", { name: c, exact: true }) })
    .getByRole("combobox", { name: "Status" })
    .selectOption("done");
  await expect(titles).toHaveText([b, a, c]);
  await expect(
    page.getByRole("button", {
      name: new RegExp(`^Change stack rank of ${c}`),
    }),
  ).toHaveCount(0);
  // The item page shows the rank among all ranked work.
  await page.getByRole("link", { name: b, exact: true }).click();
  await expect(
    page.getByRole("button", {
      name: new RegExp(`^Change stack rank of ${b}, now \\d+$`),
    }),
  ).toContainText(/^#\d+ of \d+$/);
  await expect(page.getByRole("alert")).toHaveCount(0);
});
test("open work is planned for the day and rescheduled from beside its title", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const title = "Open work " + Date.now();
  // Created before the page loads and outside the day, so it waits in Open work.
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  const created = await page.request.post("/api/v1/work-items", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: { title },
  });
  expect(created.ok()).toBeTruthy();
  await page.goto("/");
  const open = page.locator("section.open-work");
  await open.getByRole("textbox", { name: "Find open work" }).fill(title);
  await open
    .getByRole("button", { name: `Work on ${title} today`, exact: true })
    .click();
  const entry = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: title, exact: true }) });
  await expect(entry).toBeVisible();
  await expect(
    open.getByRole("link", { name: title, exact: true }),
  ).toHaveCount(0);
  const today = await page.getByLabel("Working day").inputValue();
  const next = new Date(Date.parse(today + "T12:00:00Z") + 86_400_000)
    .toISOString()
    .slice(0, 10);
  await entry
    .getByRole("button", { name: "Reschedule " + title, exact: true })
    .click();
  await entry.getByLabel("New date for " + title).fill(next);
  await entry.getByRole("button", { name: "Move", exact: true }).click();
  // Nothing was recorded on it today, so it leaves today and waits in Open work.
  await expect(entry).toHaveCount(0);
  await expect(
    open.getByRole("button", { name: `Work on ${title} today`, exact: true }),
  ).toBeVisible();
  await page.goto("/#today/" + next);
  const moved = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: title, exact: true }) });
  await expect(moved).toBeVisible();
  await expect(moved.getByLabel("Planned", { exact: true })).toBeChecked();
});
test("work not reached comes off the day and waits in Open work again", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const title = "Not reached " + Date.now();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  const created = await page.request.post("/api/v1/work-items", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: { title },
  });
  expect(created.ok()).toBeTruthy();
  const { id } = await created.json();
  await page.goto("/");
  const open = page.locator("section.open-work");
  await open.getByRole("textbox", { name: "Find open work" }).fill(title);
  const plan = open.getByRole("button", {
    name: `Work on ${title} today`,
    exact: true,
  });
  await plan.click();
  const entry = page
    .locator(".day-entry")
    .filter({ has: page.getByRole("link", { name: title, exact: true }) });
  const remove = entry.getByRole("button", {
    name: `Remove ${title} from today`,
    exact: true,
  });
  // Only planned, it comes off at once and waits in Open work again.
  await remove.click();
  await expect(entry).toHaveCount(0);
  await expect(plan).toBeVisible();
  // Once the day records work on it, taking it off asks first.
  await plan.click();
  await entry.getByLabel("Worked on", { exact: true }).click();
  await expect(entry.getByLabel("Worked on", { exact: true })).toBeChecked();
  await remove.click();
  const question = entry.getByRole("group", {
    name: "It is marked “Worked on”. Remove it from today anyway?",
  });
  await question.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(question).toHaveCount(0);
  await expect(entry).toBeVisible();
  await remove.click();
  await question
    .getByRole("button", { name: "Confirm remove", exact: true })
    .click();
  await expect(entry).toHaveCount(0);
  await expect(plan).toBeVisible();
  // The item's own record no longer lists the day.
  const detail = await page.request.get("/api/v1/work-items/" + id, {
    headers: { Origin: origin },
  });
  expect((await detail.json()).dailyEntries).toEqual([]);
});
test("a link changed in another tab survives tabbing through a stale field", async ({
  context,
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  await page.goto("/");
  // Let the app finish its own bootstrap so both share one session cookie.
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  const created = await page.request.post("/api/v1/work-items", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: { title: "Link race " + Date.now() },
  });
  expect(created.ok()).toBeTruthy();
  const { id } = await created.json();
  const mine = "https://dev.azure.com/org/project/_workitems/edit/1";
  const url = "https://dev.azure.com/org/project/_workitems/edit/2";
  const link = (p: typeof page) =>
    p.getByRole("textbox", { name: "Azure DevOps link" });
  const savedLink = async () =>
    (await (await page.request.get(`/api/v1/work-items/${id}`)).json()).adoUrl;
  // The first tab sets the link itself, so its field has been edited by the user.
  await page.goto("/#work/" + id);
  await link(page).fill(mine);
  await link(page).press("Tab");
  await expect.poll(savedLink).toBe(mine);
  const other = await context.newPage();
  await other.goto("/#work/" + id);
  await expect(link(other)).toHaveValue(mine);
  await link(other).fill(url);
  await link(other).press("Tab");
  await expect.poll(savedLink).toBe(url);
  // Returning to the first tab refetches the item; tabbing through must not write.
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET") writes.push(request.url());
  });
  await page.bringToFront();
  await page.evaluate(() => {
    window.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(link(page)).toHaveValue(url);
  await link(page).focus();
  await page.keyboard.press("Tab");
  await page.waitForTimeout(300);
  expect(writes).toEqual([]);
  expect(await savedLink()).toBe(url);
  await other.close();
});
test("a progress change made while editing leaves the structure draft current", async ({
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
  const { csrfToken } = await boot.json();
  const created = await page.request.post("/api/v1/roadmaps", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: {
      title: "Draft race " + Date.now(),
      steps: [{ title: "Leaf one" }, { title: "Leaf two" }],
    },
  });
  expect(created.ok()).toBeTruthy();
  const { id } = await created.json();
  await page.goto("/#roadmaps/" + id);
  await page.getByRole("button", { name: "Edit structure" }).click();
  await page
    .getByRole("textbox", { name: "Step 1 title" })
    .fill("Leaf one, renamed");
  // Progress is the step's: it leaves the roadmap's revision, which used to strand the draft (K4).
  const progressed = page.waitForResponse(
    (r) => r.url().includes("/progress") && r.request().method() === "PATCH",
  );
  await page.getByLabel("Progress for Leaf two").selectOption("doing");
  expect((await progressed).ok()).toBeTruthy();
  const current = await (await page.request.get(`/api/v1/roadmaps/${id}`)).json();
  expect(current.revision).toBe(1);
  await expect(page.getByText(/based on revision/)).toHaveCount(0);
  await page.getByRole("button", { name: "Save new version" }).click();
  await expect(
    page.getByRole("button", { name: "Edit structure" }),
  ).toBeVisible();
  const saved = await (await page.request.get(`/api/v1/roadmaps/${id}`)).json();
  const active = saved.steps.filter((s: { retired: boolean }) => !s.retired);
  expect(active.map((s: { title: string }) => s.title)).toEqual([
    "Leaf one, renamed",
    "Leaf two",
  ]);
  expect(active[1].status).toBe("doing");
  expect(saved.revision).toBe(2);
});
test("a step's text edited alone survives a structure draft's rebase", async ({
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
  const { csrfToken } = await boot.json();
  const created = await page.request.post("/api/v1/roadmaps", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: {
      title: "Step text " + Date.now(),
      steps: [{ title: "Leaf one" }, { title: "Leaf two" }, { title: "Leaf three" }],
    },
  });
  expect(created.ok()).toBeTruthy();
  const { id, steps } = await created.json();
  await page.goto("/#roadmaps/" + id);
  // A structure draft starts, and is left open in this browser.
  await page.getByRole("button", { name: "Edit structure" }).click();
  await page
    .getByRole("textbox", { name: "Step 1 title" })
    .fill("Leaf one, renamed");
  await page.getByRole("button", { name: "Close editor" }).click();
  // Meanwhile one step's text changes alone, as an agent's step.update would.
  await page.goto(`/#roadmaps/${id}/${steps[1].id}`);
  await page.getByRole("button", { name: "Edit step" }).click();
  await page
    .getByRole("textbox", { name: "Description (Markdown)" })
    .fill("AB#1559 is the predecessor");
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/steps/${steps[1].id}`) &&
      r.request().method() === "PATCH",
  );
  await page.getByRole("button", { name: "Save step" }).click();
  expect((await saved).ok()).toBeTruthy();
  await expect(page.getByText("AB#1559 is the predecessor")).toBeVisible();
  // The draft is now behind; rebasing keeps both changes.
  await page.goto("/#roadmaps/" + id);
  await page.getByRole("button", { name: "Edit structure" }).click();
  await expect(page.getByText(/based on revision 1/)).toBeVisible();
  await page.getByRole("button", { name: "Rebase onto current" }).click();
  await expect(
    page.getByRole("textbox", { name: "Step 2 description" }),
  ).toHaveValue("AB#1559 is the predecessor");
  await page.getByRole("button", { name: "Save new version" }).click();
  await expect(
    page.getByRole("button", { name: "Edit structure" }),
  ).toBeVisible();
  const plan = await (await page.request.get(`/api/v1/roadmaps/${id}`)).json();
  const by = (title: string) =>
    plan.steps.find((s: { title: string }) => s.title === title);
  expect(by("Leaf one, renamed").revision).toBe(2);
  expect(by("Leaf two").description).toBe("AB#1559 is the predecessor");
  expect(by("Leaf two").revision).toBe(2);
  // Untouched, and the step edit recorded no version: create, then the structure save.
  expect(by("Leaf three").revision).toBe(1);
  expect(plan.versions).toHaveLength(2);
});
test("the sidebar and plan outline resize by drag and keep their widths", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  const created = await page.request.post("/api/v1/roadmaps", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: { title: "Resizable " + Date.now(), steps: [{ title: "Only step" }] },
  });
  expect(created.ok()).toBeTruthy();
  const { id } = await created.json();
  await page.goto(`/#roadmaps/${id}/outline`);
  const outline = page.getByRole("region", { name: "Plan outline" });
  const sidebar = page.getByRole("complementary");
  const outlineHandle = page.getByRole("separator", {
    name: "Resize plan outline",
  });
  const sidebarHandle = page.getByRole("separator", { name: "Resize sidebar" });
  const width = async (target: Locator) => (await target.boundingBox())!.width;
  const drag = async (handle: Locator, dx: number) => {
    const box = (await handle.boundingBox())!;
    const y = box.y + box.height / 2;
    await page.mouse.move(box.x + box.width / 2, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + dx, y, { steps: 4 });
    await page.mouse.up();
  };
  await expect(outline).toBeVisible();
  const initial = await width(outline);
  await drag(outlineHandle, 120);
  expect(Math.abs((await width(outline)) - (initial + 120))).toBeLessThan(1);
  await drag(sidebarHandle, 60);
  expect(await width(sidebar)).toBe(290);
  await expect(sidebarHandle).toHaveAttribute("aria-valuenow", "290");
  // The outline stops where the detail pane would drop below 300px.
  await drag(outlineHandle, 2000);
  const detail = page.getByRole("region", { name: "Step details" });
  expect(Math.round(await width(detail))).toBe(300);
  await drag(outlineHandle, -2000);
  expect(await width(outline)).toBe(300);
  await page.reload();
  await expect(outline).toBeVisible();
  expect(await width(sidebar)).toBe(290);
  expect(await width(outline)).toBe(300);
  await sidebarHandle.dblclick();
  expect(await width(sidebar)).toBe(230);
  await outlineHandle.dblclick();
  expect(Math.abs((await width(outline)) - initial)).toBeLessThan(1);
  // Stacked layouts have nothing to drag and still fit the window.
  await drag(sidebarHandle, 120);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(sidebarHandle).toBeHidden();
  await expect(outlineHandle).toBeHidden();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
test("every page spends the whole window beside the sidebar", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  await page.setViewportSize({ width: 2200, height: 900 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const pages = {
    today: "Today",
    work: "Work",
    tickets: "Tickets",
    roadmaps: "Roadmaps",
    repositories: "Repositories",
    people: "People",
    resources: "Resources",
    agents: "Agents",
    chats: "Chats",
    discussions: "Discussions",
    review: "Review",
    settings: "Settings",
    guide: "Guide",
  };
  for (const [route, heading] of Object.entries(pages)) {
    await page.goto("/#" + route);
    await expect(
      page.getByRole("heading", { level: 1, name: heading, exact: true }),
    ).toBeVisible();
    // main fills the workspace, and the top bar's ends line up with the page's.
    const edges = await page.evaluate(() => {
      const box = (selector: string) =>
        document.querySelector(selector)!.getBoundingClientRect();
      const main = document.querySelector("main")!;
      const style = getComputedStyle(main);
      return {
        main: box("main").width,
        workspace: box(".workspace").width,
        barStart: box(".topbar > span").left,
        pageStart:
          main.getBoundingClientRect().left + parseFloat(style.paddingLeft),
        barEnd: box(".search").right,
        pageEnd:
          main.getBoundingClientRect().right - parseFloat(style.paddingRight),
      };
    });
    expect(edges.main, route).toBe(edges.workspace);
    expect(edges.barStart, route).toBeCloseTo(edges.pageStart, 0);
    expect(edges.barEnd, route).toBeCloseTo(edges.pageEnd, 0);
  }
  // A roadmap's overview runs the width of its status view.
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  const created = await page.request.post("/api/v1/roadmaps", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: {
      title: "Full width " + Date.now(),
      overview: "Ship the plan across the whole pane. ".repeat(40),
      steps: [{ title: "Only step" }],
    },
  });
  expect(created.ok()).toBeTruthy();
  const { id } = await created.json();
  await page.goto("/#roadmaps/" + id);
  const overview = page
    .getByRole("region", { name: "Status" })
    .locator(":scope > section > .markdown");
  await expect(overview).toBeVisible();
  const fit = await overview.evaluate((markdown) => {
    const pane = markdown.closest('[role="region"]')!;
    const style = getComputedStyle(pane);
    return {
      text: markdown.getBoundingClientRect().width,
      pane:
        pane.clientWidth -
        parseFloat(style.paddingLeft) -
        parseFloat(style.paddingRight),
    };
  });
  expect(fit.text).toBeCloseTo(fit.pane, 0);
});
test("every page stays reachable from the sidebar on a short window", async ({
  page,
}) => {
  // The page links, the brand and the note outgrow 480px: the links scroll rather than fall off.
  await page.setViewportSize({ width: 1280, height: 480 });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  for (const name of [
    "Guide",
    "Settings",
    "Review",
    "Discussions",
    "Chats",
    "Agents",
    "Resources",
    "People",
    "Repositories",
    "Roadmaps",
    "Tickets",
    "Work",
    "Today",
  ]) {
    await nav.getByRole("link", { name, exact: true }).click();
    await expect(
      page.getByRole("heading", { level: 1, name, exact: true }),
    ).toBeVisible();
  }
});
test("a roadmap's questions are answered and its decisions recorded", async ({
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
  const { csrfToken } = await boot.json();
  const created = await page.request.post("/api/v1/roadmaps", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: { title: "Decisions " + Date.now(), steps: [{ title: "Only step" }] },
  });
  expect(created.ok()).toBeTruthy();
  const { id } = await created.json();
  await page.goto(`/#roadmaps/${id}/questions`);
  const detail = page.getByRole("region", { name: "Questions and decisions" });
  const tab = page
    .getByRole("navigation", { name: "Roadmap views" })
    .getByRole("link", { name: /^Questions and decisions/ });
  await detail
    .getByRole("textbox", { name: "New question" })
    .fill("Which region hosts replicas?");
  await detail.getByRole("button", { name: "Add question" }).click();
  // The view counts it as open, and the status board asks for it under Needs you.
  await expect(tab).toHaveAccessibleName("Questions and decisions, 1 open");
  await page
    .getByRole("navigation", { name: "Roadmap views" })
    .getByRole("link", { name: "Status", exact: true })
    .click();
  await page
    .getByRole("region", { name: "Status" })
    .getByRole("link", { name: "Which region hosts replicas?" })
    .click();
  await expect(page).toHaveURL(new RegExp(`#roadmaps/${id}/questions$`));
  await detail
    .getByRole("button", {
      name: "Answer question Which region hosts replicas?",
    })
    .click();
  await detail.getByRole("textbox", { name: "Answer" }).fill("Frankfurt");
  await detail.getByRole("button", { name: "Save answer" }).click();
  await expect(detail.getByText("No open questions.")).toBeVisible();
  await expect(tab).toHaveAccessibleName("Questions and decisions");
  await detail.getByText("Answered (1)").click();
  await expect(detail.getByText("Frankfurt")).toBeVisible();

  await detail
    .getByRole("textbox", { name: "New decision", exact: true })
    .fill("Use SQLite");
  await detail
    .getByRole("textbox", { name: "New decision rationale" })
    .fill("One file, no server.");
  await detail.getByLabel("Date decided").fill("2026-10-30");
  await detail.getByRole("button", { name: "Record decision" }).click();
  await expect(detail.getByText("Decided 2026-10-30")).toBeVisible();

  await page.reload();
  await expect(detail.getByText("One file, no server.")).toBeVisible();
  const saved = await (await page.request.get(`/api/v1/roadmaps/${id}`)).json();
  expect(saved.questions).toMatchObject([
    {
      title: "Which region hosts replicas?",
      status: "answered",
      answer: "Frankfurt",
    },
  ]);
  expect(saved.decisions).toMatchObject([
    {
      title: "Use SQLite",
      rationale: "One file, no server.",
      decidedDate: "2026-10-30",
    },
  ]);
});
test("an open page carries on after the server forgets its session", async ({
  context,
  page,
  baseURL,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const add = async (title: string) => {
    await page.getByRole("textbox", { name: "New work item" }).fill(title);
    await page.getByRole("button", { name: "Add work", exact: true }).click();
    await expect(
      page.getByRole("link", { name: title, exact: true }),
    ).toBeVisible();
    const found = await page.request.get(
      "/api/v1/work-items?q=" + encodeURIComponent(title),
    );
    expect((await found.json()).total).toBe(1);
  };
  // A restart drops every session the server held, which to it is the same as a missing cookie.
  await context.clearCookies();
  await add("After a restart " + Date.now());
  // Another tab that renewed after the restart leaves this page holding the old session's token.
  await context.clearCookies();
  const renewed = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: new URL(baseURL!).origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  expect(renewed.ok()).toBeTruthy();
  await add("After another tab renewed " + Date.now());
});

test("a long step title takes one line in the outline and on the board, in full on hover", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  // As long as the person's titles run: the median is about 90 characters.
  const long =
    "Port production rule group 1 to the recursive descent parser and keep every one of its golden tests passing";
  const phase = crypto.randomUUID();
  const created = await page.request.post("/api/v1/roadmaps", {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: {
      title: "Long titles " + Date.now(),
      steps: [
        {
          id: phase,
          title:
            "Phase 1: rewrite the parser, rule group by rule group, behind a flag",
        },
        { title: long, parentId: phase },
      ],
    },
  });
  expect(created.ok()).toBeTruthy();
  const { id } = await created.json();
  const oneLine = (link: Locator) =>
    link.evaluate((a) => ({
      wraps: getComputedStyle(a).whiteSpace !== "nowrap",
      cut: a.scrollWidth > a.clientWidth,
      ellipsis: getComputedStyle(a).textOverflow,
      title: a.getAttribute("title"),
    }));
  await page.goto(`/#roadmaps/${id}/outline`);
  const inOutline = page
    .getByRole("region", { name: "Plan outline" })
    .getByRole("link", { name: long, exact: true });
  await expect(inOutline).toBeVisible();
  expect(await oneLine(inOutline)).toEqual({
    wraps: false,
    cut: true,
    ellipsis: "ellipsis",
    title: long,
  });
  await page.goto("/#roadmaps/" + id);
  const onBoard = page
    .getByRole("region", { name: "Status" })
    .getByRole("link", { name: long, exact: true });
  await expect(onBoard).toBeVisible();
  expect(await oneLine(onBoard)).toEqual({
    wraps: false,
    cut: true,
    ellipsis: "ellipsis",
    title: long,
  });
});
