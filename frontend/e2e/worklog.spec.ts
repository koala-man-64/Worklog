import { expect, test, type BrowserContext } from "@playwright/test";

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
  expect(externalRequests.get(context), "No external runtime resources").toEqual([]);
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
    .getByLabel("Outcome / next action")
    .fill("Verified through the browser");
  await row.getByRole("button", { name: "Save now" }).click();
  await expect(row.getByRole("status")).toHaveText("Saved");
  await page.reload();
  await expect(
    page
      .locator(".day-entry")
      .filter({ has: page.getByRole("link", { name: title, exact: true }) })
      .getByLabel("Outcome / next action"),
  ).toHaveValue("Verified through the browser");
  await page.getByRole("link", { name: title, exact: true }).click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
});

test("offline settings, backup, export, and theme controls", async ({ page }) => {
  await page.goto("/#settings");
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Create backup", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Backup created." })).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export everything (.zip)", exact: true }).click();
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
  await expect(
    page.getByRole("button", { name: "View version 2", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
});
test("notes, checklist, paths, attachments and archive recovery persist", async ({
  page,
}) => {
  await page.goto("/#work");
  const title = "Context browser " + Date.now();
  await page.getByRole("textbox", { name: "New work item" }).fill(title);
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  await page.getByRole("textbox", { name: "Filter work by text" }).fill(title);
  await page.getByRole("link", { name: title, exact: true }).click();
  await page
    .getByRole("textbox", { name: "New checklist task" })
    .fill("A small action");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByLabel("A small action")).toBeVisible();
  await page
    .getByRole("textbox", { name: "Local path", exact: true })
    .fill("C:\\Projects\\example.txt");
  await page.getByRole("button", { name: "Add path", exact: true }).click();
  await expect(
    page.getByText("C:\\Projects\\example.txt", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Add note", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Markdown note", exact: true })
    .fill("## Durable context\n\n- [x] Captured");
  const note = page.locator(".note");
  await note.getByRole("button", { name: "Save now" }).last().click();
  await expect(note.getByRole("status").last()).toHaveText("Saved");
  await page.getByLabel("Attach a file").setInputFiles({
    name: "evidence.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Local evidence"),
  });
  await expect(page.getByRole("link", { name: "evidence.txt" })).toBeVisible();
  await page.getByRole("button", { name: "Archive note", exact: true }).click();
  await expect(note).toHaveCount(0);
  await page.getByText(/Archived supporting records/).click();
  await page.getByRole("button", { name: "Recover", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Markdown note", exact: true }),
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
test("HTML import previews safe content and preserves original reference", async ({
  page,
}) => {
  await page.goto("/#roadmaps");
  const title = "Import " + Date.now();
  await page.getByLabel("Import HTML plan").setInputFiles({
    name: "plan.html",
    mimeType: "text/html",
    buffer: Buffer.from(
      `<h1>${title}</h1><p>Overview</p><h2>Build carefully</h2><p>First action</p><script>window.importExecuted=true</script>`,
    ),
  });
  await expect(page.getByLabel("Imported title")).toHaveValue(title);
  await expect(
    page.getByText("Build carefully", { exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => "importExecuted" in window)).toBe(false);
  await page
    .getByRole("button", { name: "Create from selected steps", exact: true })
    .click();
  await page.getByRole("link", { name: title, exact: true }).click();
  await expect(page.getByRole("link", { name: "plan.html" })).toBeVisible();
  await expect(
    page.getByText("Build carefully", { exact: true }),
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
  await page.getByRole("button", { name: "I have saved it — dismiss" }).click();
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
  await expect(page.getByText(/approved · revision/)).toBeVisible();
  await page.goto("/#work");
  await page.getByRole("textbox", { name: "Filter work by text" }).fill(title);
  await expect(
    page.getByRole("link", { name: title, exact: true }),
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
  await source.getByLabel("Outcome / next action").fill("Keep this outcome");
  await source.getByRole("button", { name: "Save now" }).click();
  await expect(source.getByRole("status")).toHaveText("Saved");
  await page.reload();
  await expect(source.getByLabel("Outcome / next action")).toHaveValue(
    "Keep this outcome",
  );
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
      .getByLabel("Outcome / next action"),
  ).toHaveValue("Keep this outcome");
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
test("a structure draft survives a progress change made while editing", async ({
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
  // Progress bumps the roadmap revision, which used to strand the draft.
  await page.getByLabel("Progress for Leaf two").selectOption("doing");
  await expect(page.getByText(/based on revision/)).toBeVisible();
  await page.getByRole("button", { name: "Rebase onto current" }).click();
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
});
