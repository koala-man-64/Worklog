import { expect, test } from "@playwright/test";

test("repositories connect work, named agent reports, chat badges and human run cleanup", async ({
  page,
}) => {
  const suffix = crypto.randomUUID();
  const repoName = "Coordination " + suffix;
  const workName = "Review shared API " + suffix;
  const chatId = crypto.randomUUID();
  const reviewerName = "API reviewer " + suffix;
  const builderName = "API builder " + suffix;
  const unattachedName = "Unattached run " + suffix;
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Repositories", exact: true })
    .click();
  await expect(page).toHaveURL(/#repositories$/);
  await expect(
    page.getByRole("heading", { name: "Repositories", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Repository name", exact: true })
    .fill(repoName);
  await page
    .getByRole("textbox", { name: "Repository remote URL" })
    .fill(`https://github.com/example/${suffix}`);
  await page
    .getByRole("textbox", { name: "Repository remote URL" })
    .press("Enter");
  // Another address of the same remote links to its record instead of adding one.
  await expect(
    page.getByRole("textbox", { name: "Repository name", exact: true }),
  ).toHaveValue("");
  await page
    .getByRole("textbox", { name: "Repository name", exact: true })
    .fill(repoName + " over SSH");
  await page
    .getByRole("textbox", { name: "Repository remote URL" })
    .fill(`git@github.com:Example/${suffix}.git`);
  await page
    .getByRole("textbox", { name: "Repository remote URL" })
    .press("Enter");
  await expect(
    page.getByRole("status").filter({ hasText: "already registered" }),
  ).toHaveText(`That remote is already registered as ${repoName}.`);
  await page
    .getByRole("textbox", { name: "Find repositories", exact: true })
    .fill(repoName);
  // The list row's link: the notice above links to the same record by the same name.
  const repositoryLink = page
    .getByRole("article")
    .getByRole("link", { name: repoName, exact: true });
  const repoHref = await repositoryLink.getAttribute("href");
  expect(repoHref).toBeTruthy();
  const repositoryId = repoHref!.split("/")[1];

  await page.goto("/#work");
  await page.getByRole("textbox", { name: "New work item" }).fill(workName);
  await page
    .getByRole("textbox", { name: "New work repositories search", exact: true })
    .fill(repoName);
  await page
    .getByRole("group", { name: "New work repositories", exact: true })
    .getByRole("checkbox", { name: new RegExp(repoName) })
    .check();
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  await page.getByRole("textbox", { name: "Find repository filter" }).fill(repoName);
  await page
    .getByRole("combobox", { name: "Filter work by repository" })
    .selectOption(repositoryId);
  const workLink = page.getByRole("link", { name: workName, exact: true });
  const workHref = await workLink.getAttribute("href");
  const itemId = workHref!.split("/")[1];
  await workLink.click();
  await page
    .getByRole("textbox", { name: "Agent chat title", exact: true })
    .fill("Shared API chat");
  await page
    .getByRole("textbox", { name: "Agent chat ID", exact: true })
    .fill(chatId);
  await page
    .getByRole("textbox", { name: "Agent chat repositories", exact: true })
    .fill("legacy/unresolved-" + suffix);
  await page
    .getByRole("textbox", {
      name: "Agent chat registered repositories search",
      exact: true,
    })
    .fill(repoName);
  await page
    .getByRole("group", {
      name: "Agent chat registered repositories",
      exact: true,
    })
    .getByRole("checkbox", { name: new RegExp(repoName) })
    .check();
  await page.getByRole("button", { name: "Add chat", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "Messages of Shared API chat" }),
  ).toBeVisible();

  await page.goto("/#settings");
  await page
    .getByRole("textbox", { name: "Credential label" })
    .fill("Coordination browser agent");
  await page
    .getByRole("button", { name: "Create credential", exact: true })
    .click();
  const token = await page.locator(".token code").innerText();
  await page.getByRole("button", { name: "I have saved it" }).click();
  async function post(path: string, data: unknown) {
    const response = await page.request.post("/api/v1" + path, {
      headers: {
        Authorization: "Bearer " + token,
        "Idempotency-Key": crypto.randomUUID(),
      },
      data,
    });
    expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
    return response.json();
  }
  const run = await post("/agent-runs", {
    clientRunKey: crypto.randomUUID(),
    displayName: reviewerName,
    provider: "OpenAI",
    externalChatId: chatId,
  });
  const peer = await post("/agent-runs", {
    clientRunKey: crypto.randomUUID(),
    displayName: builderName,
  });
  await post("/agent-runs", {
    clientRunKey: crypto.randomUUID(),
    displayName: unattachedName,
  });
  const participation = await post("/participations", {
    runId: run.id,
    description: "Reviewing shared transactions",
    contexts: [
      {
        repositoryId,
        worktreePath: "C:/review/shared-api",
        branch: "codex/review",
      },
    ],
  });
  await post("/participations", {
    runId: peer.id,
    itemId,
    description: "Implementing API",
    contexts: [{ repositoryId, branch: "codex/implementation" }],
  });
  await post(`/participations/${participation.id}/updates`, {
    kind: "blocker",
    text: "Waiting for contract review",
  });

  await page.goto("/" + repoHref);
  await expect(
    page.getByRole("heading", { name: reviewerName, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: builderName, exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator("article.coordination-report")
      .getByText("C:/review/shared-api", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Waiting for contract review", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Reviewing shared transactions", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Activity of API reviewer" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: workName, exact: true }),
  ).toBeVisible();

  // The blocker predates the work association: its immutable feed target stays
  // repository-only, but the participation history must remain reachable.
  const attached = await page.request.patch(
    `/api/v1/participations/${participation.id}`,
    {
      headers: { Authorization: "Bearer " + token },
      data: { revision: participation.revision + 1, itemId },
    },
  );
  expect(attached.ok(), await attached.text()).toBeTruthy();

  await page.goto("/" + workHref);
  await expect(
    page.getByRole("heading", { name: builderName, exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("group", { name: "Linked repositories", exact: true })
      .getByRole("checkbox", { name: new RegExp(repoName) }),
  ).toBeChecked();
  const reviewerCard = page.locator("article.coordination-report").filter({
    has: page.getByRole("heading", { name: reviewerName, exact: true }),
  });
  await reviewerCard
    .getByText("Participation history", { exact: true })
    .click();
  await expect(
    reviewerCard.getByText("Waiting for contract review", { exact: true }),
  ).toBeVisible();
  await reviewerCard
    .getByText("Reported context when posted", { exact: true })
    .click();
  await expect(
    reviewerCard.getByText("C:/review/shared-api", { exact: true }),
  ).toHaveCount(2);
  await page.goto("/#chats");
  await expect(
    page.getByText("legacy/unresolved-" + suffix + " · Unregistered", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText(reviewerName, { exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: repoName, exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Observed transcript activity/i)).toBeVisible();
  await page
    .getByRole("group", { name: "Show activity by" })
    .getByRole("button", { name: "Work item", exact: true })
    .click();
  const group = page.getByRole("list", { name: "Chats in " + workName, exact: true });
  await expect(
    group.getByRole("link", { name: repoName, exact: true }),
  ).toBeVisible();
  await expect(group.getByText(reviewerName, { exact: true })).toBeVisible();

  await page.goto("/#repositories");
  await page.getByText("Open agent runs", { exact: true }).click();
  await page
    .getByRole("button", { name: "End run " + unattachedName, exact: true })
    .click();
  await page
    .getByRole("button", { name: "Confirm end run", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "End run " + unattachedName,
      exact: true,
    }),
  ).toHaveCount(0);
  await page
    .getByText("Reported activity across repositories", { exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: reviewerName, exact: true }),
  ).toBeVisible();
});

test("a person merges a moved repository's record and its old remote follows", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID();
  const oldName = "Old home " + suffix;
  const newName = "New home " + suffix;
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
  async function write(path: string, data: unknown) {
    const response = await page.request.post("/api/v1" + path, {
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        Origin: origin,
        "X-CSRF-Token": csrfToken,
      },
      data,
    });
    expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
    return response.json();
  }
  const old = await write("/repositories", {
    name: oldName,
    url: `https://github.com/old-owner/${suffix}`,
  });
  const moved = await write("/repositories", {
    name: newName,
    url: `https://github.com/new-owner/${suffix}`,
  });
  const work = await write("/work-items", {
    title: "Work on the old home " + suffix,
    repositoryIds: [old.id],
  });

  await page.goto("/#repositories/" + old.id);
  await page.getByText("Merge into another repository").click();
  await page
    .getByRole("textbox", { name: "Find a repository to merge into" })
    .fill(newName);
  await page
    .getByRole("combobox", { name: "Merge into" })
    .selectOption(moved.id);
  await page
    .getByRole("button", { name: `Merge into ${newName}…`, exact: true })
    .click();
  await expect(
    page.getByText(`Merge ${oldName} into ${newName}?`),
  ).toBeVisible();
  await page.getByRole("button", { name: "Confirm merge" }).click();

  // The survivor now holds the work, and the merged record names it.
  await expect(page).toHaveURL(new RegExp(`#repositories/${moved.id}$`));
  await expect(page.getByRole("heading", { name: newName })).toBeVisible();
  await expect(
    page.getByRole("link", { name: work.title, exact: true }),
  ).toBeVisible();
  await page.goto("/#repositories/" + old.id);
  await expect(page.getByText(/now answers for its remote/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Recover repository" }),
  ).toHaveCount(0);
  // Registering the old remote again links to the survivor.
  const again = await write("/repositories", {
    name: oldName,
    url: `git@github.com:old-owner/${suffix}.git`,
  });
  expect([again.id, again.created]).toEqual([moved.id, false]);
});
