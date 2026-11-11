import { useState } from "react";
import { useData } from "./hooks";
import type { UsageReport } from "./apiTypes";
import { ColumnChart, Meter, type Column, type Tone } from "./ui/charts";
import { formatCount, formatDay } from "./ui/format";
import { Empty, ErrorMessage } from "./ui/Messages";
import {
  areas,
  buckets,
  DAILY_LIMIT,
  pageRows,
  scopeLabel,
  totals,
  type Actors,
  type Area,
  type Bucket,
  type PageRow,
} from "./usageReport";

/** The windows the page offers, in days; 30 is `worklog usage`'s own. */
const WINDOWS = [7, 30, 90, 365];

type Actor = NonNullable<UsageReport["actor"]>;
/** Whose uses the page counts: everyone's, or one actor's alone, as GET /usage?actor= takes it. */
const WHOSE: { label: string; actor: Actor | null }[] = [
  { label: "You and agents", actor: null },
  { label: "You", actor: "person" },
  { label: "Agents", actor: "agent" },
];

/** An actor as the page names it: its series and column, who its uses are "by", and its colour. */
type Shown = { actor: Actor; label: string; by: string; tone: Tone };
const YOU: Shown = { actor: "person", label: "You", by: "you", tone: "you" };
const AGENTS: Shown = {
  actor: "agent",
  label: "Agents",
  by: "agents",
  tone: "agents",
};
/** The actors a report counts: the one it names, or both. */
const shownIn = (report: UsageReport) =>
  report.actor === "person"
    ? [YOU]
    : report.actor === "agent"
      ? [AGENTS]
      : [YOU, AGENTS];
/** Uses by each actor shown, such as "3 by you, 5 by agents". */
const madeBy = (shown: Shown[], made: Actors) =>
  shown
    .map(({ actor, by }) => `${formatCount(made[actor])} by ${by}`)
    .join(", ");
const NOTHING_USED: Record<Actor, string> = {
  person: "You used nothing",
  agent: "Your agents used nothing",
};

/**
 * Which pages and features you and your agents use, and which nothing uses, in a window of days:
 * the counts usage.sqlite3 holds and those the server holds for its next write (GET /usage).
 * It counts both, or yours or agents' alone.
 */
export function Usage() {
  const [days, setDays] = useState(30);
  const [actor, setActor] = useState<Actor | null>(null);
  // No refresh on a timer: each read of this page is a call it counts.
  const query = useData<UsageReport>(
    `/usage?days=${days}${actor ? `&actor=${actor}` : ""}`,
    undefined,
    { keepPrevious: true },
  );
  const report = query.data;
  const began =
    report?.recordedSince && report.recordedSince > report.window.from
      ? report.recordedSince
      : null;
  return (
    <>
      <header>
        <h1>Usage</h1>
        <p className="lede">
          Which pages and features you and your agents use, and which nothing
          uses.
        </p>
      </header>
      <div className="toolbar">
        <div className="window-toggles" role="group" aria-label="Days shown">
          {WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              aria-pressed={days === window}
              onClick={() => setDays(window)}
            >
              {window} days
            </button>
          ))}
        </div>
        <div className="actor-toggles" role="group" aria-label="Whose uses">
          {WHOSE.map((option) => (
            <button
              key={option.label}
              type="button"
              aria-pressed={actor === option.actor}
              onClick={() => setActor(option.actor)}
            >
              {option.label}
            </button>
          ))}
        </div>
        {began && <small>Counting began {formatDay(began)}.</small>}
      </div>
      <ErrorMessage error={query.error} />
      {report ? (
        <UsageView report={report} stale={query.isPlaceholderData} />
      ) : (
        !query.error && <p>Loading usage…</p>
      )}
    </>
  );
}

