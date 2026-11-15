import { expect, test } from "@playwright/test";

test("live shows agents taking work, overlapping on repositories and communicating, on its page and inside a roadmap", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID();
  const builderLabel = "Live builder " + suffix;
  const reviewerLabel = "Live reviewer " + suffix;
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

  const roadmapTitle = "Live roadmap " + suffix;
  const roadmap = await write("/roadmaps", {
    title: roadmapTitle,
    steps: [{ title: "Live step " + suffix }],
  });
  const stepId = roadmap.steps[0].id as string;
  const itemTitle = "Live work " + suffix;
  const item = await write("/work-items", {
    title: itemTitle,
    roadmapId: roadmap.id,
    stepId,
  });
  const repoName = "Live repo " + suffix;
  const repository = await write("/repositories", { name: repoName });
  const builder = await write("/credentials", { label: builderLabel });
  const reviewer = await write("/credentials", { label: reviewerLabel });

  const builderRun = await write(
    "/agent-runs",
    {
      clientRunKey: "builder-" + suffix,
      displayName: "Builder run",
      provider: "codex",
      externalChatId: "live-chat-" + suffix,
    },
    builder.token,
  );
  const reviewerRun = await write(
    "/agent-runs",
    {
      clientRunKey: "reviewer-" + suffix,
      displayName: "Reviewer run",
      provider: "claude",
    },
    reviewer.token,
  );
  await write("/chats", {
    ownerType: "roadmap",
    ownerId: roadmap.id,
    chatId: "live-chat-" + suffix,
    title: "Live chat " + suffix,
    stepIds: [stepId],
  });
  await write(
    "/participations",
    {
      runId: builderRun.id,
      itemId: item.id,
      description: "Implementing the live view",
      contexts: [{ repositoryId: repository.id, branch: "codex/live-ui" }],
    },
    builder.token,
  );
  await write(
    "/participations",
    {
      runId: reviewerRun.id,
      itemId: item.id,
      description: "Reviewing the live API",
      contexts: [{ repositoryId: repository.id, branch: "claude/live-review" }],
    },
    reviewer.token,
  );
  await write(
    "/discussions",
    {
      title: "Live coordination " + suffix,
      text: "Ready for peer check on the live endpoint",
      contexts: [{ type: "roadmap", id: roadmap.id }],
      acknowledgement: {
        mode: "all",
        targets: [{ kind: "agent", id: reviewer.agentId }],
      },
    },
    builder.token,
  );
  const proposal = await write(
    "/proposals",
    {
      title: "Live proposal " + suffix,
      rationale: "Update item status",
      operations: [
        {
          action: "item.update",
          payload: { id: item.id, revision: item.revision, status: "doing" },
        },
      ],
    },
    builder.token,
  );
  await write(
    `/proposals/${proposal.id}/submit`,
    { revision: proposal.revision },
    builder.token,
  );

  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Live", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Live", level: 1, exact: true }),
  ).toBeVisible();
  await page.getByRole("combobox", { name: "Filter by roadmap" }).selectOption(roadmap.id);
  const flow = page.locator(".live-canvas");
  await expect(flow.locator("h3", { hasText: itemTitle })).toBeVisible();
  await expect(flow.locator("h3", { hasText: builderLabel })).toBeVisible();
  await expect(flow.locator("h3", { hasText: reviewerLabel })).toBeVisible();
  await expect(flow.locator("h3", { hasText: repoName })).toBeVisible();
  await expect(flow.getByText("Overlap: 2 agents")).toBeVisible();
  await expect(flow.getByText("codex/live-ui")).toBeVisible();

  await page.getByRole("button", { name: "Swimlanes", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Agent activity swimlanes" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Previous pulse" }).click();
  await expect(
    page.getByRole("complementary", { name: "Selection inspector" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Live now", exact: true }).click();

  await page.getByText("Show as table", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Active connections", exact: true }),
  ).toBeVisible();

  await page.goto(`/#roadmaps/${roadmap.id}`);
  await page
    .getByRole("navigation", { name: "Roadmap views" })
    .getByRole("link", { name: "Live", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`#roadmaps/${roadmap.id}/live$`));
  await expect(
    page.getByRole("combobox", { name: "Filter by roadmap" }),
  ).toHaveCount(0);
  await expect(page.getByText("Overlap: 2 agents")).toBeVisible();
});
