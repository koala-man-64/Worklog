import { expect, test, type BrowserContext, type Page } from "@playwright/test";

type JsonRecord = Record<string, unknown>;

const externalRequests = new WeakMap<BrowserContext, string[]>();
test.beforeEach(async ({ context, baseURL }) => {
  const blocked: string[] = [];
  externalRequests.set(context, blocked);
  const origin = new URL(baseURL!).origin;
  await context.route(/^https?:\/\//, route => {
    if (new URL(route.request().url()).origin === origin) return route.continue();
    blocked.push(route.request().url());
    return route.abort("internetdisconnected");
  });
});
test.afterEach(async ({ context }) => {
  expect(externalRequests.get(context), "No external runtime resources").toEqual([]);
});

async function refreshDiscussions(page: Page) {
  await page.getByRole("region", { name: "Discussion list", exact: true })
    .getByRole("button", { name: "Refresh", exact: true }).click();
}

function required<T>(value: T | null | undefined, label: string): T {
  if (value == null) throw new Error(`Missing ${label} in backend response`);
  return value;
}

async function createAgent(page: Page, label: string) {
  await page.goto("/#settings");
  await page.getByRole("textbox", { name: "Credential label" }).fill(label);
  await page
    .getByRole("button", { name: "Create credential", exact: true })
    .click();
  const token = await page.locator(".token code").innerText();
  await page.getByRole("button", { name: "I have saved it" }).click();
  return token;
}

