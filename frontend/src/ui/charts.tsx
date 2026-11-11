import { useState, type CSSProperties, type PointerEvent } from "react";
import { formatCount } from "./format";

/** A series' colour: the person's or agents'. Each keeps its colour wherever it shows. */
export type Tone = "you" | "agents";
export type Series = { label: string; tone: Tone };
/** One column: its label, such as "Nov 6", and a value for each series, in the series' order. */
export type Column = { key: string; label: string; values: number[] };

/** Up to this many columns, each has its label under it; past it, the first, middle and last do. */
const LABEL_EACH = 8;

/** The smallest 1, 2 or 5 times a power of ten at or above `max`, so an axis ends on a clean number. */
export function niceTop(max: number) {
  if (max <= 1) return 1;
  const power = 10 ** Math.floor(Math.log10(max));
  return [1, 2, 5, 10].map((step) => step * power).find((top) => top >= max)!;
}

/**
 * Columns over time, each stacking its series from the base up. Pointing at the plot reads out the
 * nearest column's values; "Show as table" lists them all, which is also how a keyboard or a screen
 * reader reaches them, so the readout only adds to what the table says.
 */
export function ColumnChart({
  title,
  unit,
  series,
  columns,
  note,
  className,
}: {
  title: string;
  /** What a column is, such as "Day" or "Week": the table's first heading. */
  unit: string;
  series: Series[];
  columns: Column[];
  note?: string;
  /** Added to the figure's own, for the page to place the chart. */
  className?: string;
}) {
  const [pointed, setPointed] = useState<number | null>(null);
  const totals = columns.map((column) =>
    column.values.reduce((a, b) => a + b, 0),
  );
  const top = niceTop(Math.max(0, ...totals));
  const ticks = top > 1 && top % 2 === 0 ? [top, top / 2, 0] : [top, 0];
  const bySeries = series.map((_, index) =>
    columns.reduce((total, column) => total + column.values[index], 0),
  );
  const peak = totals.indexOf(Math.max(...totals));
  const span =
    columns.length > 1
      ? `${columns[0].label} to ${columns.at(-1)!.label}`
      : (columns[0]?.label ?? "");
  const summary =
    `${title}, ${span}: ` +
    (totals[peak] > 0
      ? series
          .map((one, index) =>
            series.length > 1
              ? `${one.label.toLowerCase()} ${bySeries[index]}`
              : `${bySeries[index]} in all`,
          )
          .join(", ") + `; the most, ${totals[peak]}, ${columns[peak].label}`
      : "none");
  const point = (event: PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    if (!box.width || !columns.length) return;
    const at = Math.floor(
      ((event.clientX - box.left) / box.width) * columns.length,
    );
    setPointed(Math.min(columns.length - 1, Math.max(0, at)));
  };
  const shown = pointed === null ? undefined : columns[pointed];
  const share = pointed === null ? 0 : (pointed + 0.5) / columns.length;
  const middle =
    columns.length > 2
      ? columns[Math.floor((columns.length - 1) / 2)]
      : undefined;
  return (
    <figure className={"column-chart" + (className ? ` ${className}` : "")}>
      <figcaption>
        <h3>{title}</h3>
        {series.length > 1 && (
          <ul className="chart-legend">
            {series.map((one, index) => (
              <li key={one.label}>
                <span
                  className={"chart-swatch " + one.tone}
                  aria-hidden="true"
                />
                {one.label} <strong>{formatCount(bySeries[index])}</strong>
              </li>
            ))}
          </ul>
        )}
      </figcaption>
      <div className="chart-body">
        {/* Evenly spaced from the top down, as the ticks are, each label centred on its line. */}
        <div className="chart-ticks" aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick}>{formatCount(tick)}</span>
          ))}
        </div>
        <div
          className="chart-plot"
          role="img"
          aria-label={summary}
          onPointerMove={point}
          onPointerDown={point}
          onPointerLeave={() => setPointed(null)}
        >
          {ticks.map((tick) => (
            <span
              key={tick}
              className="chart-grid"
              style={{ bottom: `${(tick / top) * 100}%` }}
            />
          ))}
          <div
            className="chart-columns"
            style={{ "--columns": columns.length } as CSSProperties}
          >
            {columns.map((column, index) => (
              <div
                key={column.key}
                className={
                  "chart-column" + (index === pointed ? " pointed" : "")
                }
              >
                {totals[index] > 0 && (
                  <div
                    className="chart-stack"
                    style={{ height: `${(totals[index] / top) * 100}%` }}
                  >
                    {column.values.map(
                      (value, at) =>
                        value > 0 && (
                          <span
                            key={series[at].label}
                            className={"chart-segment " + series[at].tone}
                            style={{ flexGrow: value }}
                          />
                        ),
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
          {shown && (
            <div
              className={
                "chart-readout" +
                (share < 0.2 ? " start" : share > 0.8 ? " end" : "")
              }
              style={{ left: `${share * 100}%` }}
            >
              <span className="chart-readout-label">{shown.label}</span>
              {series.map((one, index) => (
                <span key={one.label} className="chart-readout-row">
                  <span className={"chart-line-key " + one.tone} />
                  <strong>{formatCount(shown.values[index])}</strong>{" "}
                  {one.label}
                </span>
              ))}
            </div>
          )}
        </div>
        {columns.length <= LABEL_EACH ? (
          <div
            className="chart-dates each"
            style={{ "--columns": columns.length } as CSSProperties}
            aria-hidden="true"
          >
            {columns.map((column) => (
              <span key={column.key}>{column.label}</span>
            ))}
          </div>
        ) : (
          <div className="chart-dates" aria-hidden="true">
            <span>{columns[0].label}</span>
            {middle && <span>{middle.label}</span>}
            <span>{columns.at(-1)!.label}</span>
          </div>
        )}
      </div>
      {note && <p className="chart-note">{note}</p>}
      <details className="chart-table">
        <summary>Show as table</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">{unit}</th>
              {series.map((one) => (
                <th scope="col" key={one.label}>
                  {one.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {columns.map((column) => (
              <tr key={column.key}>
                <th scope="row">{column.label}</th>
                {column.values.map((value, index) => (
                  <td key={series[index].label}>{formatCount(value)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/**
 * A share of a whole: the part used, in the person's colour, over a track of the same hue. With a
 * label, screen readers announce it as a meter; without one, the text beside it says the same.
 */
export function Meter({
  value,
  max,
  label,
  thin = false,
}: {
  value: number;
  max: number;
  label?: string;
  thin?: boolean;
}) {
  const share = max > 0 ? Math.min(1, value / max) : 0;
  const fill = share > 0 && <span style={{ width: `${share * 100}%` }} />;
  const className = "meter" + (thin ? " thin" : "");
  return label ? (
    <div
      className={className}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
    >
      {fill}
    </div>
  ) : (
    <div className={className} aria-hidden="true">
      {fill}
    </div>
  );
}
