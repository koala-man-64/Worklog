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

test("an agent's file reaches a work item only once the person approves it", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  // Data persists between local runs, so everything here is found by its own words.
  const suffix = crypto.randomUUID().slice(0, 8);
  const name = `parser-report-${suffix}.txt`;
  const report = `Parser findings ${suffix}`;
  await page.goto("/");
  // Bootstrap after the page has its session, so both share one CSRF token.
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  async function call(
    method: "POST" | "PATCH",
    path: string,
    data: unknown,
    token?: string,
    contentType?: string,
  ) {
    const response = await page.request.fetch("/api/v1" + path, {
      method,
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        ...(contentType ? { "Content-Type": contentType } : {}),
        ...(token
          ? { Authorization: "Bearer " + token }
          : { Origin: origin, "X-CSRF-Token": csrfToken }),
      },
      data,
    });
    expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
    return response.json();
  }
  const item = await call("POST", "/work-items", {
    title: `Parser fix ${suffix}`,
  });
  const { token } = await call("POST", "/credentials", {
    label: `File agent ${suffix}`,
  });
  // The agent drafts, uploads its evidence, names it in the change, then submits.
  const draft = await call(
    "POST",
    "/proposals",
    { title: `Attach ${name}`, rationale: "The findings", operations: [] },
    token,
  );
  const evidence = await call(
    "POST",
    `/attachments?ownerType=proposal&ownerId=${draft.id}&name=${name}`,
    report,
    token,
    "text/plain",
  );
  const updated = await call(
    "PATCH",
    `/proposals/${draft.id}`,
    {
      revision: draft.revision,
      operations: [
        {
          action: "attachment.create",
          payload: {
            ownerType: "item",
            ownerId: item.id,
            attachmentId: evidence.id,
          },
        },
      ],
    },
    token,
  );
  await call(
    "POST",
    `/proposals/${draft.id}/submit`,
    { revision: updated.revision },
    token,
  );

  await page.goto(`/#work/${item.id}`);
  await expect(
    page.getByRole("heading", { name: item.title, exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name, exact: true })).toHaveCount(0);

  await page.goto(`/#review/${draft.id}`);
  const change = page.locator("article.operation");
  await expect(change).toContainText(
    `Approval attaches a copy of ${name} (${report.length} B), from this proposal’s files.`,
  );
  await page
    .getByRole("button", { name: "Approve proposal", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Confirm approve", exact: true })
    .click();
  await expect(page.getByText(/approved, revision/)).toBeVisible();

  await page.goto(`/#work/${item.id}`);
  const attached = page.getByRole("link", { name, exact: true });
  await expect(attached).toBeVisible();
  const download = await page.request.get(
    (await attached.getAttribute("href"))!,
  );
  expect(await download.text()).toBe(report);
});
