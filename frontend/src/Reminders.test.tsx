import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { setDataContext } from "./api";
import { Reminders, RemindersLink } from "./Reminders";
import type { Reminder, ReminderDetail } from "./apiTypes";

const ID = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";
const THIRD = "33333333-3333-4333-8333-333333333333";
const AGENT = {
  id: "agent-1",
  label: "Implementer",
  kind: "agent" as const,
  avatar: "teal-round-antenna",
};
function reminder(fields: Partial<Reminder> = {}): Reminder {
  return {
    id: ID,
    title: "Merge #113 once CI is green",
    date: "2026-11-06",
    time: "14:25",
    state: "open",
    due: true,
    overdue: false,
    target: null,
    author: { id: "local-user", label: "You", kind: "human", avatar: null },
    archived: false,
    revision: 3,
    createdAt: "2026-11-05T09:00:00Z",
    updatedAt: "2026-11-05T09:00:00Z",
    doneAt: null,
    ...fields,
  };
}
function detail(fields: Partial<ReminderDetail> = {}): ReminderDetail {
  return {
    ...reminder(),
    notes: "Check the **Backend** job first.",
    history: [
      {
        id: "event-1",
        kind: "reminder.create",
        actorId: "local-user",
        actorLabel: "You",
        actorKind: "human",
        createdAt: "2026-11-05T09:00:00Z",
      },
    ],
    ...fields,
  };
}
/** Renders with a session in the Worklog timezone, as the app opens with one. */
function show(component: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  client.setQueryData(["session"], {
    csrfToken: "token",
    today: "2026-11-06",
    timezone: "America/Chicago",
    dataId: "reminders-test",
  });
  return render(
    <QueryClientProvider client={client}>{component}</QueryClientProvider>,
  );
}
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
type Write = {
  method: string;
  path: string;
  body: Record<string, unknown>;
};
/** Answers reads from `reads` by path prefix, and each write with the reminder at its next revision. */
function serve(
  reads: Record<string, () => unknown>,
  write: (w: Write) => unknown = (w) =>
    detail({ revision: Number(w.body.revision ?? 0) + 1 }),
) {
  const writes: Write[] = [];
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    if (method !== "GET") {
      const w = { method, path, body: JSON.parse(String(init?.body)) };
      writes.push(w);
      return json(write(w));
    }
    paths.push(path);
    const match = Object.keys(reads)
      .sort((a, b) => b.length - a.length)
      .find((prefix) => path.startsWith(prefix));
    return json(match ? reads[match]() : { items: [], total: 0 });
  });
  return { writes, paths };
}
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("reminder-test-" + crypto.randomUUID());
  // 14:25 on Friday 6 November 2026 in Chicago; only the clock is faked, not timers.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-06T20:25:00Z"));
});
afterEach(() => {
  vi.useRealTimers();
  location.hash = "";
});

it("lists open reminders by when they come due and opens the first", async () => {
  const { paths } = serve({
    "/reminders?": () => ({
      items: [
        reminder({
          id: OTHER,
          title: "Reply to Ana",
          date: "2026-11-04",
          time: "16:00",
          overdue: true,
        }),
        reminder(),
        reminder({
          id: THIRD,
          title: "Plan the sprint",
          date: "2026-11-07",
          time: null,
          due: false,
          author: AGENT,
          target: { type: "item", id: ID, title: "Sprint 12", archived: false },
        }),
      ],
      total: 3,
    }),
    ["/reminders/" + OTHER]: () =>
      detail({ id: OTHER, title: "Reply to Ana", overdue: true }),
  });
  show(<Reminders />);
  const list = await screen.findByRole("region", { name: "Reminder list" });
  const rows = await within(list).findAllByRole("link");
  expect(rows.map((r) => r.textContent)).toEqual([
    expect.stringMatching(/^Reply to AnaOverdue · .* · 16:00$/),
    "Merge #113 once CI is greenToday · 14:25",
    "Plan the sprintTomorrow · All day · Sprint 12 · set by Implementer",
  ]);
  expect(rows[0]).toHaveAttribute("aria-current", "page");
  expect(paths[0]).toBe(
    "/reminders?state=open&archived=false&q=&limit=50&offset=0",
  );
  const pane = screen.getByRole("region", { name: "Reminder" });
  expect(
    await within(pane).findByRole("heading", { name: "Reply to Ana" }),
  ).toBeInTheDocument();
});

