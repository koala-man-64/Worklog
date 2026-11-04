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
  detail: null as unknown,
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
          : path.startsWith("/work-items/")
            ? state.detail
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
const entryOf = (title: string) =>
  screen.getByRole("link", { name: title }).closest("article")!;

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
  // An archived item's tasks, scratchpad, links and files stay on its own page.
  expect(screen.queryByRole("tablist")).toBeNull();
});

it("keeps an entry's controls on its title row and the day's counts beside its heading", () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
  ]);
  render(view("2026-10-28", "2026-10-28"));
  const head = screen
    .getByRole("link", { name: "Ship the release" })
    .closest(".entry-head") as HTMLElement;
  expect(within(head).getByLabelText("Planned")).toBeInTheDocument();
  expect(within(head).getByLabelText("Worked on")).toBeInTheDocument();
  expect(
    within(head).getByRole("button", { name: "Move Ship the release down" }),
  ).toBeInTheDocument();
  expect(
    within(head).getByRole("combobox", { name: "Status" }),
  ).toBeInTheDocument();
  // The outcome's Save now sits beside its label, not on a row of its own.
  const outcome = screen
    .getByRole("textbox", { name: "Outcome / next action" })
    .closest(".draft") as HTMLElement;
  expect(
    within(outcome.querySelector(".field-heading") as HTMLElement).getByRole(
      "button",
      { name: "Save now" },
    ),
  ).toBeInTheDocument();
  const heading = screen
    .getByRole("heading", { name: /^The day’s work/ })
    .closest(".day-heading") as HTMLElement;
  expect(heading.querySelector(".day-summary")).not.toBeNull();
});

const release = () => ({
  id: "release",
  notes: [
    {
      id: "pad",
      revision: 3,
      archived: false,
      title: "Scratchpad",
      text: "Release notes",
      ownerType: "item",
      ownerId: "release",
    },
  ],
  tasks: [
    {
      id: "t1",
      revision: 1,
      archived: false,
      text: "Tag it",
      done: true,
      itemId: "release",
    },
    {
      id: "t2",
      revision: 5,
      archived: false,
      text: "Announce it",
      done: false,
      itemId: "release",
    },
  ],
  links: [],
  attachments: [],
  references: [],
});

it("shows an entry's outcome first and reads its work item only once another tab opens", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing", {
      outcome: "Cut the branch",
      counts: { tasks: 2, tasksDone: 1, links: 3, files: 0, scratchpad: true },
    }),
  ]);
  state.detail = release();
  render(view("2026-10-28", "2026-10-28"));
  const tabs = screen.getByRole("tablist", { name: "Ship the release" });
  // The counts say what each tab holds before anything is read.
  expect(
    within(tabs)
      .getAllByRole("tab")
      .map((tab) => tab.textContent),
  ).toEqual([
    "Outcome / next action",
    "Tasks1/2",
    "Scratchpad•",
    "Links3",
    "Files",
  ]);
  expect(
    within(tabs).getByRole("tab", { name: "Tasks, 1 of 2 done" }),
  ).toHaveAttribute("aria-selected", "false");
  expect(
    within(tabs).getByRole("tab", { name: "Outcome / next action" }),
  ).toHaveAttribute("aria-selected", "true");
  expect(
    screen.getByRole("textbox", { name: "Outcome / next action" }),
  ).toHaveValue("Cut the branch");
  expect(state.paths).not.toContain("/work-items/release");
  await userEvent.click(
    within(tabs).getByRole("tab", { name: "Scratchpad, has text" }),
  );
  expect(
    screen.getByRole("textbox", { name: "Scratchpad (Markdown)" }),
  ).toHaveValue("Release notes");
  expect(state.paths).toContain("/work-items/release");
  // The outcome keeps its place, hidden, so a draft in it survives the switch.
  expect(
    screen.getByRole("textbox", {
      name: "Outcome / next action",
      hidden: true,
    }),
  ).not.toBeVisible();
  const panel = screen.getByRole("tabpanel", { name: "Scratchpad, has text" });
  expect(panel).toBeVisible();
  // The tab names the panel; it repeats no heading of its own.
  expect(within(panel).queryByRole("heading")).toBeNull();
});

it("ticks an entry's tasks off and adds more from its Tasks tab", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
  ]);
  state.detail = release();
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  render(view("2026-10-28", "2026-10-28"));
  await userEvent.click(screen.getByRole("tab", { name: "Tasks" }));
  const panel = screen.getByRole("tabpanel", { name: "Tasks" });
  expect(within(panel).getByRole("checkbox", { name: "Tag it" })).toBeChecked();
  await userEvent.click(
    within(panel).getByRole("checkbox", { name: "Announce it" }),
  );
  await userEvent.type(
    within(panel).getByRole("textbox", { name: "New task" }),
    "Close the milestone{Enter}",
  );
  await waitFor(() => expect(writes(network)).toHaveLength(2));
  expect(
    writes(network).map(([url, init]) => [url, JSON.parse(String(init?.body))]),
  ).toEqual([
    ["/api/v1/tasks/t2", { revision: 5, done: true }],
    [
      "/api/v1/tasks",
      { itemId: "release", text: "Close the milestone", done: false },
    ],
  ]);
});

