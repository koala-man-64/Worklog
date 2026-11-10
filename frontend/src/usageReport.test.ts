import { describe, expect, it } from "vitest";
import type { UsageDay, UsageReport } from "./apiTypes";
import {
  areaOf,
  areas,
  buckets,
  pageRows,
  scopeLabel,
  totals,
} from "./usageReport";

const report = (fields: Partial<UsageReport>): UsageReport => ({
  file: "C:/Worklog/usage.sqlite3",
  version: "1.0.0",
  recordedSince: "2026-11-02",
  window: { days: 7, from: "2026-11-02", to: "2026-11-08" },
  agents: true,
  daily: [],
  pages: { used: [], unused: [] },
  calls: { used: [], unused: [] },
  ...fields,
});
const day = (date: string, fields: Partial<UsageDay> = {}): UsageDay => ({
  day: date,
  pageViews: 0,
  reads: { person: 0, agent: 0 },
  changes: { person: 0, agent: 0 },
  failed: 0,
  ...fields,
});

describe("totals", () => {
  it("splits calls into reads and changes by who made them, and counts only the calls this version serves", () => {
    expect(
      totals(
        report({
          pages: {
            used: [
              { page: "today", views: 5, lastUsed: "2026-11-08" },
              { page: "work", views: 2, lastUsed: "2026-11-07" },
            ],
            unused: [{ page: "people", lastUsed: null }],
          },
          calls: {
            used: [
              {
                call: "GET /work-items",
                scope: "agent:read",
                person: 10,
                agent: 4,
                failed: 1,
                lastUsed: "2026-11-08",
              },
              {
                call: "POST /work-items",
                scope: "human",
                person: 3,
                agent: 0,
                failed: 2,
                lastUsed: "2026-11-08",
              },
              {
                call: "PATCH /tickets/{ref}",
                scope: "agent:ticket",
                person: 0,
                agent: 6,
                failed: 0,
                lastUsed: "2026-11-06",
              },
              // An older version served it; this one doesn't.
              {
                call: "GET /retired",
                scope: null,
                person: 1,
                agent: 0,
                failed: 0,
                lastUsed: "2026-11-03",
              },
            ],
            unused: [
              {
                call: "DELETE /notes/{identity}",
                scope: "human",
                lastUsed: null,
              },
              {
                call: "GET /notes/{identity}",
                scope: "agent:read",
                lastUsed: null,
              },
            ],
          },
        }),
      ),
    ).toEqual({
      pageViews: 7,
      pagesOpened: 2,
      pages: 3,
      reads: { person: 11, agent: 4 },
      changes: { person: 3, agent: 6 },
      calls: 24,
      failed: 3,
      featuresUsed: 3,
      features: 5,
    });
  });
});

describe("buckets", () => {
  it("gives each day from the later of the window's start and the day counting began a column, zeros where nothing was used", () => {
    const columns = buckets(
      report({
        recordedSince: "2026-11-05",
        daily: [
          day("2026-11-05", { pageViews: 2, failed: 1 }),
          day("2026-11-07", { changes: { person: 1, agent: 3 } }),
        ],
      }),
    );
    expect(columns.map((column) => column.first)).toEqual([
      "2026-11-05",
      "2026-11-06",
      "2026-11-07",
      "2026-11-08",
    ]);
    expect(columns.every((column) => column.first === column.last)).toBe(true);
    expect(columns.map((column) => column.pageViews)).toEqual([2, 0, 0, 0]);
    expect(columns[0].failed).toBe(1);
    expect(columns[2].changes).toEqual({ person: 1, agent: 3 });
    expect(columns[3].reads).toEqual({ person: 0, agent: 0 });
  });

  it("covers the whole window when counting began before it, or has not begun", () => {
    const window = { days: 7, from: "2026-11-02", to: "2026-11-08" };
    expect(
      buckets(report({ recordedSince: "2026-09-01", window })),
    ).toHaveLength(7);
    expect(buckets(report({ recordedSince: null, window }))).toHaveLength(7);
  });

  it("sums a window over 90 days by week, the last ending on its last day and the first cut short where counting began", () => {
    const weeks = buckets(
      report({
        recordedSince: "2026-10-20",
        window: { days: 365, from: "2025-11-09", to: "2026-11-08" },
        daily: [
          day("2026-10-20", { pageViews: 1 }),
          day("2026-10-26", { pageViews: 2 }),
          day("2026-11-01", { pageViews: 4, reads: { person: 3, agent: 1 } }),
          day("2026-11-08", { pageViews: 8 }),
        ],
      }),
    );
    expect(
      weeks.map(({ first, last, pageViews }) => [first, last, pageViews]),
    ).toEqual([
      ["2026-10-20", "2026-10-25", 1],
      ["2026-10-26", "2026-11-01", 6],
      ["2026-11-02", "2026-11-08", 8],
    ]);
    expect(weeks[1].reads).toEqual({ person: 3, agent: 1 });
  });

  it("keeps a day after the window's last, as when the timezone moved east", () => {
    const columns = buckets(
      report({
        recordedSince: "2026-11-08",
        daily: [day("2026-11-08"), day("2026-11-09", { pageViews: 1 })],
      }),
    );
    expect(columns.map((column) => [column.first, column.pageViews])).toEqual([
      ["2026-11-08", 0],
      ["2026-11-09", 1],
    ]);
  });
});

