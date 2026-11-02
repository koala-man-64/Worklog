import { sessionStore } from "./storage";
let csrf = "";
let dataContext = "unbound";
export const getDataContext = () => dataContext;
export function setDataContext(value: string) {
  dataContext = value;
}
export const contextStorageKey = (
  kind: string,
  key: string,
  context = dataContext,
) => `worklog:${context}:${kind}:${key}`;
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, value]) => [key, canonical(value)]),
    );
  return value;
}
async function pendingRequest(path: string, method: string, body: unknown) {
  const context = getDataContext();
  const bytes =
    body instanceof File
      ? await body.arrayBuffer()
      : new TextEncoder().encode(JSON.stringify(canonical(body)) ?? "");
  const hash = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
  )
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
  const storageKey = `worklog:${context}:pending:${method}:${path}:${body instanceof File ? body.type : ""}:${hash}`;
  const key = sessionStore.read(storageKey).value ?? crypto.randomUUID();
  sessionStore.write(storageKey, key);
  return { storageKey, key };
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code: string,
  ) {
    super(message);
  }
}
async function failure(result: Response) {
  const data = await result
    .json()
    .catch(() => ({ error: { message: result.statusText } }));
  return new ApiError(
    result.status,
    data.error?.message || "Request failed",
    data.error?.code || "request_failed",
  );
}
export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const expectedContext = getDataContext();
  const requestCsrf = csrf;
  const pending =
    method !== "GET" && path !== "/session/bootstrap"
      ? await pendingRequest(path, method, body)
      : null;
  const send = (token: string) => {
    const headers: Record<string, string> = {};
    if (method !== "GET") {
      headers["X-CSRF-Token"] = token;
      if (pending) headers["Idempotency-Key"] = pending.key;
    }
    if (path === "/session/bootstrap") headers["X-Worklog-Bootstrap"] = "1";
    if (body !== undefined)
      headers["Content-Type"] =
        body instanceof File
          ? body.type || "application/octet-stream"
          : "application/json";
    if (path !== "/session/bootstrap" && getDataContext() !== expectedContext)
      throw new ApiError(
        409,
        "The data folder changed before this request. Your local draft is retained.",
        "data_context",
      );
    return fetch("/api/v1" + path, {
      method,
      credentials: "same-origin",
      headers,
      body:
        body === undefined
          ? undefined
          : body instanceof File
            ? body
            : JSON.stringify(body),
    });
  };
  let result = await send(requestCsrf);
  // A restarted server has forgotten this page's session, and a session another tab renewed has a
  // token this page lacks. Renew once and resend; a write keeps its idempotency key.
  if (
    path !== "/session/bootstrap" &&
    (result.status === 401 || result.status === 403) &&
    ["session", "csrf"].includes((await failure(result.clone())).code)
  ) {
    await renewSession();
    result = await send(csrf);
  }
  if (!result.ok) {
    if (pending && result.status >= 400 && result.status < 500)
      sessionStore.remove(pending.storageKey);
    throw await failure(result);
  }
  const data = result.status === 204 ? (undefined as T) : await result.json();
  if (pending) sessionStore.remove(pending.storageKey);
  return data;
}
let renewal: Promise<void> | null = null;
/**
 * Starts a new session for this page: the server keeps sessions in memory, so a restart forgets
 * them. Callers at the same time share one renewal. It refuses when the server now holds other
 * data, such as after a restore, so a renewal never gives this page a session on that data.
 */
export function renewSession() {
  renewal ??= (async () => {
    const expected = getDataContext();
    const health = await fetch("/health", { credentials: "same-origin" });
    if (!health.ok) throw await failure(health);
    if ((await health.json()).dataId === expected) {
      const data = await request<{ csrfToken: string; dataId: string }>(
        "/session/bootstrap",
        "POST",
        {},
      );
      if (data.dataId === expected) {
        csrf = data.csrfToken;
        return;
      }
    }
    throw new ApiError(
      409,
      "The data folder changed while this page was open. Reload Worklog to continue; your local drafts are retained.",
      "data_context",
    );
  })().finally(() => {
    renewal = null;
  });
  return renewal;
}
export async function bootstrap() {
  const data = await request<{
    csrfToken: string;
    today: string;
    timezone: string;
    dataId: string;
  }>("/session/bootstrap", "POST", {});
  csrf = data.csrfToken;
  if (typeof data.dataId !== "string" || !data.dataId)
    throw new Error(
      "The server did not identify its data. Reconnect before editing.",
    );
  setDataContext(data.dataId);
  return data;
}
export const download = (path: string) => "/api/v1" + path;
