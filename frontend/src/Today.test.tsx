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
import { formatDiscussionDate } from "./discussionFormatting";
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
            : path.startsWith("/repositories/")
              ? { id: path.split("/")[2], name: "Platform", archived: false }
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
  // Its entry stays on the day it recorded, named for that day.
  const weekday = new Date("2026-10-20T12:00:00").toLocaleDateString(
    undefined,
    { weekday: "long" },
  );
  const remove = screen.getByRole("button", {
    name: `Remove Archived work from ${weekday}`,
  });
  expect(remove).toHaveTextContent(`Remove from ${weekday}`);
  expect(remove).toBeDisabled();
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

// When the release's tasks were added, and when the first was ticked.
const ADDED = "2026-10-26T15:04:00.123456+00:00";
const TICKED = "2026-10-28T09:30:00+00:00";
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
      createdAt: ADDED,
      updatedAt: TICKED,
    },
    {
      id: "t2",
      revision: 5,
      archived: false,
      text: "Announce it",
      done: false,
      itemId: "release",
      createdAt: ADDED,
      updatedAt: ADDED,
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
    "Repositories",
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

// A drag's data, as the browser carries it from the grip to the drop.
function transfer() {
  const data = new Map<string, string>();
  return {
    effectAllowed: "",
    get types() {
      return [...data.keys()];
    },
    setData: (type: string, value: string) => void data.set(type, value),
    getData: (type: string) => data.get(type) ?? "",
  };
}
const taskRow = (panel: HTMLElement, text: string) =>
  within(panel)
    .getByRole("checkbox", { name: text })
    .closest(".task") as HTMLElement;

it("says when each of an entry's tasks was added and last changed", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
  ]);
  state.detail = release();
  render(view("2026-10-28", "2026-10-28"));
  await userEvent.click(screen.getByRole("tab", { name: "Tasks" }));
  const panel = screen.getByRole("tabpanel", { name: "Tasks" });
  const times = (text: string) =>
    taskRow(panel, text).querySelector(".task-times") as HTMLElement;
  expect(times("Tag it")).toHaveTextContent(
    `Created ${formatDiscussionDate(ADDED)} · Updated ${formatDiscussionDate(TICKED)}`,
  );
  expect(
    [...times("Tag it").querySelectorAll("time")].map((t) => [
      t.getAttribute("dateTime"),
      t.getAttribute("title"),
    ]),
  ).toEqual([
    [ADDED, new Date(ADDED).toLocaleString()],
    [TICKED, new Date(TICKED).toLocaleString()],
  ]);
  expect(times("Announce it")).toHaveTextContent(
    `Created ${formatDiscussionDate(ADDED)} · Updated ${formatDiscussionDate(ADDED)}`,
  );
  // The times sit beside the checkbox, not in its name.
  expect(
    within(panel).getByRole("checkbox", { name: "Tag it" }),
  ).toBeInTheDocument();
});

it("moves an entry's tasks up and down with their buttons", async () => {
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
  const button = (name: string) => within(panel).getByRole("button", { name });
  // The first task can't go up, nor the last down.
  expect(button("Move task Tag it up")).toBeDisabled();
  expect(button("Move task Announce it down")).toBeDisabled();
  await userEvent.click(button("Move task Announce it up"));
  await waitFor(() => expect(button("Move task Tag it down")).toBeEnabled());
  await userEvent.click(button("Move task Tag it down"));
  await waitFor(() => expect(writes(network)).toHaveLength(2));
  // Each move names its task's revision and its new place, 1 first.
  expect(
    writes(network).map(([url, init]) => [url, JSON.parse(String(init?.body))]),
  ).toEqual([
    ["/api/v1/tasks/t2/move", { revision: 5, position: 1 }],
    ["/api/v1/tasks/t1/move", { revision: 1, position: 2 }],
  ]);
});

