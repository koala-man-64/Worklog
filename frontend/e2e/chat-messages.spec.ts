import { expect, test } from "@playwright/test";

test("a message written into a chat reaches a confirmed agent's open run once, and archiving ends what is still waiting", async ({
  page,
}) => {
  const suffix = crypto.randomUUID();
  const workName = "Deliver messages " + suffix;
  const chatTitle = "Delivery chat " + suffix;
  const chatId = crypto.randomUUID();
  const agentLabel = "Delivery agent " + suffix;
  const first = "Use the revised import format " + suffix;
  const second = "Also keep the old reader " + suffix;

  await page.goto("/#work");
  await page.getByRole("textbox", { name: "New work item" }).fill(workName);
  await page.getByRole("button", { name: "Add work", exact: true }).click();
  // Earlier runs can leave enough work that a new item starts on a later page.
  await page
    .getByRole("textbox", { name: "Filter work by text" })
    .fill(workName);
  await page.getByRole("link", { name: workName, exact: true }).click();
  await page
    .getByRole("textbox", { name: "Agent chat title", exact: true })
    .fill(chatTitle);
  await page
    .getByRole("textbox", { name: "Agent chat ID", exact: true })
    .fill(chatId);
  await page.getByRole("button", { name: "Add chat", exact: true }).click();
  const chatLink = page.getByRole("link", { name: "Messages of " + chatTitle });
  await expect(chatLink).toBeVisible();
  const chatHref = await chatLink.getAttribute("href");
  const workUrl = page.url();

  await page.goto("/#settings");
  await page
    .getByRole("textbox", { name: "Credential label" })
    .fill(agentLabel);
  await page
    .getByRole("button", { name: "Create credential", exact: true })
    .click();
  const token = await page.locator(".token code").innerText();
  await page.getByRole("button", { name: "I have saved it" }).click();
  async function asAgent(path: string, data: unknown) {
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
  await asAgent("/agent-runs", {
    clientRunKey: crypto.randomUUID(),
    displayName: "Importer run",
    externalChatId: chatId,
  });

  await page.goto("/" + chatHref);
  const history = page.getByRole("region", { name: "Chat history" });
  // A credential issued in Settings only claims the chat until you confirm its agent here.
  const waiting = history.getByRole("list", {
    name: "Agents waiting for your confirmation",
  });
  await expect(waiting).toContainText(agentLabel);
  await waiting
    .getByRole("button", { name: `Confirm ${agentLabel} works in this chat` })
    .click();
  await expect(
    history.getByRole("list", { name: "Agents a message reaches" }),
  ).toContainText(agentLabel);
  await expect(waiting).toHaveCount(0);
  const box = history.getByRole("textbox", {
    name: "Message to this chat's agents",
  });
  await box.fill(first);
  await history.getByRole("button", { name: "Send", exact: true }).click();
  await expect(box).toHaveValue("");
  await expect(
    history.getByText(agentLabel + ": Waiting for pickup"),
  ).toBeVisible();

  const received = await asAgent("/inbox/receive", {});
  expect(received.items.map((m: { text: string }) => m.text)).toEqual([first]);
  expect((await asAgent("/inbox/receive", {})).items).toEqual([]);
  await expect(
    history.getByText(new RegExp(agentLabel + ": Delivered")),
  ).toBeVisible({ timeout: 15_000 });

  await box.fill(second);
  await history.getByRole("button", { name: "Send", exact: true }).click();
  await expect(
    history.getByText(agentLabel + ": Waiting for pickup"),
  ).toBeVisible();
  await page.goto(workUrl);
  await page
    .getByRole("button", { name: "Archive chat " + chatTitle, exact: true })
    .click();
  await expect(chatLink).toHaveCount(0);

  await page.goto("/" + chatHref);
  await page.getByRole("checkbox", { name: "Show archive" }).check();
  await expect(history.getByText(/This chat is archived/)).toBeVisible();
  await expect(history.getByRole("textbox")).toHaveCount(0);
  await expect(
    history.getByText(agentLabel + ": Not delivered: the chat was archived"),
  ).toBeVisible();
  expect((await asAgent("/inbox/receive", {})).items).toEqual([]);
});
