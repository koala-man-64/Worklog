import { beforeEach, expect, it, vi, type MockInstance } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Today } from "./Today";

const state = vi.hoisted(() => ({
  day: null as unknown,
  work: null as unknown,
  paths: [] as string[],
}));
vi.mock("./hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./hooks")>()),
  useData: (path: string) => {
    state.paths.push(path);
    return {
      data: path.startsWith("/days/")
        ? state.day
        : path.startsWith("/work-items?")
          ? state.work
          : { items: [], total: 0 },
    };
  },
}));

const item = (id: string, title: string, status: string, archived = false) => ({
  id,
  title,
  status,
  archived,
  revision: 4,
});
const entry = (
  itemId: string,
  title: string,
  status: string,
  fields: Record<string, unknown> = {},
) => ({
  id: "entry-" + itemId,
  itemId,
  planned: true,
  worked: false,
  outcome: "",
  revision: 2,
  item: item(itemId, title, status),
  ...fields,
});
const day = (date: string, entries: unknown[]) => ({
  date,
  revision: 7,
  reflection: "",
  entries,
  summary: { worked: [], completed: [], blocked: [] },
  nextItemId: null,
});
function view(today: string, date: string) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Today today={today} initialDate={date} />
    </QueryClientProvider>
  );
}
const writes = (network: MockInstance<typeof fetch>) =>
  network.mock.calls.filter(([, init]) => (init?.method ?? "GET") !== "GET");
const openWork = () =>
  screen.getByRole("heading", { name: /^Open work/ }).closest("section")!;

beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("today-test-" + crypto.randomUUID());
  state.paths = [];
  state.work = { items: [], total: 0 };
});

it("uses historical API summary and makes archived actuals read only", () => {
  state.day = {
    ...day("2026-10-20", [
      entry("historical-item", "Archived work", "done", {
        id: "historical-entry",
        worked: true,
        outcome: "Recorded before archiving",
        revision: 1,
        item: item("historical-item", "Archived work", "done", true),
      }),
    ]),
    summary: { worked: [{}], completed: [], blocked: [] },
  };
  render(view("2026-10-28", "2026-10-20"));
  expect(
    [...document.querySelectorAll(".day-summary strong")].map(
      (el) => el.textContent,
    ),
  ).toEqual(["1", "0", "0"]);
  expect(screen.getByRole("combobox", { name: "Status" })).toBeDisabled();
  expect(screen.getByLabelText("Worked on", { exact: true })).toBeDisabled();
  expect(screen.getByText("Recorded before archiving")).toBeInTheDocument();
  expect(
    screen.queryByRole("textbox", { name: "Outcome / next action" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "Reschedule Archived work" }),
  ).toBeDisabled();
});

it("offers open work that is not on the day and plans it there", async () => {
  state.day = day("2026-10-28", [entry("planned", "Already planned", "open")]);
  state.work = {
    items: [
      item("planned", "Already planned", "open"),
      item("doing", "Write the runbook", "doing"),
      item("blocked", "Fix the pipeline", "blocked"),
    ],
    total: 3,
  };
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  render(view("2026-10-28", "2026-10-28"));
  expect(state.paths).toContain(
    "/work-items?view=all&archived=false&limit=100&status=open&status=doing&status=blocked&q=",
  );
  expect(within(openWork()).queryByText("Already planned")).toBeNull();
  expect(
    within(openWork()).getByRole("link", { name: "Fix the pipeline" }),
  ).toBeInTheDocument();
  await userEvent.click(
    within(openWork()).getByRole("button", {
      name: "Work on Write the runbook today",
    }),
  );
  await waitFor(() => expect(writes(network)).toHaveLength(1));
  const [url, init] = writes(network)[0];
  expect(url).toBe("/api/v1/days/2026-10-28/entries/doing");
  expect(init?.method).toBe("PUT");
  expect(JSON.parse(String(init?.body))).toEqual({
    revision: 0,
    planned: true,
  });
});

it("names the day it plans for when that day is not today", () => {
  state.day = day("2026-10-30", []);
  state.work = { items: [item("review", "Review the plan", "open")], total: 1 };
  render(view("2026-10-28", "2026-10-30"));
  const weekday = new Date("2026-10-30T12:00:00").toLocaleDateString(
    undefined,
    { weekday: "long" },
  );
  expect(
    screen.getByRole("button", {
      name: `Work on Review the plan on ${weekday}`,
    }),
  ).toHaveTextContent(`Work on ${weekday}`);
});

it("says when no other open work is left", () => {
  state.day = day("2026-10-28", [entry("only", "The only item", "open")]);
  state.work = { items: [item("only", "The only item", "open")], total: 1 };
  render(view("2026-10-28", "2026-10-28"));
  expect(
    within(openWork()).getByText("No other open work."),
  ).toBeInTheDocument();
});

it("moves an entry from the picker beside its title, or cancels", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
  ]);
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(
      async (url) =>
        new Response(
          JSON.stringify(
            String(url).endsWith("/days/2026-10-31")
              ? day("2026-10-31", [])
              : {},
          ),
          { status: 200 },
        ),
    );
  render(view("2026-10-28", "2026-10-28"));
  const reschedule = screen.getByRole("button", {
    name: "Reschedule Ship the release",
  });
  const picker = () => screen.queryByLabelText("New date for Ship the release");
  expect(picker()).toBeNull();
  await userEvent.click(reschedule);
  expect(reschedule).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: "Move" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(picker()).toBeNull();
  await userEvent.click(reschedule);
  fireEvent.change(picker()!, { target: { value: "2026-10-31" } });
  await userEvent.click(screen.getByRole("button", { name: "Move" }));
  await waitFor(() => expect(writes(network)).toHaveLength(1));
  const [url, init] = writes(network)[0];
  expect(url).toBe("/api/v1/days/2026-10-28/reschedule");
  expect(JSON.parse(String(init?.body))).toEqual({
    itemId: "release",
    targetDate: "2026-10-31",
    revision: 2,
    targetRevision: 0,
  });
  await waitFor(() => expect(picker()).toBeNull());
});
