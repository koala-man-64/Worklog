import { expect, test } from "@playwright/test";

test("the agents tab shows who works on what, and why", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID();
  const label = "Registry agent " + suffix;
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
  const roadmap = await write("/roadmaps", {
    title: "Registry roadmap " + suffix,
    steps: [{ title: "Registry step" }],
  });
  const item = await write("/work-items", {
    title: "Registry work " + suffix,
    roadmapId: roadmap.id,
    stepId: roadmap.steps[0].id,
  });
  const repository = await write("/repositories", {
    name: "Registry repo " + suffix,
  });
  const { token } = await write("/credentials", { label });
  const run = await write(
    "/agent-runs",
    { clientRunKey: suffix, displayName: "Registry run", provider: "claude" },
    token,
  );
  await write(
    "/participations",
    {
      runId: run.id,
      itemId: item.id,
      description: "Building the registry",
      contexts: [{ repositoryId: repository.id, branch: "claude/registry" }],
    },
    token,
  );
  await write(
    "/discussions",
    {
      title: "Registry plan " + suffix,
      text: "Starting the registry",
      contexts: [{ type: "roadmap", id: roadmap.id }],
    },
    token,
  );

  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Agents", exact: true })
    .click();
  await expect(page).toHaveURL(/#agents$/);
  const row = page.locator("article.agent-row").filter({
    has: page.getByRole("link", { name: label, exact: true }),
  });
  await expect(row.getByText("Reporting work", { exact: true })).toBeVisible();
  await expect(
    row.getByRole("link", { name: item.title, exact: true }),
  ).toHaveAttribute("href", "#work/" + item.id);
  await expect(
    row.getByRole("link", { name: `${repository.name} · claude/registry` }),
  ).toBeVisible();

  await row.getByRole("link", { name: label, exact: true }).click();
  await expect(
    page.getByRole("heading", { name: label, exact: true }),
  ).toBeVisible();
  const roadmaps = page.getByRole("region", { name: "Roadmaps" });
  await expect(
    roadmaps.getByRole("link", { name: roadmap.title, exact: true }),
  ).toBeVisible();
  await expect(roadmaps.getByText("Reported work")).toBeVisible();
  await expect(roadmaps.getByText("Discussion")).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "Messages and updates" })
      .getByRole("link", { name: "Registry plan " + suffix }),
  ).toBeVisible();

  // The registry joins tracked chats, so it stays the person's view.
  const denied = await page.request.get("/api/v1/agents", {
    headers: { Authorization: "Bearer " + token },
  });
  expect(denied.status()).toBe(403);
});
