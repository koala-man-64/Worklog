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
  statusOnDay: status,
  item: item(itemId, title, status),
  ...fields,
});
const day = (date: string, entries: unknown[]) => ({
  date,
  revision: 7,
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
  expect(
    within(entryOf("Archived work")).getByText("Recorded before archiving"),
  ).toBeInTheDocument();
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
  // An archived item's scratchpad, links and files stay on its own page.
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
  links: [],
  attachments: [],
  references: [],
});

it("shows an entry's outcome first and reads its work item only once another tab opens", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing", {
      outcome: "Cut the branch",
      counts: { links: 3, files: 0, scratchpad: true },
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
    "Scratchpad•",
    "Links3",
    "Files",
    "Repositories",
  ]);
  expect(
    within(tabs).getByRole("tab", { name: "Scratchpad, has text" }),
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

it("moves between an entry's tabs with the arrow keys, Home and End", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
  ]);
  state.detail = release();
  render(view("2026-10-28", "2026-10-28"));
  const tab = (name: string) => screen.getByRole("tab", { name });
  await userEvent.click(tab("Outcome / next action"));
  await userEvent.keyboard("{ArrowRight}");
  expect(tab("Scratchpad")).toHaveFocus();
  expect(tab("Scratchpad")).toHaveAttribute("aria-selected", "true");
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

it("sums up the work done below the day's entries and above the open work", () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "done", {
      worked: true,
      outcome: "Tagged the build",
      statusOnDay: "doing",
    }),
    entry("runbook", "Write the runbook", "open", { outcome: "Not reached" }),
  ]);
  render(view("2026-10-28", "2026-10-28"));
  const summary = screen.getByRole("region", { name: "The day’s summary" });
  // The day the page shows groups it, not the item's status now.
  expect(
    within(summary).getByRole("heading", { name: "Doing" }),
  ).toBeInTheDocument();
  expect(summary).toHaveTextContent("Ship the release");
  expect(summary).toHaveTextContent("Tagged the build");
  expect(summary).not.toHaveTextContent("Write the runbook");
  expect(
    entryOf("Write the runbook").compareDocumentPosition(summary) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    summary.compareDocumentPosition(openWork()) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

it("shows no summary on a day with no work", () => {
  state.day = day("2026-10-28", []);
  render(view("2026-10-28", "2026-10-28"));
  expect(
    screen.queryByRole("region", { name: "The day’s summary" }),
  ).toBeNull();
});
