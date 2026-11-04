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
  // An archived item's scratchpad, links and files stay on its own page.
  expect(
    screen.queryByText("Scratchpad, links, files and repositories"),
  ).toBeNull();
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

it("reads an entry's scratchpad, links and files only once they are opened", async () => {
  state.day = day("2026-10-28", [
    entry("release", "Ship the release", "doing"),
  ]);
  state.detail = {
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
  };
  render(view("2026-10-28", "2026-10-28"));
  const details = screen
    .getByText("Scratchpad, links, files and repositories")
    .closest("details")!;
  expect(state.paths).not.toContain("/work-items/release");
  expect(screen.queryByLabelText("Scratchpad (Markdown)")).toBeNull();
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(await screen.findByLabelText("Scratchpad (Markdown)")).toHaveValue(
    "Release notes",
  );
  expect(state.paths).toContain("/work-items/release");
  expect(
    within(details).getByRole("heading", { name: /^Links/ }),
  ).toBeInTheDocument();
  expect(within(details).getByLabelText("Attach a file")).toBeInTheDocument();
});

it("shows the repositories work links to, and links another from an entry's details", async () => {
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
  const details = within(day_)
    .getByText("Scratchpad, links, files and repositories")
    .closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  const repositories = await within(details).findByRole("group", {
    name: "Repositories",
  });
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
