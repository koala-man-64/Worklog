/**
 * Where the browser suite may run. The specs create over a hundred work items, credentials,
 * roadmaps, a backup and an export, so they run against the disposable server Playwright starts
 * itself. A server it did not start (WORKLOG_BASE_URL) must be declared disposable with
 * WORKLOG_E2E_DISPOSABLE=1, and port 8877, where the person's own instance runs, is never one.
 */
export const PERSONAL_PORT = "8877";

export function checkTarget(env: Record<string, string | undefined>): void {
  const target = env.WORKLOG_BASE_URL;
  if (!target) return;
  let port: string;
  try {
    port = new URL(target).port;
  } catch {
    throw new Error(`WORKLOG_BASE_URL is not an address: ${target}`);
  }
  if (port === PERSONAL_PORT)
    throw new Error(
      `WORKLOG_BASE_URL names port ${PERSONAL_PORT}, where the personal instance runs; the browser suite never writes there`,
    );
  if (env.WORKLOG_E2E_DISPOSABLE !== "1")
    throw new Error(
      "WORKLOG_BASE_URL names a server the browser suite did not start, and the suite writes test data into it: " +
        "set WORKLOG_E2E_DISPOSABLE=1 only if that server holds nothing you need",
    );
}
