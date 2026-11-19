import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { PullRequests } from "./PullRequests";
import {
  activityBuckets,
  activityRefresh,
  mergeText,
} from "./PullRequestActivity";
import { ColumnChart } from "./ui/charts";
import type { ActivityDay, PullRequest, PullRequestActivity } from "./apiTypes";
import { formatDay } from "./ui/format";

const days = (
  from: string,
  count: number,
  busy: Record<string, Partial<ActivityDay>> = {},
) =>
  Array.from({ length: count }, (_, at) => {
    const day = new Date(`${from}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() + at);
    const key = day.toISOString().slice(0, 10);
    return {
      day: key,
      commits: 0,
      additions: 0,
      deletions: 0,
      merged: 0,
      ...busy[key],
    };
  });

const activity: PullRequestActivity = {
  window: {
    days: 7,
    from: "2026-11-13",
    to: "2026-11-19",
    timezone: "America/Chicago",
  },
  daily: days("2026-11-13", 7, {
    "2026-11-14": { commits: 3, additions: 120, deletions: 30, merged: 1 },
    "2026-11-18": { commits: 2, additions: 10, deletions: 400, merged: 2 },
  }),
  repositories: [
    {
      id: "r1",
      name: "Parser",
      branch: "main",
      state: "ready",
      detail: "",
      fetchedAt: new Date(Date.now() - 3 * 3_600_000).toISOString(),
      scannedAt: "2026-11-19T17:00:00Z",
      commits: 5,
      additions: 130,
      deletions: 430,
      merged: 3,
      registered: 2,
    },
    {
      id: "r2",
      name: "Notes",
      branch: "",
      state: "no_checkout",
      detail: "No agent has reported a checkout",
      fetchedAt: null,
      scannedAt: "2026-11-19T17:00:00Z",
      commits: 0,
      additions: 0,
      deletions: 0,
      merged: 0,
      registered: 0,
    },
    {
      id: "r3",
      name: "Slow",
      branch: "",
      state: "failed",
      detail: "fatal: bad object",
      fetchedAt: null,
      scannedAt: "2026-11-19T17:00:00Z",
      commits: 0,
      additions: 0,
      deletions: 0,
      merged: 0,
      registered: 1,
    },
  ],
  pullRequests: [
    {
      id: "p1",
      repositoryId: "r1",
      number: 5,
      mergedAt: "2026-11-14T18:00:00Z",
      sha: "a",
      additions: 120,
      deletions: 34,
      files: 6,
    },
    {
      id: "p2",
      repositoryId: "r1",
      number: 9,
      mergedAt: null,
      sha: null,
      additions: null,
      deletions: null,
      files: null,
    },
    {
      id: "p3",
      repositoryId: "r3",
      number: 7,
      mergedAt: "2026-11-18T18:00:00Z",
      sha: "b",
      additions: null,
      deletions: null,
      files: null,
    },
  ],
  scanning: false,
  scanned: 3,
  total: 3,
};

const pull = (id: string, title: string): PullRequest => ({
  id,
  ownerType: "item",
  ownerId: "w1",
  kind: "pr",
  url: `https://github.com/example/parser/pull/${id}`,
  reference: "",
  title,
  description: "",
  chatId: null,
  submittedBy: "Morgan",
  archived: false,
  revision: 1,
  createdAt: "2026-11-14T00:00:00Z",
  updatedAt: "2026-11-14T00:00:00Z",
  chatTitle: null,
  ownerTitle: "Parser work",
});

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  setDataContext("pull-request-activity-test");
});

