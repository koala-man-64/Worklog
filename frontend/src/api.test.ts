import { afterEach, expect, it, vi } from "vitest";
import { bootstrap, request, ApiError, setDataContext } from "./api";
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});
it("bootstraps the cookie session and sends csrf on writes", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          csrfToken: "csrf",
          today: "2026-10-28",
          timezone: "America/Chicago",
          dataId: "dataset-test",
        }),
      ),
    )
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "one" })));
  vi.stubGlobal("fetch", fetch);
  await bootstrap();
  await request("/work-items", "POST", { title: "First" });
  expect(fetch.mock.calls[0][1].headers["X-Worklog-Bootstrap"]).toBe("1");
  expect(fetch.mock.calls[1][1]).toMatchObject({
    credentials: "same-origin",
    headers: { "X-CSRF-Token": "csrf", "Content-Type": "application/json" },
  });
  expect(fetch.mock.calls[1][1].headers["Idempotency-Key"]).toBeTruthy();
});
it("preserves conflict evidence", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "revision_conflict", message: "Changed elsewhere" },
        }),
        { status: 409 },
      ),
    ),
  );
  await expect(
    request("/work-items/one", "PATCH", { revision: 1 }),
  ).rejects.toMatchObject({
    status: 409,
    code: "revision_conflict",
    message: "Changed elsewhere",
  } satisfies Partial<ApiError>);
});
it("reuses persisted idempotency after lost responses, and changes the key for changed payloads", async () => {
  const fetch = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("Connection lost after write"))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "one" })))
    .mockRejectedValueOnce(new TypeError("Connection lost"));
  vi.stubGlobal("fetch", fetch);
  await expect(
    request("/work-items", "POST", { title: "Original", description: "A" }),
  ).rejects.toThrow("Connection lost");
  const first = fetch.mock.calls[0][1].headers["Idempotency-Key"];
  await request("/work-items", "POST", { description: "A", title: "Original" });
  expect(fetch.mock.calls[1][1].headers["Idempotency-Key"]).toBe(first);
  expect(sessionStorage.length).toBe(0);
  await expect(
    request("/work-items", "POST", { title: "Changed", description: "A" }),
  ).rejects.toThrow();
  expect(fetch.mock.calls[2][1].headers["Idempotency-Key"]).not.toBe(first);
  expect(
    [...Object.keys(sessionStorage)].some((key) => key.includes("Changed")),
  ).toBe(false);
});
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
const refusal = (code: string, status: number) =>
  json({ error: { code, message: "Refused" } }, status);
/** Answers like a server that restarted: each path's first request is refused, later ones served. */
function restarted(
  refuse: () => Response,
  { dataId = "test", always = false } = {},
) {
  const refused = new Set<string>();
  const fetch = vi.fn(async (input: string, _init?: RequestInit) => {
    if (input === "/health") return json({ app: "worklog", dataId });
    if (input === "/api/v1/session/bootstrap")
      return json({ csrfToken: "renewed", today: "2026-11-02", dataId });
    if (always || !refused.has(input)) {
      refused.add(input);
      return refuse();
    }
    return json({ items: [], total: 0 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const urls = (fetch: ReturnType<typeof restarted>) =>
  fetch.mock.calls.map(([url]) => url);
it("starts a new session and resends when a restarted server forgot this page's", async () => {
  const fetch = restarted(() => refusal("session", 401));
  await expect(request("/work-items")).resolves.toEqual({
    items: [],
    total: 0,
  });
  expect(urls(fetch)).toEqual([
    "/api/v1/work-items",
    "/health",
    "/api/v1/session/bootstrap",
    "/api/v1/work-items",
  ]);
});
it.each([
  ["a restart", () => refusal("session", 401)],
  ["another tab renewed the session", () => refusal("csrf", 403)],
])(
  "resends a write refused after %s with the new token and the same idempotency key",
  async (_, refuse) => {
    const fetch = restarted(refuse);
    await request("/work-items", "POST", { title: "Kept" });
    const [sent, , , resent] = fetch.mock.calls;
    expect(resent[0]).toBe("/api/v1/work-items");
    expect(resent[1]!.headers).toMatchObject({
      "X-CSRF-Token": "renewed",
      "Idempotency-Key": (sent[1]!.headers as Record<string, string>)[
        "Idempotency-Key"
      ],
    });
    expect(sessionStorage.length).toBe(0);
  },
);
it("shares one renewal between requests refused together", async () => {
  const fetch = restarted(() => refusal("session", 401));
  await Promise.all([request("/work-items"), request("/people")]);
  expect(urls(fetch).filter((url) => url === "/health")).toHaveLength(1);
  expect(
    urls(fetch).filter((url) => url === "/api/v1/session/bootstrap"),
  ).toHaveLength(1);
});
it("starts no session when the server now holds other data, as after a restore", async () => {
  const fetch = restarted(() => refusal("session", 401), {
    dataId: "restored",
  });
  await expect(request("/work-items")).rejects.toMatchObject({
    status: 409,
    code: "data_context",
  } satisfies Partial<ApiError>);
  expect(urls(fetch)).toEqual(["/api/v1/work-items", "/health"]);
});
it("resends once, then reports the refusal and forgets the write's key", async () => {
  const fetch = restarted(() => refusal("session", 401), { always: true });
  await expect(
    request("/work-items", "POST", { title: "Refused" }),
  ).rejects.toMatchObject({
    status: 401,
    code: "session",
  } satisfies Partial<ApiError>);
  expect(fetch).toHaveBeenCalledTimes(4);
  expect(sessionStorage.length).toBe(0);
});
it("does not renew for other refusals", async () => {
  const fetch = restarted(() => refusal("agent_scope", 403));
  await expect(request("/work-items", "POST", {})).rejects.toMatchObject({
    status: 403,
    code: "agent_scope",
  } satisfies Partial<ApiError>);
  expect(urls(fetch)).toEqual(["/api/v1/work-items"]);
});
it("isolates pending idempotency keys between datasets for identical requests", async () => {
  const fetch = vi.fn().mockRejectedValue(new TypeError("Lost response"));
  vi.stubGlobal("fetch", fetch);
  setDataContext("dataset-A");
  await expect(
    request("/work-items", "POST", { title: "Same" }),
  ).rejects.toThrow();
  const keyA = fetch.mock.calls[0][1].headers["Idempotency-Key"];
  setDataContext("dataset-B");
  await expect(
    request("/work-items", "POST", { title: "Same" }),
  ).rejects.toThrow();
  expect(fetch.mock.calls[1][1].headers["Idempotency-Key"]).not.toBe(keyA);
  expect(sessionStorage.length).toBe(2);
});
