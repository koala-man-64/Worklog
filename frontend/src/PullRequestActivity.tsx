import type {
  ActivityDay,
  ActivityPullRequest,
  ActivityRepository,
  PullRequestActivity,
} from "./apiTypes";
import { ago } from "./Chats";
import { ColumnChart, Meter, type Column } from "./ui/charts";
import { formatCount, formatDay } from "./ui/format";
import { DAILY_LIMIT } from "./usageReport";

/** The windows the activity offers, in days, as GET /pull-requests/activity takes them. */
export const ACTIVITY_WINDOWS = [7, 30, 90, 365];

/** How often the page reads the count again: often while a count runs, else each minute. */
export const activityRefresh = (data: PullRequestActivity | undefined) =>
  data?.scanning ? 15_000 : 60_000;

type Sum = {
  commits: number;
  additions: number;
  deletions: number;
  merged: number;
};
type Bucket = Sum & { first: string; last: string };

function addDays(day: string, count: number) {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date + count))
    .toISOString()
    .slice(0, 10);
}

/**
 * The window's days as chart columns; a window over DAILY_LIMIT days summed by week, the last week
 * ending on its last day and the first cut short where the window begins.
 */
export function activityBuckets(daily: ActivityDay[], days: number): Bucket[] {
  if (!daily.length) return [];
  const width = days > DAILY_LIMIT ? 7 : 1;
  const first = daily[0].day;
  const byDay = new Map(daily.map((day) => [day.day, day]));
  const found: Bucket[] = [];
  for (let end = daily.at(-1)!.day; end >= first; end = addDays(end, -width)) {
    const start = [addDays(end, 1 - width), first].sort().at(-1)!;
    const bucket: Bucket = {
      first: start,
      last: end,
      commits: 0,
      additions: 0,
      deletions: 0,
      merged: 0,
    };
    for (let day = start; day <= end; day = addDays(day, 1)) {
      const counted = byDay.get(day);
      if (!counted) continue;
      bucket.commits += counted.commits;
      bucket.additions += counted.additions;
      bucket.deletions += counted.deletions;
      bucket.merged += counted.merged;
    }
    found.unshift(bucket);
  }
  return found;
}

/** Why a repository has no counts, or where its counts come from. */
export function repositoryState(row: ActivityRepository, now = Date.now()) {
  switch (row.state) {
    case "ready":
      return `origin/${row.branch}, ${row.fetchedAt ? `fetched ${ago(row.fetchedAt, now)}` : "never fetched"}`;
    case "pending":
      return "Waiting to be counted";
    case "scanning":
      return "Counting now";
    case "no_checkout":
      return "No checkout of it has been reported";
    case "checkout_mismatch":
      return "Its reported checkout is of another remote";
    case "no_default_branch":
      return "Its checkout has no origin/main or origin/master";
    case "timed_out":
      return "git took too long to count it";
    case "failed":
      return `Counting failed${row.detail ? `: ${row.detail}` : ""}`;
  }
}

/** A registered pull request's merge, as its row on the list says it. */
export function mergeText(merge: ActivityPullRequest | undefined) {
  if (!merge) return null;
  if (!merge.repositoryId) return "Not in a registered repository";
  if (!merge.mergedAt) return "No merge found on main";
  const merged = `merged ${formatDay(merge.mergedAt.slice(0, 10))}`;
  if (merge.additions === null || merge.deletions === null)
    return `Size not counted yet · ${merged}`;
  const files =
    merge.files === 1 ? "1 file" : `${formatCount(merge.files ?? 0)} files`;
  return `+${formatCount(merge.additions)} −${formatCount(merge.deletions)} · ${files} · ${merged}`;
}

/**
 * What reached each registered repository's default branch in a window of days: commits, lines added
 * and removed, and pull requests merged, read from the repositories' local checkouts by Worklog's
 * background count (GET /pull-requests/activity), which never fetches.
 */
export function Activity({
  report,
  days,
  onDays,
  stale,
}: {
  report: PullRequestActivity | undefined;
  days: number;
  onDays: (days: number) => void;
  stale: boolean;
}) {
  return (
    <section aria-labelledby="pull-request-activity">
      <h2 id="pull-request-activity">Activity</h2>
      <div className="toolbar">
        <div className="window-toggles" role="group" aria-label="Days shown">
          {ACTIVITY_WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              aria-pressed={days === window}
              onClick={() => onDays(window)}
            >
              {window} days
            </button>
          ))}
        </div>
        {report?.scanning && (
          <small role="status">
            Counting {report.scanned} of {report.total} repositories…
          </small>
        )}
      </div>
      {report ? (
        <ActivityView report={report} stale={stale} />
      ) : (
        <p>Loading activity…</p>
      )}
    </section>
  );
}

