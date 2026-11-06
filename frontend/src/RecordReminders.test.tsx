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
import { RecordReminders, TodayReminders } from "./RecordReminders";
import type { Reminder } from "./reminderTypes";

const ITEM = "11111111-1111-4111-8111-111111111111";
const ID = "22222222-2222-4222-8222-222222222222";
const OTHER = "33333333-3333-4333-8333-333333333333";
function reminder(fields: Partial<Reminder> = {}): Reminder {
  return {
    id: ID,
    title: "Ask Ana about the rollout",
    date: "2026-11-06",
    time: "14:00",
    state: "open",
    due: true,
    overdue: false,
    target: null,
    author: { id: "local-user", label: "You", kind: "human", avatar: null },
    archived: false,
    revision: 2,
    createdAt: "2026-11-05T09:00:00Z",
    updatedAt: "2026-11-05T09:00:00Z",
    doneAt: null,
    ...fields,
  };
}
function show(component: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  client.setQueryData(["session"], {
    csrfToken: "token",
    today: "2026-11-06",
    timezone: "America/Chicago",
    dataId: "record-reminders-test",
  });
  return render(
    <QueryClientProvider client={client}>{component}</QueryClientProvider>,
  );
}
type Write = { method: string; path: string; body: Record<string, unknown> };
function serve(lists: (path: string) => { items: Reminder[]; total: number }) {
  const writes: Write[] = [];
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    if (method !== "GET") {
      const body = JSON.parse(String(init?.body));
      writes.push({ method, path, body });
      return new Response(
        JSON.stringify({
          ...reminder(),
          notes: "",
          revision: Number(body.revision ?? 0) + 1,
        }),
      );
    }
    paths.push(path);
    return new Response(JSON.stringify(lists(path)));
  });
  return { writes, paths };
}
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("record-reminders-" + crypto.randomUUID());
  // 14:25 on Friday 6 November 2026 in Chicago; only the clock is faked, not timers.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-06T20:25:00Z"));
});
afterEach(() => vi.useRealTimers());

it("lists a record's open reminders and adds one about that record", async () => {
  const { paths, writes } = serve(() => ({
    items: [
      reminder(),
      reminder({
        id: OTHER,
        title: "Send the summary",
        date: "2026-11-09",
        time: null,
        due: false,
        author: {
          id: "agent-1",
          label: "Implementer",
          kind: "agent",
          avatar: null,
        },
      }),
    ],
    total: 2,
  }));
  show(<RecordReminders targetType="item" targetId={ITEM} />);
  const rows = await screen.findAllByRole("listitem");
  expect(rows.map((r) => within(r).getByRole("link").textContent)).toEqual([
    "Ask Ana about the rollout",
    "Send the summary",
  ]);
  expect(rows[0]).toHaveTextContent("Today · 14:00");
  expect(rows[1]).toHaveTextContent("Set by Implementer");
  expect(paths[0]).toBe(
    `/reminders?state=open&targetType=item&targetId=${ITEM}&limit=50`,
  );
  expect(screen.getByRole("heading", { name: "Reminders 2" })).toBeVisible();
  // Only a due reminder can be snoozed.
  expect(
    within(rows[1]).queryByRole("button", { name: /Snooze/ }),
  ).not.toBeInTheDocument();
  await userEvent.type(
    screen.getByRole("textbox", { name: "New reminder" }),
    "Follow up on the review",
  );
  fireEvent.change(screen.getByLabelText("New reminder date"), {
    target: { value: "2026-11-10" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Add reminder" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toEqual({
    method: "POST",
    path: "/reminders",
    body: {
      title: "Follow up on the review",
      notes: "",
      date: "2026-11-10",
      time: null,
      targetType: "item",
      targetId: ITEM,
    },
  });
});

it("marks a reminder done from its row and snoozes a due one to tomorrow", async () => {
  const { writes } = serve(() => ({ items: [reminder()], total: 1 }));
  show(<RecordReminders targetType="person" targetId={ITEM} />);
  await userEvent.click(
    await screen.findByRole("button", {
      name: "Snooze Ask Ana about the rollout to tomorrow",
    }),
  );
  await waitFor(() => expect(writes).toHaveLength(1));
  await waitFor(() =>
    expect(
      screen.getByRole("button", {
        name: "Mark Ask Ana about the rollout done",
      }),
    ).toBeEnabled(),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Mark Ask Ana about the rollout done" }),
  );
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes.map((w) => w.body)).toEqual([
    { date: "2026-11-07", time: "14:00", revision: 2 },
    { state: "done", revision: 3 },
  ]);
});

it("takes no new reminder on an archived record and says when there is none", async () => {
  serve(() => ({ items: [], total: 0 }));
  show(
    <RecordReminders
      targetType="repository"
      targetId={ITEM}
      archived
      heading="h2"
    />,
  );
  expect(
    await screen.findByText("No open reminders for this repository."),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("textbox", { name: "New reminder" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("heading", { level: 2, name: "Reminders" }),
  ).toBeVisible();
});

it("shows on Today every open reminder due by the day's end, and on another day only that day's", async () => {
  const { paths } = serve((path) =>
    path.includes("until=")
      ? {
          items: [
            reminder({
              id: OTHER,
              title: "Reply to Ana",
              date: "2026-11-04",
              overdue: true,
              target: {
                type: "ticket",
                id: ITEM,
                title: "T-3 Parser drops rows",
                archived: false,
              },
            }),
            reminder({ title: "Merge #113", time: "17:00", due: false }),
          ],
          total: 2,
        }
      : { items: [], total: 0 },
  );
  const { unmount } = show(
    <TodayReminders date="2026-11-06" today="2026-11-06" />,
  );
  const section = await screen.findByRole("region", { name: "Reminders 2" });
  expect(paths[0]).toBe("/reminders?state=open&until=2026-11-06&limit=50");
  const rows = within(section).getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent(
    /^Reply to AnaOverdue · .* · 14:00 · T-3 Parser drops rows/,
  );
  expect(
    within(rows[0]).getByRole("link", { name: "T-3 Parser drops rows" }),
  ).toHaveAttribute("href", "#tickets/" + ITEM);
  expect(rows[1]).toHaveTextContent("Merge #113Today · 17:00");
  unmount();
  show(<TodayReminders date="2026-11-09" today="2026-11-06" />);
  await waitFor(() =>
    expect(paths).toContain("/reminders?state=open&date=2026-11-09&limit=50"),
  );
  // With none on that day, Today shows no section at all.
  expect(screen.queryByRole("region")).not.toBeInTheDocument();
});
