import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Usage } from "./UsagePage";
import type { UsageReport } from "./apiTypes";
import { formatDay } from "./ui/format";

const report: UsageReport = {
  file: "C:/Worklog/usage.sqlite3",
  version: "1.0.0",
  recordedSince: "2026-11-05",
  window: { days: 30, from: "2026-10-10", to: "2026-11-08" },
  actor: null,
  daily: [
    {
      day: "2026-11-05",
      pageViews: 3,
      reads: { person: 10, agent: 2 },
      changes: { person: 2, agent: 0 },
      failed: 1,
    },
    {
      day: "2026-11-07",
      pageViews: 1,
      reads: { person: 4, agent: 20 },
      changes: { person: 1, agent: 3 },
      failed: 0,
    },
  ],
  pages: {
    used: [
      { page: "today", views: 3, lastUsed: "2026-11-07" },
      { page: "work", views: 1, lastUsed: "2026-11-05" },
    ],
    unused: [
      { page: "releases", lastUsed: "2026-09-30" },
      { page: "people", lastUsed: null },
    ],
  },
  calls: {
    used: [
      {
        call: "GET /work-items",
        scope: "agent:read",
        person: 14,
        agent: 22,
        failed: 1,
        lastUsed: "2026-11-07",
      },
      {
        call: "POST /work-items",
        scope: "human",
        person: 3,
        agent: 0,
        failed: 0,
        lastUsed: "2026-11-07",
      },
      {
        call: "POST /tickets",
        scope: "agent:ticket",
        person: 0,
        agent: 3,
        failed: 0,
        lastUsed: "2026-11-07",
      },
    ],
    unused: [
      {
        call: "POST /work-items/{identity}/archive",
        scope: "human",
        lastUsed: "2026-10-02",
      },
      { call: "GET /tickets/{ref}", scope: "agent:read", lastUsed: null },
      { call: "GET /search", scope: "agent:read", lastUsed: null },
    ],
  },
};

/** `report` as GET /usage?actor=person answers it: your uses alone. */
const yours: UsageReport = {
  ...report,
  actor: "person",
  daily: [
    {
      day: "2026-11-05",
      pageViews: 3,
      reads: { person: 10, agent: 0 },
      changes: { person: 2, agent: 0 },
      failed: 1,
    },
    {
      day: "2026-11-07",
      pageViews: 1,
      reads: { person: 4, agent: 0 },
      changes: { person: 1, agent: 0 },
      failed: 0,
    },
  ],
  calls: {
    used: [
      {
        call: "GET /work-items",
        scope: "agent:read",
        person: 14,
        agent: 0,
        failed: 1,
        lastUsed: "2026-11-07",
      },
      {
        call: "POST /work-items",
        scope: "human",
        person: 3,
        agent: 0,
        failed: 0,
        lastUsed: "2026-11-07",
      },
    ],
    // Only agents filed tickets, so you never have.
    unused: [
      ...report.calls.unused,
      { call: "POST /tickets", scope: "agent:ticket", lastUsed: null },
    ],
  },
};

/** `report` as GET /usage?actor=agent answers it: agents' uses alone, of what they may use. */
const theirs: UsageReport = {
  ...report,
  actor: "agent",
  daily: [
    {
      day: "2026-11-05",
      pageViews: 0,
      reads: { person: 0, agent: 2 },
      changes: { person: 0, agent: 0 },
      failed: 0,
    },
    {
      day: "2026-11-07",
      pageViews: 0,
      reads: { person: 0, agent: 20 },
      changes: { person: 0, agent: 3 },
      failed: 0,
    },
  ],
  // Agents open no pages, and what is yours alone isn't theirs to use.
  pages: { used: [], unused: [] },
  calls: {
    used: [
      {
        call: "GET /work-items",
        scope: "agent:read",
        person: 0,
        agent: 22,
        failed: 0,
        lastUsed: "2026-11-07",
      },
      {
        call: "POST /tickets",
        scope: "agent:ticket",
        person: 0,
        agent: 3,
        failed: 0,
        lastUsed: "2026-11-07",
      },
    ],
    unused: [
      { call: "GET /tickets/{ref}", scope: "agent:read", lastUsed: null },
      { call: "GET /search", scope: "agent:read", lastUsed: null },
    ],
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  setDataContext("usage-test");
});

