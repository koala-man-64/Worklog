import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  onlineManager,
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { getDataContext, request } from "./api";
import { useCommand, useData, useWrites } from "./hooks";

afterEach(() => vi.restoreAllMocks());

/**
 * A server holding a list of people. A read answers the list as it was when the read arrived,
 * and each read numbered in `held` (the first is 1) waits for `answer(n)`: a slow answer to a
 * read sent before a write.
 */
function serve(held = [1]) {
  let people: string[] = [];
  const reads: string[] = [];
  const gates = new Map<number, { open: () => void; opened: Promise<void> }>();
  for (const read of held) {
    let open = () => {};
    const opened = new Promise<void>((resolve) => {
      open = () => resolve();
    });
    gates.set(read, { open, opened });
  }
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input).replace("/api/v1", "");
    if (init?.method === "POST") {
      people = [...people, JSON.parse(String(init.body)).name];
      return new Response(JSON.stringify({ people }));
    }
    reads.push(path);
    const answer = path === "/session" ? { today: "2026-11-08" } : people;
    await gates.get(reads.length)?.opened;
    return new Response(JSON.stringify(answer));
  });
  return { reads, answer: (read: number) => gates.get(read)?.open() };
}

/**
 * Adds a person. Given `afterSuccess`, it runs once this write's success has been handled, as a
 * held read's answer does when it is slower than the write's.
 */
function addPerson(afterSuccess?: () => void) {
  return async (body: { name: string }) => {
    const added = await request("/people", "POST", body);
    if (afterSuccess) setTimeout(afterSuccess);
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
      add: useCommand(addPerson(() => server.answer(1))),
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
      add: useCommand(addPerson(() => server.answer(1))),
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
  server.answer(1);
});

it("drops a closed view's refetch too, so a list kept fresh a while still reads again", async () => {
  const server = serve([2]);
  const client = newClient();
  const usePeople = () =>
    useQuery({
      queryKey: [getDataContext(), "/people"],
      queryFn: () => request<string[]>("/people"),
      staleTime: 60_000,
    });
  const list = renderHook(usePeople, { wrapper: wrapper(client) });
  await waitFor(() => expect(list.result.current.data).toEqual([]));
  void list.result.current.refetch();
  await waitFor(() => expect(server.reads).toHaveLength(2));
  list.unmount();
  const add = renderHook(() => useCommand(addPerson(() => server.answer(2))), {
    wrapper: wrapper(client),
  });
  await act(() => add.result.current.mutateAsync({ name: "Ada Park" }));
  const reopened = renderHook(usePeople, { wrapper: wrapper(client) });
  await waitFor(() =>
    expect(reopened.result.current.data).toEqual(["Ada Park"]),
  );
  expect(server.reads).toHaveLength(3);
});

it("keeps an earlier write waiting for the read that replaced its own", async () => {
  const server = serve([2, 3]);
  const client = newClient();
  const { result } = renderHook(
    () => ({
      people: useData<string[]>("/people"),
      add: useCommand(addPerson()),
    }),
    { wrapper: wrapper(client) },
  );
  await waitFor(() => expect(result.current.people.data).toEqual([]));
  const key = [getDataContext(), "/people"];
  // What the first write's caller reads back once its wait ends.
  const first = result.current.add
    .mutateAsync({ name: "Ada Park" })
    .then(() => client.getQueryData(key));
  // The first write's refresh sends a second read, and the second write's replaces it.
  await waitFor(() => expect(server.reads).toHaveLength(2));
  const second = result.current.add.mutateAsync({ name: "Ben Ode" });
  await waitFor(() => expect(server.reads).toHaveLength(3));
  server.answer(3);
  const [seen] = await act(() => Promise.all([first, second]));
  expect(seen).toEqual(["Ada Park", "Ben Ode"]);
  server.answer(2);
});

// A controlled checkbox: React puts it back to its rendered value once the change handler returns.
function Planned({ sent }: { sent: boolean[] }) {
  const writes = useWrites();
  const [planned] = useState(false);
  return (
    <input
      type="checkbox"
      aria-label="Planned"
      checked={planned}
      onChange={(e) =>
        writes.mutate(async () => {
          sent.push(e.target.checked);
        })
      }
    />
  );
}

it("starts a write while the event that asked for it is current", async () => {
  const sent: boolean[] = [];
  render(<Planned sent={sent} />, { wrapper: wrapper(newClient()) });
  await userEvent.click(screen.getByRole("checkbox", { name: "Planned" }));
  // Started later, as a mutation calls its function, it would read the box put back: false.
  await waitFor(() => expect(sent).toEqual([true]));
});

it("gives a write's own answer and follows it while it is pending", async () => {
  let finish = (_answer: { id: string }) => {};
  const answer = new Promise<{ id: string }>((resolve) => {
    finish = resolve;
  });
  const { result } = renderHook(() => useWrites(), {
    wrapper: wrapper(newClient()),
  });
  let answered: Promise<{ id: string }> = Promise.resolve({ id: "" });
  act(() => {
    answered = result.current.mutateAsync(() => answer);
  });
  await waitFor(() => expect(result.current.isPending).toBe(true));
  finish({ id: "note-1" });
  await expect(answered).resolves.toEqual({ id: "note-1" });
  await waitFor(() => expect(result.current.isPending).toBe(false));
});

it("fails a write that throws before its request starts, as a refused one does", async () => {
  const { result } = renderHook(() => useWrites(), {
    wrapper: wrapper(newClient()),
  });
  const broken = () => {
    throw new Error("No file to send");
  };
  act(() => result.current.mutate(broken));
  await waitFor(() =>
    expect(result.current.error).toEqual(new Error("No file to send")),
  );
  await act(async () => {
    await expect(result.current.mutateAsync(broken)).rejects.toThrow(
      "No file to send",
    );
  });
});

it("writes while the browser reports itself offline, since the server is on this machine", async () => {
  onlineManager.setOnline(false);
  try {
    const { result } = renderHook(() => useWrites(), {
      wrapper: wrapper(newClient()),
    });
    let answered: Promise<string> = Promise.resolve("");
    act(() => {
      answered = result.current.mutateAsync(async () => "saved");
    });
    // A mutation that waited for the browser to come online would stay pending here.
    await expect(answered).resolves.toBe("saved");
    await waitFor(() => expect(result.current.isPending).toBe(false));
  } finally {
    onlineManager.setOnline(true);
  }
});
