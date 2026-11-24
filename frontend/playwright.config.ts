import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { checkTarget } from "./e2e/guard";
// Refuses a server the suite did not start unless it is declared disposable, and port 8877 always.
checkTarget(process.env);
const port = Number(process.env.WORKLOG_PORT || 8877);
if (!Number.isInteger(port) || port < 1 || port > 65535)
  throw new Error("WORKLOG_PORT must be a port number 1-65535");
const local = `http://127.0.0.1:${port}`;
// Claude Code's folders as the server sees them; claude-sessions.spec.ts writes into the same one.
const claudeFolder = fileURLToPath(
  new URL("../.local/frontend-playwright-claude", import.meta.url),
);
export default defineConfig({
  testDir: "./e2e",
  // e2e/ also holds vitest's unit tests of the guard (*.test.ts).
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  use: {
    baseURL: process.env.WORKLOG_BASE_URL ?? local,
    headless: true,
  },
  webServer: process.env.WORKLOG_BASE_URL
    ? undefined
    : {
        command: `"${fileURLToPath(new URL("../.venv/Scripts/python.exe", import.meta.url))}" -m worklog.cli --data-dir "../.local/frontend-playwright" --port ${port} serve`,
        url: local,
        reuseExistingServer: false,
        // The server reads Claude Code's folders from here, never this machine's own: the
        // sessions running on it would show on Chats. Specs write the records they need.
        env: { CLAUDE_CONFIG_DIR: claudeFolder },
      },
  // In CI a failed test runs once more, so one flaky test cannot leave main red; locally it
  // fails at once. The list reporter's summary names each test that passed only on its retry
  // ("flaky"), and in CI the github reporter shows them on the run's page.
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["github"]] : "list",
});