/** Answers GET /usage with `answer(days, actor)`; returns the paths asked for, query included. */
function serve(
  answer: (
    days: string,
    actor: string | null,
  ) => UsageReport | Promise<UsageReport> | "unreadable",
) {
  const asked: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://worklog.test");
    asked.push(url.pathname.replace("/api/v1", "") + url.search);
    const body = await answer(
      url.searchParams.get("days") ?? "",
      url.searchParams.get("actor"),
    );
    return body === "unreadable"
      ? new Response(
          JSON.stringify({
            error: {
              code: "usage_unavailable",
              message:
                "C:/Worklog/usage.sqlite3 holds usage format 99, which this version of Worklog does not read",
            },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        )
      : new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
  });
  return asked;
}

function show() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Usage />
    </QueryClientProvider>,
  );
}

it("shows the window's figures, its days and its pages, nothing opened included", async () => {
  const asked = serve(() => report);
  show();
  const views = await screen.findByText("Page views", { selector: "dt" });
  expect(asked).toEqual(["/usage?days=30"]);
  const tile = (name: string) =>
    screen.getByText(name, { selector: "dt" }).closest(".usage-tile")!;
  expect(views.closest(".usage-tile")).toHaveTextContent(
    "Page views42 of 4 pages opened",
  );
  expect(tile("Changes")).toHaveTextContent("Changes63 by you, 3 by agents");
  expect(tile("Reads")).toHaveTextContent("Reads3614 by you, 22 by agents");
  // 1 of 42 calls.
  expect(tile("Failed calls")).toHaveTextContent("Failed calls12.4% of calls");
  expect(tile("Features used")).toHaveTextContent("3 of 63 not used");
  expect(screen.getByRole("meter", { name: "Features used" })).toHaveAttribute(
    "aria-valuenow",
    "3",
  );
  expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  // Counting began inside the window, so its days start there.
  expect(
    screen.getByText(`Counting began ${formatDay("2026-11-05")}.`),
  ).toBeInTheDocument();
  const changes = screen
    .getByRole("heading", { name: "Changes per day" })
    .closest("figure")!;
  expect(within(changes).getByRole("img")).toHaveAccessibleName(
    `Changes per day, ${formatDay("2026-11-05")} to ${formatDay("2026-11-08")}: you 3, agents 3; the most, 4, ${formatDay("2026-11-07")}`,
  );
  const rows = within(within(changes).getByRole("table")).getAllByRole("row");
  expect(rows.map((row) => row.textContent)).toEqual([
    "DayYouAgents",
    `${formatDay("2026-11-05")}20`,
    `${formatDay("2026-11-06")}00`,
    `${formatDay("2026-11-07")}13`,
    `${formatDay("2026-11-08")}00`,
  ]);
  expect(
    screen.getByText("Reads include pages refreshing what they show", {
      exact: false,
    }),
  ).toBeInTheDocument();
  const pages = within(
    screen.getByRole("region", { name: "Pages" }),
  ).getAllByRole("listitem");
  expect(pages.map((page) => page.textContent)).toEqual([
    "Today3",
    "Work1",
    // In sidebar order: Releases is in Plan, People in Directory.
    `ReleasesNot opened since ${formatDay("2026-09-30")}`,
    "PeopleNever opened",
  ]);
  expect(screen.getByText("C:/Worklog/usage.sqlite3")).toBeInTheDocument();
});

