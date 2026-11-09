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

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  setDataContext("usage-test");
});

/** Answers GET /usage with `answer(days)`; returns the paths asked for, query included. */
function serve(
  answer: (days: string) => UsageReport | Promise<UsageReport> | "unreadable",
) {
  const asked: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://worklog.test");
    asked.push(url.pathname.replace("/api/v1", "") + url.search);
    const body = await answer(url.searchParams.get("days") ?? "");
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
