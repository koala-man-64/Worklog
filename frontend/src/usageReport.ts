/**
 * What the Usage page draws from a usage report (GET /usage): its totals, its days as chart
 * columns, its pages as ranked rows and its calls grouped by area. Nothing here asks the server.
 */
import type { UsageReport } from "./apiTypes";
import { destinations } from "./navigation";

export type Actors = { person: number; agent: number };

/** A read is a GET; anything else changes something. */
const isRead = (call: string) => call.startsWith("GET ");
const sum = (values: number[]) =>
  values.reduce((total, value) => total + value, 0);

export type Totals = {
  pageViews: number;
  pagesOpened: number;
  pages: number;
  reads: Actors;
  changes: Actors;
  calls: number;
  failed: number;
  /** Calls this version serves that something made in the window. */
  featuresUsed: number;
  /** Calls this version serves. */
  features: number;
};

export function totals(report: UsageReport): Totals {
  const reads = { person: 0, agent: 0 };
  const changes = { person: 0, agent: 0 };
  let failed = 0;
  for (const call of report.calls.used) {
    const by = isRead(call.call) ? reads : changes;
    by.person += call.person;
    by.agent += call.agent;
    failed += call.failed;
  }
  // A call an older version served, which this one no longer does, has no scope.
  const featuresUsed = report.calls.used.filter(
    (call) => call.scope !== null,
  ).length;
  return {
    pageViews: sum(report.pages.used.map((page) => page.views)),
    pagesOpened: report.pages.used.length,
    pages: report.pages.used.length + report.pages.unused.length,
    reads,
    changes,
    calls: reads.person + reads.agent + changes.person + changes.agent,
    failed,
    featuresUsed,
    features: featuresUsed + report.calls.unused.length,
  };
}

const DAY_MS = 86_400_000;
const addDays = (day: string, days: number) =>
  new Date(Date.parse(day + "T00:00:00Z") + days * DAY_MS)
    .toISOString()
    .slice(0, 10);

/** One chart column: the sums of a day, or of a week from `first` to `last`. */
export type Bucket = {
  first: string;
  last: string;
  pageViews: number;
  reads: Actors;
  changes: Actors;
  failed: number;
};

/** Windows longer than this are drawn by the week, so each column stays wide enough to read. */
export const DAILY_LIMIT = 90;

/**
 * The window's days as chart columns, zeros where nothing was used, from the later of its first day
 * and the day counting began, to its last day. A window over DAILY_LIMIT days is summed by week, the
 * last week ending on its last day and the first cut short where the days begin.
 */
export function buckets(report: UsageReport): Bucket[] {
  const { from, to, days } = report.window;
  const latest = report.daily.at(-1)?.day;
  const last = latest && latest > to ? latest : to;
  const began = report.recordedSince;
  const first = [began && began > from ? began : from, last].sort()[0];
  const byDay = new Map(report.daily.map((day) => [day.day, day]));
  const width = days > DAILY_LIMIT ? 7 : 1;
  const found: Bucket[] = [];
  for (let end = last; end >= first; end = addDays(end, -width)) {
    const start = [addDays(end, 1 - width), first].sort().at(-1)!;
    const bucket: Bucket = {
      first: start,
      last: end,
      pageViews: 0,
      reads: { person: 0, agent: 0 },
      changes: { person: 0, agent: 0 },
      failed: 0,
    };
    for (let day = start; day <= end; day = addDays(day, 1)) {
      const counted = byDay.get(day);
      if (!counted) continue;
      bucket.pageViews += counted.pageViews;
      bucket.reads.person += counted.reads.person;
      bucket.reads.agent += counted.reads.agent;
      bucket.changes.person += counted.changes.person;
      bucket.changes.agent += counted.changes.agent;
      bucket.failed += counted.failed;
    }
    found.unshift(bucket);
  }
  return found;
}

export type PageRow = {
  page: string;
  label: string;
  views: number;
  lastUsed: string | null;
};

/** Each counted page, the Guide's view for agents last. */
const counted = [...destinations.map(({ route }) => route), "agent-guide"];
const labels = new Map<string, string>([
  ...destinations.map(({ route, label }) => [route, label] as const),
  ["agent-guide", "Guide for agents"],
]);
export const pageLabel = (page: string) => labels.get(page) ?? page;

/** Every page: those opened in the window, the most viewed first, then the rest in sidebar order. */
export function pageRows(report: UsageReport): PageRow[] {
  const place = (page: string) => {
    const index = counted.indexOf(page);
    return index < 0 ? counted.length : index;
  };
  return [
    ...report.pages.used.map(({ page, views, lastUsed }) => ({
      page,
      label: pageLabel(page),
      views,
      lastUsed,
    })),
    ...[...report.pages.unused]
      .sort((a, b) => place(a.page) - place(b.page))
      .map(({ page, lastUsed }) => ({
        page,
        label: pageLabel(page),
        views: 0,
        lastUsed,
      })),
  ];
}

export type Feature = Actors & {
  call: string;
  /** Who may make the call, from the route policy; null once this version no longer serves it. */
  scope: string | null;
  failed: number;
  lastUsed: string | null;
  used: boolean;
};

export type Area = Actors & {
  /** The calls' first path segment, such as `/work-items`. */
  name: string;
  features: Feature[];
  /** Calls this version serves here, and how many of them something made. */
  served: number;
  used: number;
  failed: number;
};

/** The area a call belongs to: the first segment of its path, such as `/work-items`. */
export const areaOf = (call: string) =>
  "/" + (call.split(" ")[1] ?? "").split("/")[1];

/**
 * The calls by area: the areas most called first and those nothing called last, by name; in each,
 * the calls made, the most made first, then those nothing made, by name.
 */
export function areas(report: UsageReport): Area[] {
  const found = new Map<string, Area>();
  const features: Feature[] = [
    ...report.calls.used.map((call) => ({ ...call, used: true })),
    ...report.calls.unused.map((call) => ({
      ...call,
      person: 0,
      agent: 0,
      failed: 0,
      used: false,
    })),
  ];
  for (const feature of features) {
    const name = areaOf(feature.call);
    const area = found.get(name) ?? {
      name,
      features: [],
      served: 0,
      used: 0,
      person: 0,
      agent: 0,
      failed: 0,
    };
    area.features.push(feature);
    area.person += feature.person;
    area.agent += feature.agent;
    area.failed += feature.failed;
    if (feature.scope !== null) {
      area.served += 1;
      if (feature.used) area.used += 1;
    }
    found.set(name, area);
  }
  const made = (item: Actors) => item.person + item.agent;
  const byName = (a: { name?: string; call?: string }, b: typeof a) =>
    (a.name ?? a.call ?? "").localeCompare(b.name ?? b.call ?? "");
  for (const area of found.values())
    area.features.sort(
      (a, b) =>
        Number(b.used) - Number(a.used) || made(b) - made(a) || byName(a, b),
    );
  return [...found.values()].sort((a, b) => made(b) - made(a) || byName(a, b));
}

/** Who may make a call, as the page words it: the person alone, anyone, or an agent scope. */
export function scopeLabel(scope: string | null) {
  return scope === null
    ? "No longer served"
    : scope === "human"
      ? "You only"
      : scope === "public"
        ? "Anyone"
        : scope;
}