it("opens an area onto its calls, those nothing made with when they last were", async () => {
  serve(() => report);
  const user = userEvent.setup();
  show();
  const areas = within(
    await screen.findByRole("region", { name: "Features by area" }),
  );
  const summaries = areas
    .getAllByText(/^\/[a-z-]+$/)
    .map((name) => name.closest("summary")!);
  expect(summaries.map((summary) => summary.textContent)).toEqual([
    "/work-items2 of 3 used17 by you, 22 by agents1 failed",
    "/tickets1 of 2 used0 by you, 3 by agents",
    "/search0 of 1 usedNo calls",
  ]);
  await user.click(summaries[0]);
  const calls = within(summaries[0].closest("details")!).getAllByRole("row");
  expect(calls.map((row) => row.textContent)).toEqual([
    "CallWho may make itYouAgentsFailedLast used",
    `GET /work-itemsagent:read14221${formatDay("2026-11-07")}`,
    `POST /work-itemsYou only300${formatDay("2026-11-07")}`,
    `POST /work-items/{identity}/archiveYou onlyNot used${formatDay("2026-10-02")}`,
  ]);
  await user.click(summaries[2]);
  expect(
    within(summaries[2].closest("details")!).getByRole("row", {
      name: /GET \/search/,
    }),
  ).toHaveTextContent("GET /searchagent:readNot usedNever");
});

it("asks for another window and keeps the last one on screen, dimmed, until it comes", async () => {
  let release: (value: UsageReport) => void = () => undefined;
  const asked = serve((days) =>
    days === "7"
      ? new Promise<UsageReport>((resolve) => {
          release = resolve;
        })
      : report,
  );
  const user = userEvent.setup();
  show();
  await screen.findByRole("heading", { name: "Page views per day" });
  await user.click(screen.getByRole("button", { name: "7 days" }));
  expect(asked).toEqual(["/usage?days=30", "/usage?days=7"]);
  expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  // The 30 days' answer stays while the 7 days' loads.
  const body = document.querySelector(".usage")!;
  expect(body).toHaveClass("stale");
  expect(body).toHaveAttribute("aria-busy", "true");
  expect(
    screen.getByText("Changes", { selector: "dt" }).closest(".usage-tile"),
  ).toHaveTextContent("6");
  release({
    ...report,
    window: { days: 7, from: "2026-11-02", to: "2026-11-08" },
    daily: [],
    pages: {
      used: [],
      unused: [...report.pages.used, ...report.pages.unused].map(
        ({ page }) => ({ page, lastUsed: null }),
      ),
    },
  });
  expect(
    await screen.findByText("Nothing was used in these 7 days."),
  ).toBeInTheDocument();
  expect(document.querySelector(".usage")).not.toHaveClass("stale");
  expect(
    screen.queryByRole("heading", { name: "Page views per day" }),
  ).not.toBeInTheDocument();
  // What nothing used still shows.
  expect(
    within(screen.getByRole("region", { name: "Pages" })).getAllByText(
      "Never opened",
    ),
  ).toHaveLength(4);
});

/** Each choice of whose uses the page counts, and whether it is the one pressed. */
const whose = () =>
  within(screen.getByRole("group", { name: "Whose uses" }))
    .getAllByRole("button")
    .map(
      (button) =>
        `${button.textContent}: ${button.getAttribute("aria-pressed")}`,
    );