it("moves an entry's task by dragging its grip onto another, and only within its own list", async () => {
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
  // A task dragged from another work item's list is not this list's to move.
  const elsewhere = transfer();
  elsewhere.setData(
    "application/x-worklog-task",
    JSON.stringify({ taskId: "t2", itemId: "other", dataId: "x" }),
  );
  fireEvent.dragOver(taskRow(panel, "Tag it"), { dataTransfer: elsewhere });
  fireEvent.drop(taskRow(panel, "Tag it"), { dataTransfer: elsewhere });
  const dataTransfer = transfer();
  fireEvent.dragStart(
    within(panel).getByRole("img", {
      name: "Drag task Announce it to reorder",
    }),
    { dataTransfer },
  );
  expect(taskRow(panel, "Announce it")).toHaveClass("dragging");
  fireEvent.dragOver(taskRow(panel, "Tag it"), { dataTransfer });
  fireEvent.drop(taskRow(panel, "Tag it"), { dataTransfer });
  await waitFor(() => expect(writes(network)).toHaveLength(1));
  expect(
    writes(network).map(([url, init]) => [url, JSON.parse(String(init?.body))]),
  ).toEqual([["/api/v1/tasks/t2/move", { revision: 5, position: 1 }]]);
  expect(taskRow(panel, "Announce it")).not.toHaveClass("dragging");
  // The drop is the list's alone: the day entry around it neither reorders nor reports an error.
  expect(screen.queryByRole("alert")).toBeNull();
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
  expect(tab("Repositories")).toHaveAttribute("aria-selected", "true");
  expect(
    within(screen.getByRole("tabpanel", { name: "Repositories" })).getByRole(
      "button",
      { name: "Link repository" },
    ),
  ).toBeInTheDocument();
  await userEvent.keyboard("{ArrowLeft}");
  expect(tab("Files")).toHaveFocus();
  expect(
    within(screen.getByRole("tabpanel", { name: "Files" })).getByLabelText(
      "Attach a file",
    ),
  ).toBeInTheDocument();
  await userEvent.keyboard("{End}{ArrowRight}");
  expect(tab("Outcome / next action")).toHaveFocus();
  await userEvent.keyboard("{ArrowLeft}");
  expect(tab("Repositories")).toHaveFocus();
  await userEvent.keyboard("{Home}");
  expect(tab("Outcome / next action")).toHaveAttribute("aria-selected", "true");
  // Only the selected tab is in the page's tab order.
  expect(
    screen.getAllByRole("tab").map((t) => t.getAttribute("tabindex")),
  ).toEqual(["0", "-1", "-1", "-1", "-1", "-1"]);
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

it("shows the repositories work links to, and links another from an entry's Repositories tab", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing", {
      item: {
        ...item("release", "Ship the release", "doing"),
        repositoryIds: ["repo-a"],
      },
    }),
  ]);
  state.work = {
    items: [
      {
        ...item("runbook", "Write the runbook", "open"),
        repositoryIds: ["repo-a"],
      },
    ],
    total: 1,
  };
  state.detail = {
    ...item("release", "Ship the release", "doing"),
    repositoryIds: ["repo-a"],
    notes: [],
    links: [],
    attachments: [],
    references: [],
  };
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (_input, init) =>
      (init?.method ?? "GET") === "GET"
        ? new Response(
            JSON.stringify({
              items: [
                { id: "repo-a", name: "Platform", url: "", archived: false },
                { id: "repo-b", name: "Billing", url: "", archived: false },
              ],
              total: 2,
            }),
          )
        : new Response("{}"),
    );
  render(view("2026-10-28", "2026-10-28"));
  const day_ = screen
    .getByRole("link", { name: "Ship the release" })
    .closest("article")!;
  expect(within(day_).getByRole("link", { name: "Platform" })).toHaveAttribute(
    "href",
    "#repositories/repo-a",
  );
  expect(
    within(openWork()).getByRole("link", { name: "Platform" }),
  ).toBeInTheDocument();
  // Capturing from Today can link repositories too.
  expect(
    screen.getByRole("group", { name: "New work repositories" }),
  ).toBeInTheDocument();
  // The tab counts the item's repositories before the item is read.
  await userEvent.click(
    within(day_).getByRole("tab", { name: "Repositories, 1" }),
  );
  const repositories = await within(
    within(day_).getByRole("tabpanel", { name: "Repositories, 1" }),
  ).findByRole("group", { name: "Repositories" });
  await userEvent.click(
    within(repositories).getByRole("button", { name: "Link repository" }),
  );
  await userEvent.click(
    await within(repositories).findByRole("button", { name: /Billing/ }),
  );
  await waitFor(() => expect(writes(network)).toHaveLength(1));
  const [url, init] = writes(network)[0];
  expect([url, init?.method]).toEqual(["/api/v1/work-items/release", "PATCH"]);
  expect(JSON.parse(String(init?.body))).toEqual({
    revision: 4,
    repositoryIds: ["repo-a", "repo-b"],
  });
});

it("offers open work that is not on the day and plans it there", async () => {
  state.day = day("2026-10-28", [entry("planned", "Already planned", "open")]);
  state.work = {
    items: [
      item("planned", "Already planned", "open"),
      item("doing", "Write the runbook", "doing"),
      item("waiting", "Hear back from review", "waiting"),
      item("blocked", "Fix the pipeline", "blocked"),
    ],
    total: 4,
  };
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  render(view("2026-10-28", "2026-10-28"));
  // Anything not finished, waiting work included.
  expect(state.paths).toContain(
    "/work-items?view=all&archived=false&limit=100&status=open&status=doing&status=waiting&status=blocked&q=",
  );
  expect(within(openWork()).queryByText("Already planned")).toBeNull();
  for (const name of ["Hear back from review", "Fix the pipeline"])
    expect(within(openWork()).getByRole("link", { name })).toBeInTheDocument();
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
  state.day = day("2026-10-30", [entry("draft", "Draft the plan", "open")]);
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
  // Taking work off the day names that day too.
  expect(
    screen.getByRole("button", {
      name: `Remove Draft the plan from ${weekday}`,
    }),
  ).toHaveTextContent(`Remove from ${weekday}`);
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

it("takes an entry that only plans its work off the day at once", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
  ]);
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(
      async () => new Response('{"ok":true}', { status: 200 }),
    );
  render(view("2026-10-28", "2026-10-28"));
  const remove = screen.getByRole("button", {
    name: "Remove Ship the release from today",
  });
  expect(remove).toHaveTextContent("Remove from today");
  // Nothing recorded to discard, so nothing to confirm.
  await userEvent.click(remove);
  expect(screen.queryByRole("group", { name: /anyway\?$/ })).toBeNull();
  await waitFor(() => expect(writes(network)).toHaveLength(1));
  const [url, init] = writes(network)[0];
  expect([url, init?.method]).toEqual([
    "/api/v1/days/2026-10-28/entries/release/remove",
    "POST",
  ]);
  expect(JSON.parse(String(init?.body))).toEqual({ revision: 2 });
});

