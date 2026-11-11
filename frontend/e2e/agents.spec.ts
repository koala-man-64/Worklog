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
  const { token, agentId } = await write("/credentials", { label });
  const chatId = "registry-chat-" + suffix;
  const run = await write(
    "/agent-runs",
    {
      clientRunKey: suffix,
      displayName: "Registry run",
      provider: "claude",
      externalChatId: chatId,
    },
    token,
  );
  await write("/chats", {
    ownerType: "roadmap",
    ownerId: roadmap.id,
    chatId,
    title: "Registry chat " + suffix,
    stepIds: [roadmap.steps[0].id],
  });
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
  // Data persists between local runs, so the agent is found by its own words.
  const index = page.getByRole("region", { name: "Agent index" });
  await index.getByRole("searchbox", { name: "Find agents" }).fill(suffix);
  await index.getByRole("button", { name: new RegExp("^" + label) }).click();
  const card = page
    .getByRole("region", { name: "Agent card" })
    .getByRole("article", { name: label, exact: true });
  await expect(card.getByText("Reporting work", { exact: true })).toBeVisible();
  await expect(
    card.getByRole("link", { name: item.title, exact: true }),
  ).toHaveAttribute("href", "#work/" + item.id);
  await expect(
    card.getByRole("link", { name: `${repository.name} · claude/registry` }),
  ).toBeVisible();
  // The card names the repositories and roadmaps its work took it to.
  const repositories = card.getByRole("region", { name: "Repositories" });
  await expect(
    repositories.getByRole("link", { name: repository.name, exact: true }),
  ).toHaveAttribute("href", "#repositories/" + repository.id);
  await expect(
    repositories.getByText("Current", { exact: true }),
  ).toBeVisible();
  const cardRoadmaps = card.getByRole("region", { name: "Roadmaps" });
  await expect(
    cardRoadmaps.getByRole("link", { name: roadmap.title, exact: true }),
  ).toHaveAttribute("href", "#roadmaps/" + roadmap.id);
  await expect(cardRoadmaps).toContainText("Reported work · Discussion");

  await card.getByRole("link", { name: label, exact: true }).click();
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

  // The roadmap's plan shows the agent beside the step its work sits on.
  await page.goto("/#roadmaps/" + roadmap.id);
  const step = page
    .getByRole("region", { name: "Plan outline" })
    .locator(".step-row")
    .filter({
      has: page.getByRole("link", { name: "Registry step", exact: true }),
    });
  await expect(
    step.getByRole("link", { name: label, exact: true }),
  ).toHaveAttribute("href", "#agents/" + agentId);
  // The overview lists it under Agents from that same work, with no tie set by hand.
  const listed = page
    .getByRole("region", { name: "Roadmap details" })
    .getByRole("region", { name: "Agents", exact: true })
    .locator(".agent-tie-row")
    .filter({ has: page.getByRole("link", { name: label, exact: true }) });
  await expect(listed).toContainText(
    "Current" + "Reported work" + "Discussion" + "Tracked chat",
  );

  // The registry joins tracked chats, so it stays the person's view.
  for (const path of ["/agents", `/roadmaps/${roadmap.id}/agents`]) {
    const denied = await page.request.get("/api/v1" + path, {
      headers: { Authorization: "Bearer " + token },
    });
    expect(denied.status(), path).toBe(403);
  }

  // Once the agent ends its run, it is finished there, though its chat stays tracked.
  await write(`/agent-runs/${run.id}/end`, { revision: run.revision }, token);
  const workers = page.waitForResponse((response) =>
    response.url().endsWith(`/api/v1/roadmaps/${roadmap.id}/agents`),
  );
  await page.reload();
  expect((await (await workers).json()).items).toEqual([]);
  await expect(
    step.getByRole("link", { name: "Registry step", exact: true }),
  ).toBeVisible();
  await expect(
    step.getByRole("link", { name: label, exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("Agents working here")).toHaveCount(0);
  // Its work stays on the roadmap's record, no longer current, and on the step's.
  await expect(listed).toContainText("Reported work");
  await expect(listed.getByText("Current", { exact: true })).toHaveCount(0);
  await step.getByRole("link", { name: "Registry step", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Step details" })
      .getByRole("region", { name: "Agents", exact: true })
      .getByRole("link", { name: label, exact: true }),
  ).toHaveAttribute("href", "#agents/" + agentId);
});

test("an agent registers its own identity and speaks as it", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID();
  const sponsorLabel = "Shared agents " + suffix;
  const label = "Registered agent " + suffix;
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
  const sponsor = await post("/credentials", { label: sponsorLabel });
  // The agent generates its own token; Worklog keeps only its hash.
  const token = Buffer.from(
    crypto.getRandomValues(new Uint8Array(32)),
  ).toString("base64url");
  const chatId = crypto.randomUUID();
  const agent = await post(
    "/agent-registrations",
    { label, token, chatId },
    sponsor.token,
  );
  expect(agent.registeredBy.label).toBe(sponsorLabel);
  expect(agent.chatId).toBe(chatId);
  const posted = await post(
    "/discussions",
    { title: "Self registered " + suffix, text: "Speaking for myself" },
    token,
  );
  expect(posted.message.authorId).toBe(agent.agentId);

  await page.goto("/#agents/" + agent.agentId);
  await expect(
    page.getByRole("heading", { name: label, exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Registered by " + sponsorLabel)).toBeVisible();
  await page.goto("/#settings");
  await expect(page.getByText("Registered by " + sponsorLabel)).toBeVisible();
  await expect(
    page.getByRole("link", { name: sponsorLabel, exact: true }),
  ).toHaveAttribute("href", "#agents/" + sponsor.agentId);
  // Revoking the issued credential would take the registered agent with it.
  const issuedRow = page
    .locator("div.line")
    .filter({ has: page.locator("strong", { hasText: sponsorLabel }) })
    .filter({ has: page.getByRole("button") });
  await expect(
    issuedRow.getByRole("button", { name: "Revoke with 1 registered" }),
  ).toBeVisible();

  // Retiring revokes only the registered identity, never its sponsor.
  await post("/agent-registrations/retire", {}, token);
  const retired = await page.request.get("/api/v1/context", {
    headers: { Authorization: "Bearer " + token },
  });
  expect(retired.status()).toBe(401);
  const shared = await page.request.get("/api/v1/context", {
    headers: { Authorization: "Bearer " + sponsor.token },
  });
  expect(shared.ok()).toBeTruthy();
});

test("Review says which directory a proposal's agent registered from", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  // Data persists between local runs, so everything here is found by its own words.
  const suffix = crypto.randomUUID();
  const title = "Directory proposal " + suffix;
  const directory = `C:\\Projects\\parser\\.claude\\worktrees\\${suffix}`;
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
  const sponsor = await post("/credentials", {
    label: "Directory sponsor " + suffix,
  });
  const token = Buffer.from(
    crypto.getRandomValues(new Uint8Array(32)),
  ).toString("base64url");
  // `worklog agent register` sends the directory it runs in.
  const agent = await post(
    "/agent-registrations",
    {
      label: "Directory agent " + suffix,
      token,
      chatId: crypto.randomUUID(),
      directory,
    },
    sponsor.token,
  );
  expect(agent.directory).toBe(directory);
  const drafted = await post(
    "/proposals",
    {
      title,
      rationale: "",
      operations: [{ action: "item.create", payload: { title } }],
    },
    token,
  );
  const submitted = await post(
    `/proposals/${drafted.id}/submit`,
    { revision: drafted.revision },
    token,
  );
  expect(submitted.status).toBe("submitted");

  await page.goto("/#review");
  const link = page.getByRole("link", { name: title, exact: true });
  const row = page.locator("article.roadmap-row").filter({ has: link });
  await expect(row).toContainText(`Registered from ${directory}`);
  await link.click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
  await expect(page.getByText(directory, { exact: true })).toBeVisible();
  await expect(
    page.getByText("Registered from", { exact: false }),
  ).toContainText(directory);

  await post("/agent-registrations/retire", {}, token);
});