function UsageView({ report, stale }: { report: UsageReport; stale: boolean }) {
  const sums = totals(report);
  // Drawn as the report counts, which, while another loads, may not be as the toggles now say.
  const shown = shownIn(report);
  // Only you open pages, so agents' uses alone have no page views to show.
  const pages = report.actor !== "agent";
  const series = shown.map(({ label, tone }) => ({ label, tone }));
  const each = (made: Actors) => shown.map(({ actor }) => made[actor]);
  const split = (made: Actors) =>
    shown.length > 1 ? madeBy(shown, made) : `By ${shown[0].by}`;
  const weekly = report.window.days > DAILY_LIMIT;
  const columns = buckets(report);
  const column = (values: (bucket: Bucket) => number[]): Column[] =>
    columns.map((bucket) => ({
      key: bucket.first,
      label:
        bucket.first === bucket.last
          ? formatDay(bucket.first)
          : `${formatDay(bucket.first)} to ${formatDay(bucket.last)}`,
      values: values(bucket),
    }));
  const per = weekly ? "week" : "day";
  const unit = weekly ? "Week" : "Day";
  return (
    <div className={"usage" + (stale ? " stale" : "")} aria-busy={stale}>
      <dl className="usage-tiles">
        {pages && (
          <div className="usage-tile">
            <dt>Page views</dt>
            <dd>{formatCount(sums.pageViews)}</dd>
            <small>
              {sums.pagesOpened} of {sums.pages} pages opened
            </small>
          </div>
        )}
        <div className="usage-tile">
          <dt>Changes</dt>
          <dd>{formatCount(sums.changes.person + sums.changes.agent)}</dd>
          <small>{split(sums.changes)}</small>
        </div>
        <div className="usage-tile">
          <dt>Reads</dt>
          <dd>{formatCount(sums.reads.person + sums.reads.agent)}</dd>
          <small>{split(sums.reads)}</small>
        </div>
        <div className="usage-tile">
          <dt>Failed calls</dt>
          <dd>{formatCount(sums.failed)}</dd>
          <small>
            {sums.failed
              ? `${((sums.failed / sums.calls) * 100).toFixed(1)}% of calls`
              : "None failed"}
          </small>
        </div>
        <div className="usage-tile">
          <dt>Features used</dt>
          <dd>
            {formatCount(sums.featuresUsed)}{" "}
            <span className="usage-of">of {formatCount(sums.features)}</span>
          </dd>
          <Meter
            value={sums.featuresUsed}
            max={sums.features}
            label="Features used"
          />
          <small>
            {formatCount(sums.features - sums.featuresUsed)} not used
          </small>
        </div>
      </dl>
      {report.daily.length ? (
        <div className="usage-charts">
          {pages && (
            <ColumnChart
              className="wide"
              title={`Page views per ${per}`}
              unit={unit}
              series={[{ label: "Page views", tone: "you" }]}
              columns={column((bucket) => [bucket.pageViews])}
            />
          )}
          <ColumnChart
            title={`Changes per ${per}`}
            unit={unit}
            series={series}
            columns={column((bucket) => each(bucket.changes))}
          />
          <ColumnChart
            title={`Reads per ${per}`}
            unit={unit}
            series={series}
            columns={column((bucket) => each(bucket.reads))}
            note={
              pages
                ? "Reads include pages refreshing what they show, so they say less about what you use than page views and changes."
                : undefined
            }
          />
        </div>
      ) : (
        <Empty>
          {report.actor ? NOTHING_USED[report.actor] : "Nothing was used"} in
          these {report.window.days} days.
        </Empty>
      )}
      <section aria-labelledby="usage-pages">
        <h2 id="usage-pages">Pages</h2>
        {pages ? (
          <PageBars rows={pageRows(report)} />
        ) : (
          <p className="muted">
            Only you open pages, so agents have no page views.
          </p>
        )}
      </section>
      <section aria-labelledby="usage-areas">
        <h2 id="usage-areas">Features by area</h2>
        <p className="muted">
          Each feature is an API call, by method and route. Open an area to see
          its calls, used or not, and who may make them.
        </p>
        <div className="usage-areas">
          {areas(report).map((area) => (
            <AreaRow key={area.name} area={area} shown={shown} />
          ))}
        </div>
      </section>
      <p className="muted usage-note">
        Worklog counts each page you open and each call made to it by name and
        day alone, never a record or anything you typed. The counts live in{" "}
        <code>{report.file}</code>, apart from your records: backups and exports
        leave them out.
      </p>
    </div>
  );
}

/** Every page as a bar of its views, the most viewed first; a page nothing opened shows when, if ever, it last was. */
function PageBars({ rows }: { rows: PageRow[] }) {
  const most = Math.max(1, ...rows.map((row) => row.views));
  return (
    <ol className="page-bars">
      {rows.map((row) => (
        <li key={row.page} className={row.views ? undefined : "unused"}>
          <span className="page-bar-label">{row.label}</span>
          <span className="page-bar-track">
            {row.views > 0 && (
              <span
                className="page-bar"
                style={{
                  width: `calc((100% - 4.5rem) * ${row.views / most})`,
                }}
              />
            )}
            <span className="page-bar-value">
              {row.views
                ? formatCount(row.views)
                : row.lastUsed
                  ? `Not opened since ${formatDay(row.lastUsed)}`
                  : "Never opened"}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}

/**
 * An area's share of calls used and its uses; opened, each call with who may make it and its uses,
 * a column for each actor shown.
 */
function AreaRow({ area, shown }: { area: Area; shown: Shown[] }) {
  return (
    <details className="usage-area">
      <summary>
        <code className="usage-area-name">{area.name}</code>
        <Meter value={area.used} max={area.served} thin />
        <span className="usage-area-share">
          {area.used} of {area.served} used
        </span>
        <span className="usage-area-calls">
          {area.person + area.agent ? madeBy(shown, area) : "No calls"}
        </span>
        {area.failed > 0 && (
          <span className="usage-area-failed">
            {formatCount(area.failed)} failed
          </span>
        )}
      </summary>
      <div className="usage-features">
        <table>
          <thead>
            <tr>
              <th scope="col">Call</th>
              <th scope="col">Who may make it</th>
              {shown.map(({ actor, label }) => (
                <th key={actor} scope="col" className="number">
                  {label}
                </th>
              ))}
              <th scope="col" className="number">
                Failed
              </th>
              <th scope="col">Last used</th>
            </tr>
          </thead>
          <tbody>
            {area.features.map((feature) => (
              <tr
                key={feature.call}
                className={feature.used ? undefined : "unused"}
              >
                <th scope="row">
                  <code>{feature.call}</code>
                </th>
                <td>
                  {feature.scope?.startsWith("agent:") ? (
                    <code>{feature.scope}</code>
                  ) : (
                    scopeLabel(feature.scope)
                  )}
                </td>
                {feature.used ? (
                  <>
                    {shown.map(({ actor }) => (
                      <td key={actor} className="number">
                        {formatCount(feature[actor])}
                      </td>
                    ))}
                    <td className="number">{formatCount(feature.failed)}</td>
                  </>
                ) : (
                  <td colSpan={shown.length + 1}>Not used</td>
                )}
                <td>
                  {feature.lastUsed ? formatDay(feature.lastUsed) : "Never"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
