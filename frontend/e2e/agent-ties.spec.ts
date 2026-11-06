import { expect, test } from "@playwright/test";

test("the person ties an agent from a record and from its page, and an agent ties itself", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID();
  const label = "Tied agent " + suffix;
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  async function write(path: string, data: unknown, token?: string) {
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
  const item = await write("/work-items", { title: "Tie work " + suffix });
  const resource = await write("/resources", {
    title: "Tie runbook " + suffix,
    summary: "How we release",
  });
  const { token, agentId } = await write("/credentials", { label });

  // The person ties the agent on the work item's page, with its role there.
  await page.goto("/#work/" + item.id);
  const agents = page.getByRole("region", { name: "Agents" });
  await agents
    .getByRole("combobox", { name: "Choose agent" })
    .selectOption({ label });
  await agents
    .getByRole("textbox", { name: "Agent's role" })
    .fill("Implementer");
  await agents.getByRole("button", { name: "Tie agent" }).click();
  await expect(
    agents.getByRole("link", { name: label, exact: true }),
  ).toHaveAttribute("href", "#agents/" + agentId);
  await expect(
    agents.getByRole("textbox", { name: "Role for " + label }),
  ).toHaveValue("Implementer");
  await expect(agents.getByText("By you")).toBeVisible();

  // The agent ties itself to the resource, with no proposal.
  await write(
    "/agent-links",
    { targetType: "resource", targetId: resource.id, relationship: "Follows" },
    token,
  );

  // Its page lists both by kind, with who set each.
  await page.goto("/#agents/" + agentId);
  const tied = page.getByRole("region", { name: "Tied to" });
  await expect(
    tied
      .getByRole("region", { name: "Tied work items" })
      .getByRole("link", { name: item.title, exact: true }),
  ).toHaveAttribute("href", "#work/" + item.id);
  const resources = tied.getByRole("region", { name: "Tied resources" });
  await expect(
    resources.getByRole("link", { name: resource.title, exact: true }),
  ).toBeVisible();
  await expect(resources.getByText("By the agent")).toBeVisible();

  // The resource keeps its page as it reads, with the agent folded under it.
  await page.goto("/#resources/" + resource.id);
  const fold = page.locator("details.agent-ties-fold");
  await expect(fold.locator("summary")).toContainText("Agents 1");
  await fold.locator("summary").click();
  await expect(
    fold.getByRole("link", { name: label, exact: true }),
  ).toBeVisible();
  await expect(fold.getByText("By the agent")).toBeVisible();

  // Unlink the work item from the agent's page.
  await page.goto("/#agents/" + agentId);
  await tied
    .getByRole("button", { name: "Unlink " + item.title, exact: true })
    .click();
  await expect(
    tied.getByRole("region", { name: "Tied work items" }),
  ).toHaveCount(0);

  // The agent reads what it is still tied to in its context.
  const context = await page.request.get("/api/v1/context", {
    headers: { Authorization: "Bearer " + token },
  });
  const { ties } = await context.json();
  expect(
    ties.items.map(
      (t: { targetType: string; targetId: string; relationship: string }) => [
        t.targetType,
        t.targetId,
        t.relationship,
      ],
    ),
  ).toEqual([["resource", resource.id, "Follows"]]);
});