async function postAsAgent(
  page: Page,
  token: string,
  path: string,
  body: unknown,
) {
  const response = await page.request.post(path, {
    headers: {
      Authorization: "Bearer " + token,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: body,
  });
  const text = await response.text();
  if (!response.ok())
    throw new Error(
      `Agent request ${path} failed (${response.status()}): ${text}`,
    );
  return JSON.parse(text) as JsonRecord;
}

async function getJson(page: Page, path: string) {
  const response = await page.request.get(path);
  const text = await response.text();
  if (!response.ok())
    throw new Error(`Request ${path} failed (${response.status()}): ${text}`);
  return JSON.parse(text) as JsonRecord;
}

test("human replies do not satisfy a request, while an agent receipt appears in the thread", async ({
  page,
}) => {
  const alphaLabel = "Agent Alpha " + Date.now();
  const alphaToken = await createAgent(page, alphaLabel);
  const discussionTitle = "Please confirm the local release notes " + Date.now();
  const settings = await getJson(page, "/api/v1/settings");
  const alphaProfileId = required(
    (settings.credentials as { label: string; agentId: string }[]).find(
      (credential) => credential.label === alphaLabel,
    )?.agentId,
    "Agent Alpha profile",
  );
  await page.goto("/#discussions");
  await expect(
    page.getByRole("heading", { name: "Discussions", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "New post", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Post message" })
    .fill(discussionTitle);
  await page
    .getByRole("checkbox", {
      name: "Request acknowledgement (received and understood)",
    })
    .check();
  await page.getByLabel("Who may acknowledge?").selectOption("any");
  await page.getByRole("checkbox", { name: alphaLabel }).check();
  await page.getByRole("button", { name: "Post discussion" }).click();
  await expect(
    page.getByRole("heading", {
      name: discussionTitle,
      exact: true,
    }),
  ).toBeVisible();
  const requestCard = page.getByRole("region", {
    name: "Acknowledgement request",
  });
  await expect(
    requestCard.getByText("Awaiting", { exact: true }),
  ).toBeVisible();

  const replyText = "I saw your note and will review it.";
  const replyBox = page.getByRole("textbox", { name: "Reply message" });
  const postedReply = page.getByRole("list", { name: "Discussion messages" })
    .getByText(replyText, { exact: true });
  await replyBox.fill(replyText);
  await page.getByRole("button", { name: "Send reply", exact: true }).click();
  await expect(postedReply).toBeVisible();
  await expect(replyBox).toHaveValue("");
  await expect(
    requestCard.getByText("Awaiting", { exact: true }),
  ).toBeVisible();

  const list = await getJson(
    page,
    "/api/v1/discussions?view=all&limit=50&offset=0",
  );
  const thread = required(
    (list.items as { id: string; title: string }[]).find(
      (item) => item.title === discussionTitle,
    ),
    "discussion thread",
  );
  const detail = await getJson(page, `/api/v1/discussions/${thread.id}`);
  const original = required(
    (
      detail.messages as {
        id: string;
        text: string;
        request: {
          targets: { id: string; recipientId: string | null }[];
        } | null;
      }[]
    ).find(
      (message) => message.text === discussionTitle,
    ),
    "original message",
  );
  expect(original.request?.targets).toHaveLength(1);
  expect(original.request?.targets[0].recipientId).toBe(alphaProfileId);
  const targetId = required(original.request, "acknowledgement request")
    .targets[0].id;
  await postAsAgent(
    page,
    alphaToken,
    `/api/v1/discussions/messages/${original.id}/acknowledge`,
    { targetIds: [targetId] },
  );
  await refreshDiscussions(page);
  await expect(
    requestCard.getByText("Satisfied", { exact: true }),
  ).toBeVisible();
  await expect(requestCard.getByText(alphaLabel + " acknowledged")).toBeVisible();
  await expect(postedReply).toBeVisible();
});

test("agent handoff offers and accepted ownership are visible in the role panel", async ({
  page,
}) => {
  const alphaLabel = "Handoff Alpha " + Date.now();
  const betaLabel = "Handoff Beta " + Date.now();
  const alphaToken = await createAgent(page, alphaLabel);
  const betaToken = await createAgent(page, betaLabel);
  const settings = await getJson(page, "/api/v1/settings");
  const credentials = settings.credentials as {
    label: string;
    agentId: string;
  }[];
  const betaId = required(
    credentials.find((credential) => credential.label === betaLabel)
      ?.agentId,
    "Handoff Beta profile",
  );

  await page.goto("/");
  const itemTitle = "Handoff context " + Date.now();
  await page.getByRole("textbox", { name: "New work item" }).fill(itemTitle);
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  await expect(
    page.getByRole("link", { name: itemTitle, exact: true }),
  ).toBeVisible();

  await page.goto("/#discussions");
  await page.getByText(/Discussion roles \(/).click();
  await page.getByText("Create an unassigned role", { exact: true }).click();
  await page.getByRole("searchbox", { name: "Role context" }).fill(itemTitle);
  await page.getByRole("button", { name: new RegExp(itemTitle) }).click();
  await page
    .getByRole("textbox", { name: "Role name" })
    .fill("Release reviewer");
  const roleCreated = page.waitForResponse(response =>
    response.url().endsWith("/api/v1/discussions/roles") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Create role", exact: true }).click();
  const roleResponse = await roleCreated;
  expect(roleResponse.ok()).toBe(true);
  const role = await roleResponse.json() as { id: string; revision: number; contextTitle: string };
  expect(role.contextTitle).toBe(itemTitle);

  const claimed = await postAsAgent(
    page,
    alphaToken,
    `/api/v1/discussions/roles/${role.id}/claim`,
    { revision: role.revision },
  );
  const offered = await postAsAgent(
    page,
    alphaToken,
    `/api/v1/discussions/roles/${role.id}/offer`,
    {
      revision: claimed.revision as number,
      successorId: betaId,
      note: "Beta has the current context.",
    },
  );
  await refreshDiscussions(page);
  const roleCard = page.getByTestId(`discussion-role-${role.id}`);
  await expect(roleCard).toContainText("Owner: " + alphaLabel);
  await expect(roleCard).toContainText("Handoff offered to " + betaLabel);

  await postAsAgent(
    page,
    betaToken,
    `/api/v1/discussions/roles/${role.id}/accept`,
    { revision: offered.revision as number },
  );
  await refreshDiscussions(page);
  await expect(roleCard).toContainText("Owner: " + betaLabel);
  await expect(roleCard).toContainText("No pending handoff offer.");
  await roleCard.getByText("Reassign owner", { exact: true }).click();
  const alphaId = required(credentials.find(credential => credential.label === alphaLabel)?.agentId, "original profile");
  await roleCard.getByRole("combobox", { name: "New owner", exact: true }).selectOption(alphaId);
  await roleCard.getByLabel("Reason for reassignment").fill("Human recovery of the current responsibility");
  await roleCard.getByRole("button", { name: "Reassign role", exact: true }).click();
  await expect(roleCard).toContainText("Owner: " + alphaLabel);
});

test("human release, archived replies, draft recovery, search and narrow layout", async ({ page }, testInfo) => {
  const title = "Persistent forum question " + Date.now();
  await page.goto("/#discussions");
  await page.getByRole("button", { name: "New post", exact: true }).click();
  await page.getByLabel("Post message", { exact: true }).fill(title);
  await page.getByRole("checkbox", { name: "Request acknowledgement (received and understood)" }).check();
  await page.getByLabel("Who may acknowledge?").selectOption("any_agent");
  await page.getByRole("button", { name: "Post discussion", exact: true }).click();
  const requestCard = page.getByRole("region", { name: "Acknowledgement request" });
  await expect(requestCard.getByText("Awaiting", { exact: true })).toBeVisible();
  await page.getByLabel("Reply message", { exact: true }).fill("This reply draft survives a reload.");
  await page.reload();
  await expect(page.getByLabel("Reply message", { exact: true })).toHaveValue("This reply draft survives a reload.");
  await page.getByRole("button", { name: "Send reply", exact: true }).click();
  await expect(requestCard.getByText("Awaiting", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Archive", exact: true }).click();
  await page.getByRole("button", { name: "Awaiting", exact: true }).click();
  await expect(page.getByRole("region", { name: "Discussion list", exact: true }).getByRole("link", { name: new RegExp(title) })).toBeVisible();
  await page.getByLabel("Reply message", { exact: true }).fill("Human can still reply after archival.");
  await page.getByRole("button", { name: "Send reply", exact: true }).click();
  await expect(page.getByText("Human can still reply after archival.", { exact: true })).toBeVisible();
  await requestCard.getByRole("button", { name: "Release wait…", exact: true }).click();
  await requestCard.getByLabel("Reason for releasing this wait").fill("The human has taken responsibility for this decision.");
  await requestCard.getByRole("button", { name: "Release wait", exact: true }).click();
  await expect(requestCard.getByText("Released by human", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Archived", exact: true }).click();
  await page.getByRole("searchbox", { name: "Search discussions" }).fill(title);
  await expect(page.getByRole("region", { name: "Discussion list", exact: true }).getByRole("link", { name: new RegExp(title) })).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("discussion-mobile.png"), fullPage: true });
});
