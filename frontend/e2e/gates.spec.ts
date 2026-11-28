import { expect, test } from "@playwright/test";

test("enforces manual, linked, and phase completion gates end-to-end", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  expect(boot.ok(), await boot.text()).toBeTruthy();
  const { csrfToken } = await boot.json();

  async function api(
    method: "POST" | "PATCH",
    path: string,
    data: unknown,
  ): Promise<{ id: string; revision?: number }> {
    const response = await page.request.fetch("/api/v1" + path, {
      method,
      headers: {
        Origin: origin,
        "X-CSRF-Token": csrfToken,
        "Idempotency-Key": crypto.randomUUID(),
      },
      data,
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }

  const runTag = crypto.randomUUID().slice(0, 8);
  const ticketTitle = `Fix release crash ${runTag}`;
  const phaseId = crypto.randomUUID();
  const leaf1Id = crypto.randomUUID();
  const leaf2Id = crypto.randomUUID();
  const roadmap = await api("POST", "/roadmaps", {
    title: `Release gates e2e ${runTag}`,
    overview: "Verify leaf blocking, linked auto-eval, and phase rollup.",
    steps: [
      { id: phaseId, title: "Launch phase", position: 0 },
      {
        id: leaf1Id,
        title: "Security hardening",
        parentId: phaseId,
        position: 0,
      },
      {
        id: leaf2Id,
        title: "Stabilize checkout",
        parentId: phaseId,
        position: 1,
      },
    ],
  });
  const ticket = await api("POST", "/tickets", {
    title: ticketTitle,
    kind: "bug",
    priority: "high",
  });

  // 1. Leaf 1: add a manual Human-only gate, observe blocked completion, pass gate, then complete.
  await page.goto(`/#roadmaps/${roadmap.id}/${leaf1Id}`);
  const gatesSection = page.getByRole("region", { name: "Step gates" });
  await gatesSection.getByRole("button", { name: "Add gate", exact: true }).click();
  await gatesSection.getByLabel("New gate title").fill("Security sign-off");
  await gatesSection
    .getByLabel("New gate criteria")
    .fill("Threat model reviewed by owner.");
  await gatesSection
    .getByRole("checkbox", { name: /Human-only/ })
    .check();
  await gatesSection
    .getByRole("button", { name: "Create gate", exact: true })
    .click();

  await expect(gatesSection.getByText("Security sign-off")).toBeVisible();
  await expect(gatesSection.getByText("Human-only", { exact: true })).toBeVisible();

  const leaf1Progress = page.getByLabel("Progress for Security hardening");
  await leaf1Progress.selectOption("done");
  await expect(page.getByText(/Security sign-off/)).toBeVisible();
  await expect(leaf1Progress).toHaveValue("open");

  await gatesSection.getByRole("button", { name: "Pass gate", exact: true }).click();
  await gatesSection
    .getByLabel("Verification note (optional)")
    .fill("Signed off after pentest.");
  await gatesSection
    .getByRole("button", { name: "Confirm pass", exact: true })
    .click();
  await expect(gatesSection.getByText("Passed", { exact: true })).toBeVisible();

  await leaf1Progress.selectOption("done");
  await expect(leaf1Progress).toHaveValue("done");

  // 2. Leaf 2: link a ticket gate that auto-evaluates when the ticket is closed.
  await page.goto(`/#roadmaps/${roadmap.id}/${leaf2Id}`);
  await gatesSection.getByRole("button", { name: "Add gate", exact: true }).click();
  await gatesSection.getByLabel("New gate title").fill("Blocker ticket closed");
  await gatesSection.getByLabel("Linked record type").selectOption("ticket");
  await gatesSection
    .getByRole("button", { name: "Find ticket", exact: true })
    .click();
  await gatesSection.getByLabel("Ticket search").fill(ticketTitle);
  await gatesSection
    .getByRole("button", { name: new RegExp(ticketTitle) })
    .click();
  await gatesSection
    .getByRole("button", { name: "Create gate", exact: true })
    .click();

  await expect(gatesSection.getByText("Blocker ticket closed")).toBeVisible();
  await expect(gatesSection.getByText("Auto-evaluated", { exact: true })).toBeVisible();
  await expect(gatesSection.getByText("Open", { exact: true })).toBeVisible();

  const leaf2Progress = page.getByLabel("Progress for Stabilize checkout");
  await leaf2Progress.selectOption("done");
  await expect(leaf2Progress).toHaveValue("open");

  // Close the ticket; gate should auto-evaluate to Passed and allow completion.
  await api("PATCH", `/tickets/${ticket.id}`, {
    revision: ticket.revision ?? 1,
    status: "done",
    resolution: "Fixed crash in checkout worker.",
  });
  await page.reload();
  await expect(gatesSection.getByText("Passed", { exact: true })).toBeVisible();
  await page.getByLabel("Progress for Stabilize checkout").selectOption("done");
  await expect(page.getByLabel("Progress for Stabilize checkout")).toHaveValue("done");

  // 3. Parent phase: add a phase gate and verify parent holds at doing until waived.
  await page.goto(`/#roadmaps/${roadmap.id}/${phaseId}`);
  const phaseProgress = page.getByLabel("Progress for Launch phase");
  await expect(phaseProgress).toHaveValue("done");

  await gatesSection.getByRole("button", { name: "Add gate", exact: true }).click();
  await gatesSection.getByLabel("New gate title").fill("Executive go/no-go");
  await gatesSection
    .getByRole("button", { name: "Create gate", exact: true })
    .click();

  await expect(phaseProgress).toHaveValue("doing");
  await expect(
    gatesSection.getByText(/All child steps are done, but this step stays in progress/),
  ).toBeVisible();

  // Check Status view rollup shows the open phase gate.
  await page.goto(`/#roadmaps/${roadmap.id}`);
  const rollup = page.getByRole("region", { name: "Roadmap gates summary" });
  await expect(rollup.getByText("Executive go/no-go")).toBeVisible();
  await expect(rollup.getByText("Phase gate", { exact: true })).toBeVisible();

  // Return to parent phase and waive the gate with a reason; phase rolls up to done.
  await page.goto(`/#roadmaps/${roadmap.id}/${phaseId}`);
  await gatesSection.getByRole("button", { name: "Waive", exact: true }).click();
  await gatesSection
    .getByLabel("Waive reason (required)")
    .fill("Pre-approved during weekly review.");
  await gatesSection
    .getByRole("button", { name: "Confirm waive", exact: true })
    .click();

  await expect(gatesSection.getByText("Waived", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Progress for Launch phase")).toHaveValue("done");
});
