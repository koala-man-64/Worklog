import { expect, test } from "@playwright/test";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Where the e2e server reads Claude Code's records of running sessions (playwright.config.ts).
const sessions = fileURLToPath(
  new URL("../../.local/frontend-playwright-claude/sessions/", import.meta.url),
);

test("a Claude Code session running on this machine is offered on Chats until it is tied to a work item", async ({
  page,
}) => {
  const suffix = crypto.randomUUID();
  const workName = "Tie a running session " + suffix;
  const name = "Running session " + suffix;
  const sessionId = crypto.randomUUID();
  // A record as Claude Code keeps it while a session runs, here for this test's own process,
  // which runs and started before the record says the session did.
  const record = path.join(sessions, `${process.pid}.json`);
  mkdirSync(sessions, { recursive: true });
  writeFileSync(
    record,
    JSON.stringify({
      pid: process.pid,
      sessionId,
      cwd: "C:\\Projects\\running-session",
      startedAt: Date.now(),
      kind: "interactive",
      entrypoint: "cli",
      name,
      status: "waiting",
      waitingFor: "permission prompt",
      updatedAt: Date.now(),
    }),
  );
  try {
    // A work item to tie it to. The Work list is not this spec's: wait for the item itself.
    await page.goto("/#work");
    await page.getByRole("textbox", { name: "New work item" }).fill(workName);
    const created = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname === "/api/v1/work-items",
    );
    await page.getByRole("button", { name: "Add work", exact: true }).click();
    expect((await created).ok()).toBe(true);

    // The section shows nothing until the list of running sessions arrives, which a busy parallel
    // run can hold up: wait for the server's answer that names this session, then for the page.
    const listed = page.waitForResponse(
      async (r) =>
        new URL(r.url()).pathname === "/api/v1/claude-sessions" &&
        r.ok() &&
        ((await r.json()) as { items: { sessionId: string }[] }).items.some(
          (s) => s.sessionId === sessionId,
        ),
      { timeout: 20_000 },
    );
    await page.goto("/#chats");
    await listed;
    const running = page.getByRole("region", {
      name: "Running on this machine",
    });
    const ties = running.getByRole("group", { name: "Tie " + name });
    await expect(running.getByText(name, { exact: true })).toBeVisible();
    await expect(running).toContainText("Waiting for permission prompt");
    await ties.getByRole("button", { name: "Tie work item" }).click();
    await ties
      .getByRole("textbox", { name: "Work item to tie search" })
      .fill(workName);
    await ties.getByRole("button", { name: new RegExp(workName) }).click();

    // Tracked now, it opens among the chats under its own session ID and leaves the offer.
    const history = page.getByRole("region", { name: "Chat history" });
    await expect(history.getByRole("heading", { name })).toBeVisible();
    await expect(history.getByText(sessionId, { exact: true })).toBeVisible();
    await expect(running.getByText(name, { exact: true })).toHaveCount(0);
  } finally {
    rmSync(record, { force: true });
  }
});
