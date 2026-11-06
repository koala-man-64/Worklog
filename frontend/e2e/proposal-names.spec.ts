import { expect, test } from "@playwright/test";

test("one agent proposal brings in a person, a repository, work, a ticket and a page, connected by name", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  // Data persists between local runs, so everything here is found by its own words.
  const suffix = crypto.randomUUID().slice(0, 8);
  const repository = `payments-${suffix}`;
  const work = `Fix settlement rounding ${suffix}`;
  const ticket = `Settlement rounds half-cents down ${suffix}`;
  const runbook = `Settlement runbook ${suffix}`;
  await page.goto("/");
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
    data: { label: `Onboarder ${suffix}` },
  });
  expect(issued.ok()).toBeTruthy();
  const agent = (await issued.json()) as { token: string };
  const headers = () => ({
    Authorization: "Bearer " + agent.token,
    "Idempotency-Key": crypto.randomUUID(),
  });
  const drafted = await page.request.post("/api/v1/proposals", {
    headers: headers(),
    data: {
      title: `Bring in payments ${suffix}`,
      rationale: "Everything the payments work needs, connected.",
      operations: [
        {
          action: "person.create",
          payload: { id: "@lead", name: `Jane Doe ${suffix}` },
        },
        {
          action: "repository.create",
          payload: {
            id: "@api",
            name: repository,
            url: `https://github.com/acme/${repository}`,
          },
        },
        {
          action: "item.create",
          payload: { id: "@fix", title: work, repositoryIds: ["@api"] },
        },
        {
          action: "person.link",
          payload: {
            personId: "@lead",
            targetType: "repository",
            targetId: "@api",
            relationship: "Owner",
          },
        },
        {
          action: "ticket.create",
          payload: { title: ticket, itemId: "@fix", repositoryIds: ["@api"] },
        },
        {
          action: "resource.create",
          payload: { title: runbook, summary: "How to rerun a settlement" },
        },
      ],
    },
  });
  expect(drafted.ok(), await drafted.text()).toBeTruthy();
  const proposal = (await drafted.json()) as { id: string; revision: number };
  const submitted = await page.request.post(
    `/api/v1/proposals/${proposal.id}/submit`,
    { headers: headers(), data: { revision: proposal.revision } },
  );
  expect(submitted.ok(), await submitted.text()).toBeTruthy();

  await page.goto("/#review/" + proposal.id);
  await expect(
    page.getByText(
      "@api names this new record for later changes in this proposal.",
    ),
  ).toBeVisible();
  await expect(page.getByText("@api: created by change 2")).toHaveCount(3);
  await expect(page.getByText("@fix: created by change 3")).toBeVisible();
  await expect(page.getByText(/^Malformed target/)).toHaveCount(0);
  await page
    .getByRole("button", { name: "Approve proposal", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Confirm approve", exact: true })
    .click();
  await expect(page.getByText(/approved, revision/)).toBeVisible();

  // The ticket names the new repository and the new work item.
  await page.goto("/#tickets");
  await page.getByRole("textbox", { name: "Find tickets" }).fill(suffix);
  const row = page.locator("article.ticket-row").filter({ hasText: ticket });
  await expect(row).toHaveCount(1);
  await expect(row.getByRole("link", { name: repository })).toBeVisible();
  await row.getByRole("link", { name: new RegExp(ticket) }).click();
  await expect(
    page.getByRole("link", { name: work, exact: true }),
  ).toBeVisible();

  await page.goto("/#resources");
  await page.getByRole("searchbox", { name: "Find pages" }).fill(suffix);
  await expect(
    page
      .getByRole("region", { name: "Pages" })
      .getByText(runbook, { exact: true }),
  ).toBeVisible();
});