it("asks before taking off an entry the day records work on, and keeps it on Cancel", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing", {
      worked: true,
      outcome: "Cut the branch",
    }),
    entry("runbook", "Write the runbook", "open", { outcome: "Outline only" }),
  ]);
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(
      async () => new Response('{"ok":true}', { status: 200 }),
    );
  render(view("2026-10-28", "2026-10-28"));
  const question = () => screen.queryByRole("group", { name: /anyway\?$/ });
  const remove = screen.getByRole("button", {
    name: "Remove Ship the release from today",
  });
  await userEvent.click(remove);
  expect(question()).toHaveAccessibleName(
    "It is marked “Worked on” and has an outcome / next action. Remove it from today anyway?",
  );
  // Focus lands on the choice that keeps the record, and returns to the button after it.
  const cancel = within(question()!).getByRole("button", { name: "Cancel" });
  expect(cancel).toHaveFocus();
  await userEvent.click(cancel);
  expect(question()).toBeNull();
  expect(remove).toHaveFocus();
  // Escape closes the question too, and the reschedule picker replaces it.
  await userEvent.click(remove);
  await userEvent.keyboard("{Escape}");
  expect(question()).toBeNull();
  expect(remove).toHaveFocus();
  await userEvent.click(remove);
  await userEvent.click(
    screen.getByRole("button", { name: "Reschedule Ship the release" }),
  );
  expect(question()).toBeNull();
  expect(
    screen.getByLabelText("New date for Ship the release"),
  ).toBeInTheDocument();
  expect(writes(network)).toHaveLength(0);
  // An outcome alone is asked about as well, and the question closes the picker.
  await userEvent.click(
    screen.getByRole("button", { name: "Remove Write the runbook from today" }),
  );
  expect(screen.queryByLabelText("New date for Ship the release")).toBeNull();
  expect(question()).toHaveAccessibleName(
    "It has an outcome / next action. Remove it from today anyway?",
  );
  await userEvent.click(
    within(question()!).getByRole("button", { name: "Confirm remove" }),
  );
  await waitFor(() => expect(writes(network)).toHaveLength(1));
  const [url, init] = writes(network)[0];
  expect(url).toBe("/api/v1/days/2026-10-28/entries/runbook/remove");
  expect(JSON.parse(String(init?.body))).toEqual({ revision: 2 });
  await waitFor(() => expect(question()).toBeNull());
});

it("asks before discarding an outcome typed on the day and not saved yet", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
  ]);
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  render(view("2026-10-28", "2026-10-28"));
  const removals = () =>
    writes(network).filter(([url]) => String(url).endsWith("/remove"));
  await userEvent.type(
    screen.getByRole("textbox", { name: "Outcome / next action" }),
    "Blocked on infra, retry Monday",
  );
  // The click that leaves the field starts its save; the day's copy still has no outcome.
  await userEvent.click(
    screen.getByRole("button", { name: "Remove Ship the release from today" }),
  );
  const question = screen.getByRole("group", { name: /anyway\?$/ });
  expect(question).toHaveAccessibleName(
    "It has an outcome / next action that isn't saved yet. Remove it from today anyway?",
  );
  expect(removals()).toHaveLength(0);
  await userEvent.click(
    within(question).getByRole("button", { name: "Confirm remove" }),
  );
  await waitFor(() => expect(removals()).toHaveLength(1));
});

it("keeps the question open and says why when the day refuses the removal", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing", { worked: true }),
  ]);
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(
        JSON.stringify({
          error: {
            code: "conflict",
            message: "This record changed. Reload and review your draft.",
          },
        }),
        { status: 409 },
      ),
  );
  render(view("2026-10-28", "2026-10-28"));
  await userEvent.click(
    screen.getByRole("button", { name: "Remove Ship the release from today" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Confirm remove" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "This record changed. Reload and review your draft.",
  );
  expect(
    screen.getByRole("group", {
      name: "It is marked “Worked on”. Remove it from today anyway?",
    }),
  ).toBeInTheDocument();
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