it("lists the pages opened, the most viewed first, then the rest in sidebar order", () => {
  const rows = pageRows(
    report({
      pages: {
        used: [
          { page: "work", views: 9, lastUsed: "2026-11-08" },
          { page: "agent-guide", views: 1, lastUsed: "2026-11-03" },
        ],
        // The server lists them by name.
        unused: [
          { page: "agents", lastUsed: null },
          { page: "settings", lastUsed: "2026-10-01" },
          { page: "today", lastUsed: null },
        ],
      },
    }),
  );
  expect(rows.map((row) => [row.label, row.views, row.lastUsed])).toEqual([
    ["Work", 9, "2026-11-08"],
    ["Guide for agents", 1, "2026-11-03"],
    ["Today", 0, null],
    ["Agents", 0, null],
    ["Settings", 0, "2026-10-01"],
  ]);
});

it("groups the calls by area, the most called first, each with the calls made, then those nothing made", () => {
  const found = areas(
    report({
      calls: {
        used: [
          {
            call: "GET /work-items",
            scope: "agent:read",
            person: 5,
            agent: 5,
            failed: 0,
            lastUsed: "2026-11-08",
          },
          {
            call: "POST /work-items/{identity}/archive",
            scope: "human",
            person: 1,
            agent: 0,
            failed: 1,
            lastUsed: "2026-11-07",
          },
          {
            call: "GET /tickets/{ref}",
            scope: "agent:read",
            person: 0,
            agent: 20,
            failed: 0,
            lastUsed: "2026-11-08",
          },
          {
            call: "GET /work-items/retired",
            scope: null,
            person: 2,
            agent: 0,
            failed: 0,
            lastUsed: "2026-11-02",
          },
        ],
        unused: [
          { call: "POST /work-items", scope: "human", lastUsed: "2026-10-01" },
          {
            call: "GET /notes/{identity}",
            scope: "agent:read",
            lastUsed: null,
          },
          { call: "DELETE /notes/{identity}", scope: "human", lastUsed: null },
        ],
      },
    }),
  );
  // A call this version no longer serves counts its uses, but not as a feature served.
  expect(
    found.map(({ name, used, served, person, agent, failed }) => [
      name,
      used,
      served,
      person,
      agent,
      failed,
    ]),
  ).toEqual([
    ["/tickets", 1, 1, 0, 20, 0],
    ["/work-items", 2, 3, 8, 5, 1],
    ["/notes", 0, 2, 0, 0, 0],
  ]);
  expect(found[1].features.map((feature) => feature.call)).toEqual([
    "GET /work-items",
    "GET /work-items/retired",
    "POST /work-items/{identity}/archive",
    "POST /work-items",
  ]);
  expect(found[1].features.at(-1)).toMatchObject({
    used: false,
    person: 0,
    agent: 0,
    failed: 0,
    lastUsed: "2026-10-01",
  });
  expect(found[2].features.map((feature) => feature.call)).toEqual([
    "DELETE /notes/{identity}",
    "GET /notes/{identity}",
  ]);
});

it("names a call's area by its first path segment", () => {
  expect(areaOf("GET /work-items/{identity}/proposals")).toBe("/work-items");
  expect(areaOf("POST /session/bootstrap")).toBe("/session");
  expect(areaOf("GET /openapi.json")).toBe("/openapi.json");
});

it("words who may make a call", () => {
  expect(scopeLabel("human")).toBe("You only");
  expect(scopeLabel("public")).toBe("Anyone");
  expect(scopeLabel("agent:read")).toBe("agent:read");
  expect(scopeLabel(null)).toBe("No longer served");
});