it("counts your uses alone on asking, so each figure, chart and call is yours", async () => {
  let release: (value: UsageReport) => void = () => undefined;
  const week = { days: 7, from: "2026-11-02", to: "2026-11-08" };
  const asked = serve((days, actor) => {
    if (actor !== "person")
      return days === "7" ? { ...report, window: week } : report;
    if (days === "7") return { ...yours, window: week, daily: [] };
    return new Promise<UsageReport>((resolve) => {
      release = resolve;
    });
  });
  const user = userEvent.setup();
  show();
  const tile = (name: string) =>
    screen.getByText(name, { selector: "dt" }).closest(".usage-tile")!;
  await screen.findByRole("heading", { name: "Changes per day" });
  expect(whose()).toEqual([
    "You and agents: true",
    "You: false",
    "Agents: false",
  ]);
  await user.click(screen.getByRole("button", { name: "You" }));
  expect(asked).toEqual(["/usage?days=30", "/usage?days=30&actor=person"]);
  expect(whose()).toEqual([
    "You and agents: false",
    "You: true",
    "Agents: false",
  ]);
  // Until the answer comes, the last one stays, dimmed, drawn as it counts: both.
  expect(document.querySelector(".usage")).toHaveClass("stale");
  expect(tile("Changes")).toHaveTextContent("Changes63 by you, 3 by agents");
  release(yours);
  await waitFor(() =>
    expect(tile("Changes")).toHaveTextContent("Changes3By you"),
  );
  expect(document.querySelector(".usage")).not.toHaveClass("stale");
  expect(tile("Reads")).toHaveTextContent("Reads14By you");
  // 1 of 17 calls.
  expect(tile("Failed calls")).toHaveTextContent("Failed calls15.9% of calls");
  expect(tile("Features used")).toHaveTextContent("2 of 64 not used");
  // One series, so no legend.
  const changes = screen
    .getByRole("heading", { name: "Changes per day" })
    .closest("figure")!;
  expect(within(changes).queryByRole("list")).not.toBeInTheDocument();
  expect(within(changes).getByRole("img")).toHaveAccessibleName(
    `Changes per day, ${formatDay("2026-11-05")} to ${formatDay("2026-11-08")}: 3 in all; the most, 2, ${formatDay("2026-11-05")}`,
  );
  const rows = within(within(changes).getByRole("table")).getAllByRole("row");
  expect(rows.map((row) => row.textContent)).toEqual([
    "DayYou",
    `${formatDay("2026-11-05")}2`,
    `${formatDay("2026-11-06")}0`,
    `${formatDay("2026-11-07")}1`,
    `${formatDay("2026-11-08")}0`,
  ]);
  const summaries = within(
    screen.getByRole("region", { name: "Features by area" }),
  )
    .getAllByText(/^\/[a-z-]+$/)
    .map((name) => name.closest("summary")!);
  expect(summaries.map((summary) => summary.textContent)).toEqual([
    "/work-items2 of 3 used17 by you1 failed",
    "/search0 of 1 usedNo calls",
    "/tickets0 of 2 usedNo calls",
  ]);
  await user.click(summaries[0]);
  const area = within(summaries[0].closest("details")!);
  expect(area.getAllByRole("row").map((row) => row.textContent)).toEqual([
    "CallWho may make itYouFailedLast used",
    `GET /work-itemsagent:read141${formatDay("2026-11-07")}`,
    `POST /work-itemsYou only30${formatDay("2026-11-07")}`,
    `POST /work-items/{identity}/archiveYou onlyNot used${formatDay("2026-10-02")}`,
  ]);
  expect(area.getByText("Not used")).toHaveAttribute("colspan", "2");
  // Another window still counts yours alone.
  await user.click(screen.getByRole("button", { name: "7 days" }));
  expect(
    await screen.findByText("You used nothing in these 7 days."),
  ).toBeInTheDocument();
  expect(asked.at(-1)).toBe("/usage?days=7&actor=person");
  await user.click(screen.getByRole("button", { name: "You and agents" }));
  expect(asked.at(-1)).toBe("/usage?days=7");
  await waitFor(() =>
    expect(tile("Changes")).toHaveTextContent("Changes63 by you, 3 by agents"),
  );
});