it("filters by state, words and the archive, each from the first page", async () => {
  const { paths } = serve({});
  show(<Reminders />);
  await screen.findByText("No reminders yet.");
  expect(
    screen.getByText(/Set a reminder for what you want to come back to/),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Due" }));
  expect(screen.getByRole("button", { name: "Due" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await screen.findByText("Nothing is due.");
  await userEvent.type(
    screen.getByRole("searchbox", { name: "Find reminders" }),
    "ci",
  );
  await screen.findByText("No reminder matches those words.");
  await userEvent.click(screen.getByRole("checkbox", { name: "Show archive" }));
  await screen.findByText("No archived reminders in this view.");
  expect(
    screen.queryByRole("textbox", { name: "New reminder" }),
  ).not.toBeInTheDocument();
  expect(paths).toContain(
    "/reminders?state=due&archived=true&q=ci&limit=50&offset=0",
  );
});

it("sets a reminder for today, at a time if given, and opens it", async () => {
  const { writes } = serve({ ["/reminders/" + ID]: () => detail() }, () =>
    detail({ revision: 1 }),
  );
  show(<Reminders />);
  const title = await screen.findByRole("textbox", { name: "New reminder" });
  expect(screen.getByLabelText("New reminder date")).toHaveValue("2026-11-06");
  await userEvent.type(title, "  Merge #113 once CI is green ");
  fireEvent.change(screen.getByLabelText("New reminder time"), {
    target: { value: "14:25" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Add reminder" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toEqual({
    method: "POST",
    path: "/reminders",
    body: {
      title: "Merge #113 once CI is green",
      notes: "",
      date: "2026-11-06",
      time: "14:25",
      targetType: null,
      targetId: null,
    },
  });
  await waitFor(() => expect(location.hash).toBe("#reminders/" + ID));
  expect(title).toHaveValue("");
});

it("snoozes a due reminder, then marks it done from the revision the snooze left", async () => {
  const { writes } = serve({
    "/reminders?": () => ({ items: [reminder()], total: 1 }),
    ["/reminders/" + ID]: () => detail(),
  });
  show(<Reminders selected={ID} />);
  const pane = screen.getByRole("region", { name: "Reminder" });
  await userEvent.click(
    await within(pane).findByRole("button", { name: "Snooze 1 hour" }),
  );
  await waitFor(() =>
    expect(
      within(pane).getByRole("button", { name: "Mark done" }),
    ).toBeEnabled(),
  );
  await userEvent.click(
    within(pane).getByRole("button", { name: "Mark done" }),
  );
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes).toEqual([
    {
      method: "PATCH",
      path: "/reminders/" + ID,
      body: { date: "2026-11-06", time: "15:25", revision: 3 },
    },
    {
      method: "PATCH",
      path: "/reminders/" + ID,
      body: { state: "done", revision: 4 },
    },
  ]);
});

it("saves a moved day or time as its field loses focus, and offers no snooze before it is due", async () => {
  const { writes } = serve({
    ["/reminders/" + ID]: () =>
      detail({ date: "2026-11-09", time: "09:00", due: false }),
  });
  show(<Reminders selected={ID} />);
  const pane = screen.getByRole("region", { name: "Reminder" });
  const date = await within(pane).findByLabelText("Date");
  expect(within(pane).getByText(/Coming up/)).toBeInTheDocument();
  expect(
    within(pane).queryByRole("button", { name: "Snooze 1 hour" }),
  ).not.toBeInTheDocument();
  fireEvent.change(date, { target: { value: "2026-11-10" } });
  fireEvent.blur(date);
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].body).toEqual({ date: "2026-11-10", revision: 3 });
  // Clearing the date is not saved: the field takes the saved day back.
  fireEvent.change(date, { target: { value: "" } });
  fireEvent.blur(date);
  expect(date).toHaveValue("2026-11-09");
  await userEvent.click(within(pane).getByRole("button", { name: "All day" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[1].body).toEqual({ time: null, revision: 4 });
});

it("takes a press that follows a day saved as its field loses focus, after that save", async () => {
  const { writes } = serve({
    ["/reminders/" + ID]: () =>
      detail({ date: "2026-11-09", time: "09:00", due: false }),
  });
  show(<Reminders selected={ID} />);
  const pane = screen.getByRole("region", { name: "Reminder" });
  const date = await within(pane).findByLabelText("Date");
  await userEvent.click(date);
  fireEvent.change(date, { target: { value: "2026-11-10" } });
  // The press on "Mark done" takes the focus from the field, which saves first.
  await userEvent.click(
    within(pane).getByRole("button", { name: "Mark done" }),
  );
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes.map((w) => w.body)).toEqual([
    { date: "2026-11-10", revision: 3 },
    { state: "done", revision: 4 },
  ]);
});

it("keeps a time being typed when the day saved just before it comes back", async () => {
  const saved = { date: "2026-11-09", time: "09:00" };
  const { writes, paths } = serve(
    { ["/reminders/" + ID]: () => detail({ ...saved, due: false }) },
    (w) => {
      if (typeof w.body.date === "string") saved.date = w.body.date;
      if ("time" in w.body) saved.time = w.body.time as string;
      return detail({ ...saved, revision: Number(w.body.revision) + 1 });
    },
  );
  show(<Reminders selected={ID} />);
  const pane = screen.getByRole("region", { name: "Reminder" });
  const date = await within(pane).findByLabelText("Date");
  const time = within(pane).getByLabelText("Time");
  fireEvent.change(date, { target: { value: "2026-11-10" } });
  fireEvent.blur(date);
  fireEvent.change(time, { target: { value: "11:00" } });
  // The day's save lands and the reminder is read again, with the new day and the old time.
  await waitFor(() =>
    expect(paths.filter((p) => p === "/reminders/" + ID)).toHaveLength(2),
  );
  await waitFor(() => expect(date).toHaveValue("2026-11-10"));
  expect(time).toHaveValue("11:00");
  fireEvent.blur(time);
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes.map((w) => w.body)).toEqual([
    { date: "2026-11-10", revision: 3 },
    { time: "11:00", revision: 4 },
  ]);
});

it("keeps the reminder that opened by itself open once it is done and leaves the list", async () => {
  const state = { done: false };
  const other = reminder({ id: OTHER, title: "Reply to Ana" });
  serve(
    {
      "/reminders?": () =>
        state.done
          ? { items: [other], total: 1 }
          : { items: [reminder(), other], total: 2 },
      ["/reminders/" + ID]: () =>
        state.done
          ? detail({
              state: "done",
              due: false,
              doneAt: "2026-11-06T20:26:00Z",
            })
          : detail(),
      ["/reminders/" + OTHER]: () =>
        detail({ id: OTHER, title: "Reply to Ana" }),
    },
    () => {
      state.done = true;
      return detail({ state: "done", revision: 4 });
    },
  );
  show(<Reminders />);
  const pane = screen.getByRole("region", { name: "Reminder" });
  await userEvent.click(
    await within(pane).findByRole("button", { name: "Mark done" }),
  );
  expect(
    await within(pane).findByRole("button", { name: "Reopen" }),
  ).toBeInTheDocument();
  expect(
    within(pane).getByRole("heading", { name: "Merge #113 once CI is green" }),
  ).toBeInTheDocument();
  expect(
    within(screen.getByRole("region", { name: "Reminder list" })).getAllByRole(
      "link",
    ),
  ).toHaveLength(1);
});

it("names who set it and what it is about, and archives or recovers it", async () => {
  const { writes } = serve({
    ["/reminders/" + ID]: () =>
      detail({
        author: AGENT,
        target: {
          type: "ticket",
          id: OTHER,
          title: "T-12 Parser drops rows",
          archived: true,
        },
      }),
  });
  show(<Reminders selected={ID} />);
  const pane = screen.getByRole("region", { name: "Reminder" });
  expect(
    await within(pane).findByRole("link", { name: "T-12 Parser drops rows" }),
  ).toHaveAttribute("href", "#tickets/" + OTHER);
  expect(within(pane).getByText(/\(archived\)/)).toBeInTheDocument();
  expect(within(pane).getByText("Set by Implementer")).toBeInTheDocument();
  await userEvent.click(within(pane).getByRole("button", { name: "Unlink" }));
  await userEvent.click(within(pane).getByRole("button", { name: "Archive" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes).toEqual([
    {
      method: "PATCH",
      path: "/reminders/" + ID,
      body: { targetType: null, targetId: null, revision: 3 },
    },
    {
      method: "POST",
      path: `/reminders/${ID}/archive`,
      body: { revision: 4 },
    },
  ]);
});

it("keeps an archived reminder read-only until it is recovered", async () => {
  const { writes } = serve({
    ["/reminders/" + ID]: () => detail({ archived: true }),
  });
  show(<Reminders selected={ID} />);
  const pane = screen.getByRole("region", { name: "Reminder" });
  expect(await within(pane).findByText(/^Archived:/)).toBeInTheDocument();
  expect(
    within(pane).queryByRole("button", { name: "Mark done" }),
  ).not.toBeInTheDocument();
  expect(within(pane).getByLabelText("Date")).toBeDisabled();
  await userEvent.click(within(pane).getByRole("button", { name: "Recover" }));
  await waitFor(() =>
    expect(writes).toEqual([
      {
        method: "POST",
        path: `/reminders/${ID}/recover`,
        body: { revision: 3 },
      },
    ]),
  );
});

it("says in the sidebar how many reminders are due, and keeps the link's name", async () => {
  const due = { total: 2 };
  const { paths } = serve({
    "/reminders?state=due": () => ({ items: [], total: due.total }),
  });
  const { unmount } = show(<RemindersLink current={false} />);
  // The count describes the link, so it is "Reminders" to anyone who looks for it by name.
  const link = screen.getByRole("link", { name: "Reminders" });
  await waitFor(() => expect(link).toHaveAccessibleDescription("2 due"));
  expect(screen.getByRole("link", { name: "Reminders" })).toBe(link);
  expect(link).toHaveAttribute("href", "#reminders");
  expect(paths).toEqual(["/reminders?state=due&limit=1"]);
  unmount();
  due.total = 0;
  show(<RemindersLink current />);
  // Once none is due, after the count has come back, nothing describes it.
  await waitFor(() => expect(paths).toHaveLength(2));
  const plain = screen.getByRole("link", { name: "Reminders" });
  expect(plain).toHaveAttribute("aria-current", "page");
  expect(plain).not.toHaveAttribute("aria-describedby");
  expect(plain).toHaveAccessibleDescription("");
  expect(plain.textContent).toBe("Reminders");
});
