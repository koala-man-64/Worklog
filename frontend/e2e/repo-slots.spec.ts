import { expect, test } from "@playwright/test";

test("repository checkout slots enforce cap, show queue, and promote on force release", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID().slice(0, 8);
  const repoName = "SlotRepo-" + suffix;
  const workName = "SlotTask-" + suffix;

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();

  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();

  async function post(path: string, data: unknown, token?: string) {
    const response = await page.request.post("/api/v1" + path, {
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
  }

  const repo = await post("/repositories", {
    name: repoName,
    url: `https://github.com/example/${repoName}`,
  });
  const work = await post("/work-items", {
    title: workName,
    repositoryIds: [repo.id],
  });

  // Issue 3 agents; acquire 2 active slots + 1 queued waiter.
  const a1 = await post("/credentials", { label: "Claude-" + suffix });
  const a2 = await post("/credentials", { label: "Codex-" + suffix });
  const a3 = await post("/credentials", { label: "Antigravity-" + suffix });

  const l1 = await post(
    "/leases/acquire",
    { repositoryId: repo.id, provider: "claude", branch: "claude/one" },
    a1.token,
  );
  const l2 = await post(
    "/leases/acquire",
    { repositoryId: repo.id, provider: "codex", branch: "codex/two" },
    a2.token,
  );
  const l3 = await post(
    "/leases/acquire",
    { repositoryId: repo.id, provider: "antigravity", branch: "ag/three" },
    a3.token,
  );
  expect([l1.state, l2.state, l3.state]).toEqual([
    "active",
    "active",
    "queued",
  ]);

  // Repositories list displays compact 2/2 + +1 queued badges.
  await page.goto("/#repositories");
  await page
    .getByRole("textbox", { name: "Find repositories", exact: true })
    .fill(repoName);
  const row = page.locator("article").filter({
    has: page.getByRole("link", { name: repoName, exact: true }),
  });
  await expect(row).toContainText("Slots 2/2");
  await expect(row).toContainText("+1 queued");

  // Open repository detail: see both active slots, queued waiter, and force-release Slot 1.
  await row.getByRole("link", { name: repoName, exact: true }).click();
  const slotsSection = page.getByRole("region", {
    name: "Checkout slots and queue",
  });
  await expect(slotsSection).toContainText("Claude-" + suffix);
  await expect(slotsSection).toContainText("Codex-" + suffix);
  await expect(slotsSection).toContainText("#1");
  await expect(slotsSection).toContainText("Antigravity-" + suffix);

  // Force-release Slot 1 -> Antigravity waiter promotes to Offered in Slot 1!
  const slot1 = slotsSection.locator("article.slot-card").first();
  await slot1.getByRole("button", { name: "Force release", exact: true }).click();
  await expect(slot1).toContainText("Antigravity-" + suffix);
  await expect(slot1).toContainText("Offered");

  // Update slot policy cap to 3 -> Slot 3 becomes available.
  await slotsSection
    .getByRole("spinbutton", { name: "Max active slots" })
    .fill("3");
  await slotsSection
    .getByRole("button", { name: "Save slot policy", exact: true })
    .click();
  await expect(slotsSection).toContainText("Slot 3");

  // Freeze slots (maxActiveLeases = 0) and queue a Work Item session dispatch, then cancel it.
  await slotsSection
    .getByRole("spinbutton", { name: "Max active slots" })
    .fill("0");
  await slotsSection
    .getByRole("button", { name: "Save slot policy", exact: true })
    .click();
  await expect(slotsSection).toContainText("Frozen (0/0)");

  await page.goto("/#work/" + work.id);
  await page.getByRole("button", { name: "Start session", exact: true }).click();
  await expect(
    page.getByText(/Frozen \(0\/0\) — session will queue/),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Provider", exact: true })
    .selectOption("antigravity");
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(page.getByText(/Queued for/i)).toBeVisible();
  await page
    .getByRole("button", { name: "Cancel queued dispatch", exact: true })
    .click();
  await expect(page.getByText("Queued dispatch cancelled.")).toBeVisible();
});