it("counts agents' uses alone on asking, of what they may use, with no page views", async () => {
  const week = { days: 7, from: "2026-11-02", to: "2026-11-08" };
  const asked = serve((days, actor) =>
    actor !== "agent"
      ? report
      : days === "7"
        ? { ...theirs, window: week, daily: [] }
        : theirs,
  );
  const user = userEvent.setup();
  show();
  const tile = (name: string) =>
    screen.getByText(name, { selector: "dt" }).closest(".usage-tile")!;
  await screen.findByRole("heading", { name: "Page views per day" });
  await user.click(screen.getByRole("button", { name: "Agents" }));
  expect(asked).toEqual(["/usage?days=30", "/usage?days=30&actor=agent"]);
  await waitFor(() =>
    expect(tile("Changes")).toHaveTextContent("Changes3By agents"),
  );
  expect(whose()).toEqual([
    "You and agents: false",
    "You: false",
    "Agents: true",
  ]);
  expect(tile("Reads")).toHaveTextContent("Reads22By agents");
  expect(tile("Failed calls")).toHaveTextContent("Failed calls0None failed");
  expect(tile("Features used")).toHaveTextContent("2 of 42 not used");
  // Only you open pages: no page views to count, chart or list.
  expect(
    screen.queryByText("Page views", { selector: "dt" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Page views per day" }),
  ).not.toBeInTheDocument();
  expect(
    within(screen.getByRole("region", { name: "Pages" })).getByText(
      "Only you open pages, so agents have no page views.",
    ),
  ).toBeInTheDocument();
  const reads = screen
    .getByRole("heading", { name: "Reads per day" })
    .closest("figure")!;
  // Agents' reads are no pages refreshing what they show.
  expect(within(reads).queryByText(/refreshing/)).not.toBeInTheDocument();
  const rows = within(within(reads).getByRole("table")).getAllByRole("row");
  expect(rows.map((row) => row.textContent)).toEqual([
    "DayAgents",
    `${formatDay("2026-11-05")}2`,
    `${formatDay("2026-11-06")}0`,
    `${formatDay("2026-11-07")}20`,
    `${formatDay("2026-11-08")}0`,
  ]);
  const summaries = within(
    screen.getByRole("region", { name: "Features by area" }),
  )
    .getAllByText(/^\/[a-z-]+$/)
    .map((name) => name.closest("summary")!);
  expect(summaries.map((summary) => summary.textContent)).toEqual([
    "/work-items1 of 1 used22 by agents",
    "/tickets1 of 2 used3 by agents",
    "/search0 of 1 usedNo calls",
  ]);
  await user.click(summaries[1]);
  const area = within(summaries[1].closest("details")!);
  expect(area.getAllByRole("row").map((row) => row.textContent)).toEqual([
    "CallWho may make itAgentsFailedLast used",
    `POST /ticketsagent:ticket30${formatDay("2026-11-07")}`,
    "GET /tickets/{ref}agent:readNot usedNever",
  ]);
  await user.click(screen.getByRole("button", { name: "7 days" }));
  expect(
    await screen.findByText("Your agents used nothing in these 7 days."),
  ).toBeInTheDocument();
  expect(asked.at(-1)).toBe("/usage?days=7&actor=agent");
});

it("draws a window over 90 days by the week", async () => {
  serve(() => ({
    ...report,
    recordedSince: "2026-10-26",
    window: { days: 365, from: "2025-11-09", to: "2026-11-08" },
  }));
  show();
  const chart = (
    await screen.findByRole("heading", { name: "Page views per week" })
  ).closest("figure")!;
  const rows = within(within(chart).getByRole("table")).getAllByRole("row");
  expect(rows.map((row) => row.textContent)).toEqual([
    "WeekPage views",
    `${formatDay("2026-10-26")} to ${formatDay("2026-11-01")}0`,
    `${formatDay("2026-11-02")} to ${formatDay("2026-11-08")}4`,
  ]);
});

it("says what to do when the counts cannot be read", async () => {
  serve(() => "unreadable");
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "holds usage format 99",
  );
  expect(screen.queryByText("Loading usage…")).not.toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.queryByText("Page views", { selector: "dt" }),
    ).not.toBeInTheDocument(),
  );
});