function ActivityView({
  report,
  stale,
}: {
  report: PullRequestActivity;
  stale: boolean;
}) {
  const weekly = report.window.days > DAILY_LIMIT;
  const per = weekly ? "week" : "day";
  const unit = weekly ? "Week" : "Day";
  const buckets = activityBuckets(report.daily, report.window.days);
  const columns = (values: (bucket: Bucket) => number[]): Column[] =>
    buckets.map((bucket) => ({
      key: bucket.first,
      label:
        bucket.first === bucket.last
          ? formatDay(bucket.first)
          : `${formatDay(bucket.first)} to ${formatDay(bucket.last)}`,
      values: values(bucket),
    }));
  const sum = report.daily.reduce<Sum>(
    (total, day) => ({
      commits: total.commits + day.commits,
      additions: total.additions + day.additions,
      deletions: total.deletions + day.deletions,
      merged: total.merged + day.merged,
    }),
    { commits: 0, additions: 0, deletions: 0, merged: 0 },
  );
  const uncounted = report.repositories.filter(
    (row) => row.state !== "ready",
  ).length;
  const active = report.repositories.filter(
    (row) => row.commits || row.merged,
  ).length;
  const most = Math.max(0, ...report.repositories.map((row) => row.commits));
  const span = `in ${report.window.days} days`;
  return (
    <div className={"usage" + (stale ? " stale" : "")} aria-busy={stale}>
      <dl className="usage-tiles">
        <div className="usage-tile">
          <dt>Commits</dt>
          <dd>{formatCount(sum.commits)}</dd>
          <small>
            {span}, in {active} of {report.repositories.length} repositories
          </small>
        </div>
        <div className="usage-tile">
          <dt>Lines added</dt>
          <dd>+{formatCount(sum.additions)}</dd>
          <small>{span}</small>
        </div>
        <div className="usage-tile">
          <dt>Lines removed</dt>
          <dd>−{formatCount(sum.deletions)}</dd>
          <small>{span}</small>
        </div>
        <div className="usage-tile">
          <dt>Pull requests merged</dt>
          <dd>{formatCount(sum.merged)}</dd>
          <small>{span}</small>
        </div>
      </dl>
      <div className="usage-charts">
        <ColumnChart
          title={`Commits per ${per}`}
          unit={unit}
          series={[{ label: "Commits", tone: "you" }]}
          columns={columns((bucket) => [bucket.commits])}
        />
        <ColumnChart
          title={`Lines added and removed per ${per}`}
          unit={unit}
          mirror
          series={[
            { label: "Added", tone: "added" },
            { label: "Removed", tone: "removed" },
          ]}
          columns={columns((bucket) => [bucket.additions, bucket.deletions])}
        />
      </div>
      <p className="chart-note">
        Commits that reached each repository's main branch, merge commits left
        out, as of its checkout's last fetch: Worklog reads the checkouts your
        agents report and never fetches.
        {uncounted > 0 &&
          ` ${uncounted} of ${report.repositories.length} repositories ${uncounted === 1 ? "isn't" : "aren't"} counted; the table says why.`}
      </p>
      <div className="usage-features pull-request-repositories">
        <table>
          <caption className="visually-hidden">
            Repositories, most commits first
          </caption>
          <thead>
            <tr>
              <th scope="col">Repository</th>
              <th scope="col" className="number">
                Commits
              </th>
              <th scope="col" className="number">
                Added
              </th>
              <th scope="col" className="number">
                Removed
              </th>
              <th scope="col" className="number">
                Merged
              </th>
              <th scope="col" className="number">
                Registered
              </th>
              <th scope="col">Counted from</th>
            </tr>
          </thead>
          <tbody>
            {report.repositories.map((row) => (
              <tr
                key={row.id}
                className={row.state === "ready" ? undefined : "unused"}
              >
                <th scope="row">
                  <a href={`#repositories/${row.id}`}>{row.name}</a>
                </th>
                <td className="number">
                  <span className="pull-request-commits">
                    <Meter value={row.commits} max={most} thin />
                    {formatCount(row.commits)}
                  </span>
                </td>
                <td className="number">+{formatCount(row.additions)}</td>
                <td className="number">−{formatCount(row.deletions)}</td>
                <td className="number">{formatCount(row.merged)}</td>
                <td className="number">{formatCount(row.registered)}</td>
                <td>{repositoryState(row)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
