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
  // In progress, with nobody on it: the roadmap's board shows that, Live does not.
  const quietTitle = "Quiet work " + suffix;
  const quiet = await write("/work-items", {
    title: quietTitle,
    roadmapId: roadmap.id,
    stepId,
  });
  const started = await page.request.patch("/api/v1/work-items/" + quiet.id, {
    headers: { Origin: origin, "X-CSRF-Token": csrfToken },
    data: { revision: quiet.revision, status: "doing" },
  });
  expect(started.ok(), await started.text()).toBeTruthy();
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
  // Until the filtered answer arrives, the last one stays: earlier runs' repositories may show too.
  const repoCard = flow.locator(".live-card", { hasText: repoName });
  await expect(repoCard.getByText("Overlap: 2 agents")).toBeVisible();
  await expect(repoCard.getByText("codex/live-ui")).toBeVisible();
  await expect(flow.locator("h3", { hasText: quietTitle })).toHaveCount(0);
  // The proposal waits on the person; the builder's request waits on the reviewer.
  const tile = (label: string) =>
    page.locator(".live-summary > div", { hasText: label }).locator("dd");
  await expect(tile("Needs you")).toHaveText("1");
  await expect(tile("Waiting on agents")).toHaveText("1");

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
  // Beside the roadmap's other views, its Live sits in a panel: each agent with its work and
  // repository, and nothing nobody works on.
  const panel = page.getByRole("complementary", { name: "Live activity" });
  await expect(
    panel.getByRole("link", { name: builderLabel, exact: true }),
  ).toBeVisible();
  const builderTies = panel.getByRole("list", {
    name: `${builderLabel} works on`,
  });
  await expect(
    builderTies.getByRole("link", { name: itemTitle, exact: true }),
  ).toBeVisible();
  await expect(
    builderTies.getByRole("link", { name: repoName, exact: true }),
  ).toBeVisible();
  await expect(builderTies.getByText("codex/live-ui")).toBeVisible();
  await expect(panel.getByText("Overlap: 2 agents")).toBeVisible();
  await expect(panel.getByRole("link", { name: quietTitle })).toHaveCount(0);

  await page
    .getByRole("navigation", { name: "Roadmap views" })
    .getByRole("link", { name: "Live", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`#roadmaps/${roadmap.id}/live$`));
  await expect(
    page.getByRole("combobox", { name: "Filter by roadmap" }),
  ).toHaveCount(0);
  await expect(page.getByText("Overlap: 2 agents")).toBeVisible();
  // The Live tab is the whole of it, so the panel steps aside there.
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Live panel" })).toHaveCount(0);

  // Hidden, it stays hidden, through a reload too, until the header opens it again.
  await page.goto(`/#roadmaps/${roadmap.id}/outline`);
  await panel.getByRole("button", { name: "Hide Live panel" }).click();
  await expect(panel).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("region", { name: "Plan outline" }),
  ).toBeVisible();
  const toggle = page.getByRole("button", { name: "Live panel" });
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(panel).toHaveCount(0);
  await toggle.click();
  await expect(
    panel.getByRole("link", { name: builderLabel, exact: true }),
  ).toBeVisible();
});
