import { useState } from "react";
import { useData } from "./hooks";
import type { UsageReport } from "./apiTypes";
import { ColumnChart, Meter, type Column, type Series } from "./ui/charts";
import { formatCount, formatDay } from "./ui/format";
import { Empty, ErrorMessage } from "./ui/Messages";
import {
  areas,
  buckets,
  DAILY_LIMIT,
  pageRows,
  scopeLabel,
  totals,
  type Area,
  type Bucket,
  type PageRow,
} from "./usageReport";

/** The windows the page offers, in days; 30 is `worklog usage`'s own. */
const WINDOWS = [7, 30, 90, 365];
const YOU_AND_AGENTS: Series[] = [
  { label: "You", tone: "you" },
  { label: "Agents", tone: "agents" },
];

/**
 * Which pages and features you and your agents use, and which nothing uses, in a window of days:
 * the counts usage.sqlite3 holds and those the server holds for its next write (GET /usage).
 */
export function Usage() {
  const [days, setDays] = useState(30);
  // No refresh on a timer: each read of this page is a call it counts.
  const query = useData<UsageReport>(`/usage?days=${days}`, undefined, {
    keepPrevious: true,
  });
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
        <div className="usage-tile">
          <dt>Page views</dt>
          <dd>{formatCount(sums.pageViews)}</dd>
          <small>
            {sums.pagesOpened} of {sums.pages} pages opened
          </small>
        </div>
        <div className="usage-tile">
          <dt>Changes</dt>
          <dd>{formatCount(sums.changes.person + sums.changes.agent)}</dd>
          <small>
            {formatCount(sums.changes.person)} by you,{" "}
            {formatCount(sums.changes.agent)} by agents
          </small>
        </div>
        <div className="usage-tile">
          <dt>Reads</dt>
          <dd>{formatCount(sums.reads.person + sums.reads.agent)}</dd>
          <small>
            {formatCount(sums.reads.person)} by you,{" "}
            {formatCount(sums.reads.agent)} by agents
          </small>
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
          <ColumnChart
            title={`Page views per ${per}`}
            unit={unit}
            series={[{ label: "Page views", tone: "you" }]}
            columns={column((bucket) => [bucket.pageViews])}
          />
          <ColumnChart
            title={`Changes per ${per}`}
            unit={unit}
            series={YOU_AND_AGENTS}
            columns={column((bucket) => [
              bucket.changes.person,
              bucket.changes.agent,
            ])}
          />
          <ColumnChart
            title={`Reads per ${per}`}
            unit={unit}
            series={YOU_AND_AGENTS}
            columns={column((bucket) => [
              bucket.reads.person,
              bucket.reads.agent,
            ])}
            note="Reads include pages refreshing what they show, so they say less about what you use than page views and changes."
          />
        </div>
      ) : (
        <Empty>Nothing was used in these {report.window.days} days.</Empty>
      )}
      <section aria-labelledby="usage-pages">
        <h2 id="usage-pages">Pages</h2>
        <PageBars rows={pageRows(report)} />
      </section>
      <section aria-labelledby="usage-areas">
        <h2 id="usage-areas">Features by area</h2>
        <p className="muted">
          Each feature is an API call, by method and route. Open an area to see
          its calls, used or not, and who may make them.
        </p>
        <div className="usage-areas">
          {areas(report).map((area) => (
            <AreaRow key={area.name} area={area} />
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

/** An area's share of calls used and its uses; opened, each call with who may make it and its uses. */
function AreaRow({ area }: { area: Area }) {
  return (
    <details className="usage-area">
      <summary>
        <code className="usage-area-name">{area.name}</code>
        <Meter value={area.used} max={area.served} thin />
        <span className="usage-area-share">
          {area.used} of {area.served} used
        </span>
        <span className="usage-area-calls">
          {area.person + area.agent
            ? `${formatCount(area.person)} by you, ${formatCount(area.agent)} by agents`
            : "No calls"}
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
              <th scope="col" className="number">
                You
              </th>
              <th scope="col" className="number">
                Agents
              </th>
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
                    <td className="number">{formatCount(feature.person)}</td>
                    <td className="number">{formatCount(feature.agent)}</td>
                    <td className="number">{formatCount(feature.failed)}</td>
                  </>
                ) : (
                  <td colSpan={3}>Not used</td>
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
