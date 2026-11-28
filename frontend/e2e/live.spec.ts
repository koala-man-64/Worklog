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

  const secondRoadmapTitle = "Live tree B " + suffix;
  const secondRoadmap = await write("/roadmaps", {
    title: secondRoadmapTitle,
    steps: [{ title: "Tree B step " + suffix }],
  });
  const secondStepId = secondRoadmap.steps[0].id as string;
  const secondItemTitle = "Tree B work " + suffix;
  const secondItem = await write("/work-items", {
    title: secondItemTitle,
    roadmapId: secondRoadmap.id,
    stepId: secondStepId,
  });
  const secondAgentLabel = "Tree B builder " + suffix;
  const secondAgent = await write("/credentials", { label: secondAgentLabel });
  const secondRun = await write(
    "/agent-runs",
    {
      clientRunKey: "tree-b-" + suffix,
      displayName: "Tree B run",
      provider: "codex",
    },
    secondAgent.token,
  );
  await write(
    "/participations",
    {
      runId: secondRun.id,
      itemId: secondItem.id,
      description: "Working on tree B",
    },
    secondAgent.token,
  );

  const revokedLabel = "Revoked live agent " + suffix;
  const revokedAgent = await write("/credentials", { label: revokedLabel });
  const revokedRun = await write(
    "/agent-runs",
    {
      clientRunKey: "revoked-" + suffix,
      displayName: "Revoked run",
      provider: "claude",
    },
    revokedAgent.token,
  );
  await write(
    "/participations",
    {
      runId: revokedRun.id,
      itemId: item.id,
      description: "Recently revoked participant",
    },
    revokedAgent.token,
  );
  const revokeRes = await page.request.post(`/api/v1/credentials/${revokedAgent.id}/revoke`, {
    headers: {
      Origin: origin,
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: {},
  });
  expect(revokeRes.ok(), await revokeRes.text()).toBeTruthy();

  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Live", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Live", level: 1, exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Filter by roadmap trees" }).click();
  const treeDialog = page.getByRole("dialog", { name: "Roadmap trees filter" });
  await treeDialog.getByRole("checkbox", { name: new RegExp(roadmapTitle) }).check();
  await page.keyboard.press("Escape");
  const flow = page.locator(".live-canvas");
  await expect(flow.locator("h3", { hasText: itemTitle })).toBeVisible();
  await expect(flow.locator("h3", { hasText: builderLabel })).toBeVisible();
  await expect(flow.locator("h3", { hasText: reviewerLabel })).toBeVisible();
  await expect(flow.locator("h3", { hasText: repoName })).toBeVisible();
  // Unselected roadmap tree and revoked agents stay hidden by default.
  await expect(flow.locator("h3", { hasText: secondAgentLabel })).toHaveCount(0);
  await expect(flow.locator("h3", { hasText: revokedLabel })).toHaveCount(0);
  // Toggling Show revoked reveals the recently revoked agent, and selecting a second tree adds it.
  await page.getByRole("checkbox", { name: "Show revoked" }).check();
  await expect(flow.locator("h3", { hasText: revokedLabel })).toBeVisible();
  await page.getByRole("checkbox", { name: "Show revoked" }).uncheck();
  await expect(flow.locator("h3", { hasText: revokedLabel })).toHaveCount(0);
  await page.getByRole("button", { name: "Filter by roadmap trees" }).click();
  await treeDialog.getByRole("checkbox", { name: new RegExp(secondRoadmapTitle) }).check();
  await page.keyboard.press("Escape");
  await expect(flow.locator("h3", { hasText: secondAgentLabel })).toBeVisible();
  await page
    .locator(".live-filter-strip")
    .getByRole("button", { name: new RegExp(secondRoadmapTitle) })
    .click();
  await expect(flow.locator("h3", { hasText: secondAgentLabel })).toHaveCount(0);
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

  await page.getByRole("button", { name: "Compact", exact: true }).click();
  await expect(flow).toHaveClass(/is-compact/);
  await page.getByRole("button", { name: "Compact", exact: true }).click();

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
    page.getByRole("button", { name: "Filter by roadmap trees" }),
  ).toHaveCount(0);
  await expect(page.getByText("Overlap: 2 agents")).toBeVisible();
  // The Live tab is the whole of it, so the panel steps aside there.
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Live panel" })).toHaveCount(0);

  // On Outline, Step details sits in a tab of the Live panel beside the plan.
  await page.goto(`/#roadmaps/${roadmap.id}/outline`);
  await expect(panel.getByRole("tab", { name: "Live" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page
    .getByRole("region", { name: "Plan outline" })
    .getByRole("link", { name: "Live step " + suffix, exact: true })
    .click();
  await expect(
    panel.getByRole("tab", { name: "Step details" }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    panel
      .getByRole("region", { name: "Step details" })
      .getByRole("heading", { name: "Live step " + suffix }),
  ).toBeVisible();
  await panel.getByRole("tab", { name: "Live" }).click();
  await expect(
    panel.getByRole("link", { name: builderLabel, exact: true }),
  ).toBeVisible();

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
