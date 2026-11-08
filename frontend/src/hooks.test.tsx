import { afterEach, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import type { ReactNode } from "react";
import { getDataContext, request } from "./api";
import { useCommand, useData } from "./hooks";

afterEach(() => vi.restoreAllMocks());

/**
 * A server holding a list of people. A read answers the list as it was when the read arrived,
 * but the first read's answer waits for `answerFirstRead`: a slow answer to a read sent just
 * before a write.
 */
function serve() {
  let people: string[] = [];
  const reads: string[] = [];
  let answerFirstRead = () => {};
  const firstReadHeld = new Promise<void>((resolve) => {
    answerFirstRead = () => resolve();
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input).replace("/api/v1", "");
    if (init?.method === "POST") {
      people = [...people, JSON.parse(String(init.body)).name];
      return new Response(JSON.stringify({ people }));
    }
    reads.push(path);
    const answer = path === "/session" ? { today: "2026-11-08" } : people;
    if (reads.length === 1) await firstReadHeld;
    return new Response(JSON.stringify(answer));
  });
  return { reads, answerFirstRead: () => answerFirstRead() };
}

/**
 * Adds a person. Given `answerFirstRead`, the held read answers once this write's success has
 * been handled, as it does when that answer is slower than the write's.
 */
function addPerson(answerFirstRead?: () => void) {
  return async (body: { name: string }) => {
    const added = await request("/people", "POST", body);
    if (answerFirstRead) setTimeout(answerFirstRead);
    return added;
  };
}

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const newClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

it("reads a list again when its first read was in flight as a write succeeded", async () => {
  const server = serve();
  const { result } = renderHook(
    () => ({
      people: useData<string[]>("/people"),
      add: useCommand(addPerson(server.answerFirstRead)),
    }),
    { wrapper: wrapper(newClient()) },
  );
  await waitFor(() => expect(server.reads).toEqual(["/people"]));
  await act(() => result.current.add.mutateAsync({ name: "Ada Park" }));
  // The first read answered the list from before the write, after the write succeeded.
  await waitFor(() => expect(result.current.people.data).toEqual(["Ada Park"]));
  expect(server.reads).toEqual(["/people", "/people"]);
});

it("leaves the session's read alone when a write succeeds", async () => {
  const server = serve();
  const { result } = renderHook(
    () => ({
      session: useQuery({
        queryKey: ["session"],
        queryFn: () => request<{ today: string }>("/session"),
      }),
      add: useCommand(addPerson(server.answerFirstRead)),
    }),
    { wrapper: wrapper(newClient()) },
  );
  await waitFor(() => expect(server.reads).toEqual(["/session"]));
  await act(() => result.current.add.mutateAsync({ name: "Ada Park" }));
  await waitFor(() =>
    expect(result.current.session.data).toEqual({ today: "2026-11-08" }),
  );
  expect(server.reads).toEqual(["/session"]);
});

it("drops the read of a view closed before a write, and reads again when it opens", async () => {
  const server = serve();
  const client = newClient();
  const list = renderHook(() => useData<string[]>("/people"), {
    wrapper: wrapper(client),
  });
  await waitFor(() => expect(server.reads).toEqual(["/people"]));
  list.unmount();
  const add = renderHook(() => useCommand(addPerson()), {
    wrapper: wrapper(client),
  });
  await act(() => add.result.current.mutateAsync({ name: "Ada Park" }));
  // Not left fetching an answer from before the write, nor failed by dropping it.
  expect(client.getQueryState([getDataContext(), "/people"])).toMatchObject({
    fetchStatus: "idle",
    status: "pending",
  });
  const reopened = renderHook(() => useData<string[]>("/people"), {
    wrapper: wrapper(client),
  });
  await waitFor(() =>
    expect(reopened.result.current.data).toEqual(["Ada Park"]),
  );
  expect(server.reads).toEqual(["/people", "/people"]);
  server.answerFirstRead();
});
