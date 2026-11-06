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

test("an agent proposes what a release needs, and the person approves it on Review", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  // Data persists between local runs, so everything here is found by its own words.
  const suffix = crypto.randomUUID().slice(0, 8);
  const release = `November release ${suffix}`;
  const work = `Export button ${suffix}`;
  const need = `Request the outbound firewall rule ${suffix}`;

  // The person's release and work, written before the page starts its own session.
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  const person = (path: string, data: unknown) =>
    page.request.post("/api/v1" + path, {
      headers: {
        Origin: origin,
        "X-CSRF-Token": csrfToken,
        "Idempotency-Key": crypto.randomUUID(),
      },
      data,
    });
  const created = await person("/releases", {
    title: release,
    releaseDate: "2099-11-20",
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const { id: releaseId } = (await created.json()) as { id: string };
  const item = await person("/work-items", { title: work });
  expect(item.ok(), await item.text()).toBeTruthy();
  const { id: itemId } = (await item.json()) as { id: string };
  const issued = await person("/credentials", {
    label: `Release agent ${suffix}`,
  });
  expect(issued.ok(), await issued.text()).toBeTruthy();
  const { token } = (await issued.json()) as { token: string };

  // The agent proposes the work for the release and a request it needs first.
  const agent = () => ({
    Authorization: "Bearer " + token,
    "Idempotency-Key": crypto.randomUUID(),
  });
  const drafted = await page.request.post("/api/v1/proposals", {
    headers: agent(),
    data: {
      title: `Ship the export button ${suffix}`,
      rationale: "The export calls a new host, which the firewall blocks.",
      operations: [
        {
          action: "release.add",
          payload: { releaseId, targetType: "item", targetId: itemId },
        },
        {
          action: "check.create",
          payload: {
            releaseId,
            kind: "prepare",
            text: need,
            dueDate: "2099-11-13",
          },
        },
      ],
    },
  });
  expect(drafted.ok(), await drafted.text()).toBeTruthy();
  const proposal = (await drafted.json()) as { id: string; revision: number };
  const submitted = await page.request.post(
    `/api/v1/proposals/${proposal.id}/submit`,
    { headers: agent(), data: { revision: proposal.revision } },
  );
  expect(submitted.ok(), await submitted.text()).toBeTruthy();

  // Nothing changes until the person approves on Review.
  await page.goto("/#releases/" + releaseId);
  await expect(
    page.getByRole("heading", { name: release, level: 1 }),
  ).toBeVisible();
  await expect(page.getByRole("article", { name: need })).toHaveCount(0);
  await page.goto("/#review/" + proposal.id);
  await expect(page.getByText(/^Malformed target/)).toHaveCount(0);
  await page
    .getByRole("button", { name: "Approve proposal", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Confirm approve", exact: true })
    .click();
  await expect(page.getByText(/approved, revision/)).toBeVisible();

  await page.goto("/#releases/" + releaseId);
  await expect(page.getByRole("article", { name: need })).toBeVisible();
  const items = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: /^Work items/ }) });
  await expect(items.getByRole("link", { name: work })).toBeVisible();

  // Once the request is in, the agent proposes the check done; Review compares it with the check now.
  const shown = await page.request.get("/api/v1/releases/" + releaseId, {
    headers: { Authorization: "Bearer " + token },
  });
  expect(shown.ok(), await shown.text()).toBeTruthy();
  const check = (
    (await shown.json()) as {
      checks: { id: string; text: string; revision: number }[];
    }
  ).checks.find((c) => c.text === need)!;
  const done = await page.request.post("/api/v1/proposals", {
    headers: agent(),
    data: {
      title: `Firewall rule granted ${suffix}`,
      rationale: "Network approved the rule this morning.",
      operations: [
        {
          action: "check.update",
          payload: { id: check.id, revision: check.revision, status: "done" },
        },
      ],
    },
  });
  expect(done.ok(), await done.text()).toBeTruthy();
  const update = (await done.json()) as { id: string; revision: number };
  const sent = await page.request.post(
    `/api/v1/proposals/${update.id}/submit`,
    { headers: agent(), data: { revision: update.revision } },
  );
  expect(sent.ok(), await sent.text()).toBeTruthy();
  await page.goto("/#review/" + update.id);
  const change = page
    .getByRole("heading", { name: "Change 1: check.update" })
    .locator("xpath=ancestor::article[1]");
  // Each field Review compares carries the check's current value.
  await expect(
    change.getByText("Current", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText(/^Malformed target/)).toHaveCount(0);
  await page
    .getByRole("button", { name: "Approve proposal", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Confirm approve", exact: true })
    .click();
  await expect(page.getByText(/approved, revision/)).toBeVisible();
  await page.goto("/#releases/" + releaseId);
  await expect(
    page
      .getByRole("article", { name: need })
      .getByRole("combobox", { name: "Status" }),
  ).toHaveValue("done");
});
