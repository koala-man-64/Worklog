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
