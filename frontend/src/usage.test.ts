import { beforeEach, expect, it, vi } from "vitest";
import { destinations } from "./navigation";
import { countedPages, countPageView } from "./usage";

type Call = { path: string; method: string; body: unknown };

beforeEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

/** Answers every request with `status`, or fails it as a network error would; returns the calls. */
function serve(status: number | "offline") {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    calls.push({
      path: new URL(String(input), "http://worklog.test").pathname,
      method: init?.method ?? "GET",
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    });
    if (status === "offline") throw new TypeError("Failed to fetch");
    const body =
      status < 400
        ? { ok: true }
        : { error: { code: "internal_error", message: "Broken" } };
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  return calls;
}

/** Lets the request's promise chain run to its end. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

it("counts each sidebar page and each Guide view by its name alone", async () => {
  expect(countedPages).toEqual([
    ...destinations.map(({ route }) => route),
    "agent-guide",
  ]);
  const calls = serve(200);
  for (const page of countedPages) countPageView(page);
  await vi.waitFor(() => expect(calls).toHaveLength(countedPages.length));
  expect(calls).toEqual(
    countedPages.map((page) => ({
      path: "/api/v1/page-views",
      method: "POST",
      body: { page },
    })),
  );
});

it("counts nothing for a page the sidebar doesn't have", async () => {
  const calls = serve(200);
  countPageView("");
  countPageView("nonexistent");
  countPageView("work/1234");
  await settle();
  expect(calls).toEqual([]);
});

it.each([500, "offline"] as const)(
  "drops a count that fails (%s) without an error",
  async (status) => {
    const calls = serve(status);
    expect(() => countPageView("today")).not.toThrow();
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    // An unhandled rejection here would fail the run.
    await settle();
  },
);
