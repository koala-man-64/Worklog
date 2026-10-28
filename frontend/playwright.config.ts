import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
const port = Number(process.env.WORKLOG_PORT || 8877);
if (!Number.isInteger(port) || port < 1 || port > 65535)
  throw new Error("WORKLOG_PORT must be a port number 1-65535");
const local = `http://127.0.0.1:${port}`;
export default defineConfig({
  testDir: "./e2e",
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
      },
  reporter: "list",
});