/** Answers the registry and its activity; returns the paths asked for, query included. */
function serve(answer: (days: string) => PullRequestActivity = () => activity) {
  const asked: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://worklog.test");
    const path = url.pathname.replace("/api/v1", "");
    asked.push(path + url.search);
    const body =
      path === "/pull-requests/activity"
        ? answer(url.searchParams.get("days") ?? "")
        : {
            items: [
              pull("p1", "Squash the parser"),
              pull("p2", "Open work"),
              pull("p3", "Merged lexer"),
            ],
            total: 3,
            limit: 50,
            offset: 0,
          };
    return new Response(JSON.stringify(body), {
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
      <PullRequests />
    </QueryClientProvider>,
  );
}

it("shows the window's totals, commits and lines by day, and every repository with its state", async () => {
  const asked = serve();
  show();
  const tile = await screen.findByText("Commits", { selector: "dt" });
  expect(tile.nextElementSibling).toHaveTextContent("5");
  expect(
    screen.getByText("Lines added", { selector: "dt" }).nextElementSibling,
  ).toHaveTextContent("+130");
  expect(
    screen.getByText("Lines removed", { selector: "dt" }).nextElementSibling,
  ).toHaveTextContent("−430");
  expect(
    screen.getByText("Pull requests merged", { selector: "dt" })
      .nextElementSibling,
  ).toHaveTextContent("3");
  expect(screen.getByText(/in 1 of 3 repositories/)).toBeInTheDocument();
  expect(asked).toContain("/pull-requests/activity?days=30");

  const commits = screen.getByRole("img", { name: /^Commits per day/ });
  expect(commits).toHaveAccessibleName(
    `Commits per day, ${formatDay("2026-11-13")} to ${formatDay("2026-11-19")}: 5 in all; the most, 3, ${formatDay("2026-11-14")}`,
  );
  const lines = screen.getByRole("img", {
    name: /^Lines added and removed per day/,
  });
  expect(lines).toHaveAccessibleName(
    `Lines added and removed per day, ${formatDay("2026-11-13")} to ${formatDay("2026-11-19")}: added 130, removed 430`,
  );
  // The mirrored chart: added up from the baseline, removed down, on one scale (200 up, 500 down).
  const figure = lines.closest("figure")!;
  expect(
    [...figure.querySelectorAll(".chart-ticks.placed span")].map(
      (tick) => tick.textContent,
    ),
  ).toEqual(["200", "0", "500"]);
  const up = figure.querySelector<HTMLElement>(".chart-bar.up.added")!;
  const down = figure.querySelector<HTMLElement>(".chart-bar.down.removed")!;
  expect(up.style.height).toBe(`${(120 / 700) * 100}%`);
  expect(figure.querySelectorAll(".chart-bar.down.removed")).toHaveLength(2);
  expect(down.style.height).toBe(`${(30 / 700) * 100}%`);
  const table = within(figure).getByRole("table");
  expect(
    within(table).getByRole("row", {
      name: `${formatDay("2026-11-18")} 10 400`,
    }),
  ).toBeInTheDocument();

  const repositories = screen.getByRole("table", {
    name: "Repositories, most commits first",
  });
  const rows = within(repositories).getAllByRole("row").slice(1);
  expect(
    rows.map((row) => within(row).getByRole("rowheader").textContent),
  ).toEqual(["Parser", "Notes", "Slow"]);
  expect(rows[0]).toHaveTextContent("origin/main, fetched 3 hours ago");
  expect(rows[1]).toHaveTextContent("No checkout of it has been reported");
  expect(rows[2]).toHaveTextContent("Counting failed: fatal: bad object");
  expect(within(rows[0]).getByRole("link", { name: "Parser" })).toHaveAttribute(
    "href",
    "#repositories/r1",
  );
  expect(
    screen.getByText(/2 of 3 repositories aren't counted/),
  ).toBeInTheDocument();
});

it("says each registered pull request's merge on its row", async () => {
  serve();
  show();
  const squash = await screen.findByRole("article", {
    name: "Pull request Squash the parser",
  });
  await waitFor(() =>
    expect(squash).toHaveTextContent(
      `+120 −34 · 6 files · merged ${formatDay("2026-11-14")}`,
    ),
  );
  expect(
    screen.getByRole("article", { name: "Pull request Open work" }),
  ).toHaveTextContent("No merge found on main");
  expect(
    screen.getByRole("article", { name: "Pull request Merged lexer" }),
  ).toHaveTextContent(
    `Size not counted yet · merged ${formatDay("2026-11-18")}`,
  );
});

it("reads another window and draws a year by the week", async () => {
  const year: PullRequestActivity = {
    ...activity,
    window: {
      days: 365,
      from: "2025-11-20",
      to: "2026-11-19",
      timezone: "America/Chicago",
    },
    daily: days("2025-11-20", 365, {
      "2026-11-18": { commits: 4, additions: 1, deletions: 1, merged: 0 },
    }),
  };
  const asked = serve((window) => (window === "365" ? year : activity));
  show();
  await screen.findByRole("img", { name: /^Commits per day/ });
  await userEvent.click(screen.getByRole("button", { name: "365 days" }));
  const weekly = await screen.findByRole("img", { name: /^Commits per week/ });
  expect(asked).toContain("/pull-requests/activity?days=365");
  expect(screen.getByRole("button", { name: "365 days" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(within(weekly.closest("figure")!).getAllByRole("row")).toHaveLength(
    1 + 53,
  );
});

it("says how far a count has got while one runs", async () => {
  serve(() => ({ ...activity, scanning: true, scanned: 1, total: 3 }));
  show();
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Counting 1 of 3 repositories…",
  );
});

it("reads again often while a count runs, else each minute", () => {
  expect(activityRefresh(undefined)).toBe(60_000);
  expect(activityRefresh(activity)).toBe(60_000);
  expect(activityRefresh({ ...activity, scanning: true })).toBe(15_000);
});

it("sums a long window by week, the first week cut short where the window begins", () => {
  const ten = days("2026-11-10", 10, {
    "2026-11-10": { commits: 1 },
    "2026-11-11": { commits: 2 },
    "2026-11-19": { commits: 4 },
  });
  expect(activityBuckets(ten, 10).map((bucket) => bucket.commits)).toEqual(
    ten.map((day) => day.commits),
  );
  const weeks = activityBuckets(ten, 365);
  expect(
    weeks.map((bucket) => [bucket.first, bucket.last, bucket.commits]),
  ).toEqual([
    ["2026-11-10", "2026-11-12", 3],
    ["2026-11-13", "2026-11-19", 4],
  ]);
  expect(activityBuckets([], 30)).toEqual([]);
});

it("words a merge that isn't known yet, or a pull request outside the registered repositories", () => {
  expect(mergeText(undefined)).toBeNull();
  expect(
    mergeText({
      ...activity.pullRequests[1],
      repositoryId: null,
      number: null,
    }),
  ).toBe("Not in a registered repository");
  expect(mergeText({ ...activity.pullRequests[0], files: 1 })).toContain(
    "· 1 file ·",
  );
});

it("keeps a stacked chart as it was", () => {
  render(
    <ColumnChart
      title="Uses"
      unit="Day"
      series={[
        { label: "You", tone: "you" },
        { label: "Agents", tone: "agents" },
      ]}
      columns={[{ key: "a", label: "A", values: [3, 1] }]}
    />,
  );
  expect(document.querySelector(".chart-ticks.placed")).toBeNull();
  expect(document.querySelectorAll(".chart-segment")).toHaveLength(2);
  expect(
    [...document.querySelectorAll(".chart-ticks span")].map(
      (tick) => tick.textContent,
    ),
  ).toEqual(["5", "0"]);
});