it("moves between an entry's tabs with the arrow keys, Home and End", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
  ]);
  state.detail = release();
  render(view("2026-10-28", "2026-10-28"));
  const tab = (name: string) => screen.getByRole("tab", { name });
  await userEvent.click(tab("Outcome / next action"));
  await userEvent.keyboard("{ArrowRight}");
  expect(tab("Tasks")).toHaveFocus();
  expect(tab("Tasks")).toHaveAttribute("aria-selected", "true");
  await userEvent.keyboard("{End}");
  expect(tab("Files")).toHaveAttribute("aria-selected", "true");
  expect(
    within(screen.getByRole("tabpanel", { name: "Files" })).getByLabelText(
      "Attach a file",
    ),
  ).toBeInTheDocument();
  await userEvent.keyboard("{ArrowRight}");
  expect(tab("Outcome / next action")).toHaveFocus();
  await userEvent.keyboard("{ArrowLeft}");
  expect(tab("Files")).toHaveFocus();
  await userEvent.keyboard("{Home}");
  expect(tab("Outcome / next action")).toHaveAttribute("aria-selected", "true");
  // Only the selected tab is in the page's tab order.
  expect(
    screen.getAllByRole("tab").map((t) => t.getAttribute("tabindex")),
  ).toEqual(["0", "-1", "-1", "-1", "-1"]);
});

it("shows the stack rank of the day's work and of open work", () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing", {
      item: { ...item("release", "Ship the release", "doing"), rank: 2 },
    }),
  ]);
  state.work = {
    items: [{ ...item("runbook", "Write the runbook", "open"), rank: 5 }],
    total: 1,
  };
  render(view("2026-10-28", "2026-10-28"));
  const heading = screen
    .getByRole("link", { name: "Ship the release" })
    .closest<HTMLElement>(".section-heading")!;
  expect(within(heading).getByText("Stack rank 2")).toBeInTheDocument();
  expect(within(openWork()).getByText("Stack rank 5")).toBeInTheDocument();
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

it("collapses an entry to its heading row and keeps it collapsed on any day", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
    entry("runbook", "Write the runbook", "open"),
  ]);
  const first = render(view("2026-10-28", "2026-10-28"));
  await userEvent.click(
    screen.getByRole("button", { name: "Collapse Ship the release" }),
  );
  const release = entryOf("Ship the release");
  expect(
    within(release).getByRole("button", { name: "Expand Ship the release" }),
  ).toHaveAttribute("aria-expanded", "false");
  // The heading row stays usable, its controls included; the outcome and
  // the other tabs are hidden but still mounted.
  expect(
    within(release).getByRole("button", {
      name: "Reschedule Ship the release",
    }),
  ).toBeVisible();
  expect(
    within(release).getByLabelText("Worked on", { exact: true }),
  ).toBeVisible();
  expect(
    within(release).getByRole("combobox", { name: "Status" }),
  ).toBeVisible();
  expect(
    within(release).getByRole("textbox", {
      name: "Outcome / next action",
      hidden: true,
    }),
  ).not.toBeVisible();
  expect(
    within(release).getByRole("tablist", { hidden: true }),
  ).not.toBeVisible();
  expect(
    within(entryOf("Write the runbook")).getByRole("textbox", {
      name: "Outcome / next action",
    }),
  ).toBeVisible();
  // The same work item planned on another day opens collapsed too.
  first.unmount();
  state.day = day("2026-10-29", [
    entry("release", "Ship the release", "doing", { id: "entry-release-29" }),
  ]);
  render(view("2026-10-28", "2026-10-29"));
  await userEvent.click(
    screen.getByRole("button", { name: "Expand Ship the release" }),
  );
  expect(
    screen.getByRole("button", { name: "Collapse Ship the release" }),
  ).toHaveAttribute("aria-expanded", "true");
  expect(
    screen.getByRole("textbox", { name: "Outcome / next action" }),
  ).toBeVisible();
});

it("collapses and expands every entry of the day at once", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
    entry("runbook", "Write the runbook", "open"),
  ]);
  render(view("2026-10-28", "2026-10-28"));
  const expandAll = screen.getByRole("button", { name: "Expand all" });
  const collapseAll = screen.getByRole("button", { name: "Collapse all" });
  expect(expandAll).toBeDisabled();
  await userEvent.click(collapseAll);
  for (const title of ["Ship the release", "Write the runbook"])
    expect(
      screen.getByRole("button", { name: "Expand " + title }),
    ).toHaveAttribute("aria-expanded", "false");
  expect(collapseAll).toBeDisabled();
  await userEvent.click(
    screen.getByRole("button", { name: "Expand Write the runbook" }),
  );
  expect(collapseAll).toBeEnabled();
  await userEvent.click(expandAll);
  for (const title of ["Ship the release", "Write the runbook"])
    expect(
      screen.getByRole("button", { name: "Collapse " + title }),
    ).toHaveAttribute("aria-expanded", "true");
  expect(expandAll).toBeDisabled();
});
