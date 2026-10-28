import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  use: {
    baseURL: process.env.WORKLOG_BASE_URL ?? "http://127.0.0.1:8877",
    headless: true,
  },
  webServer: process.env.WORKLOG_BASE_URL
    ? undefined
    : {
        command: `"${fileURLToPath(new URL("../.venv/Scripts/python.exe", import.meta.url))}" -m worklog.cli --data-dir "../.local/frontend-playwright" serve`,
        url: "http://127.0.0.1:8877",
        reuseExistingServer: false,
      },
  reporter: "list",
});
