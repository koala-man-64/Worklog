import { useEffect, useId, useRef, useState } from "react";
import { ErrorMessage, Markdown, statusLabel } from "./components";
import type { Day, Entry, ItemStatus } from "./types";

// The day's summary groups what was worked on by the status each item ended the day with:
// finished work first, then what waits on others, and blockers last, as a standup reads.
// Every status needs a place here, so a new one can't drop its work from the summary.
const ORDER: Record<ItemStatus, number> = {
  done: 0,
  doing: 1,
  open: 2,
  waiting: 3,
  blocked: 4,
};
const GROUP_LAST = Object.keys(ORDER).length;
const statusOf = (entry: Entry) => entry.statusOnDay ?? entry.item.status;

/** A title as plain text on one line: Markdown characters in it stay as typed. */
function literal(title: string) {
  return title
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[\\`*_[\]<>~&]/g, "\\$&");
}

/** An outcome as the body of its list item, indented under the title, its own lists and
 * paragraphs included. */
function indented(outcome: string) {
  return outcome
    .replace(/\r\n?/g, "\n")
    .replace(/^(?:[ \t]*\n)+/, "")
    .trimEnd()
    .split("\n")
    .map((line) => (line.trim() ? "  " + line : ""))
    .join("\n");
}

function listItem(entry: Entry) {
  const title = `- **${literal(entry.item.title)}**`;
  return entry.outcome.trim()
    ? `${title}\n\n${indented(entry.outcome)}`
    : title;
}

/**
 * The day's work ticked "Worked on", as Markdown: the date, then each item's title and outcome
 * under the status it ended the day with, in the day's order. Null while nothing is worked on.
 */
export function summaryMarkdown(day: Day): string | null {
  const worked = day.entries.filter((entry) => entry.worked);
  if (!worked.length) return null;
  const date = new Date(day.date + "T12:00:00").toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
  const blocks = [`**${date}**`];
  // The statuses the day's work ended in, in summary order; one this page doesn't know comes last.
  const statuses = [...new Set(worked.map(statusOf))].sort(
    (a, b) => (ORDER[a] ?? GROUP_LAST) - (ORDER[b] ?? GROUP_LAST),
  );
  for (const status of statuses)
    blocks.push(
      `### ${statusLabel(status)}`,
      worked
        .filter((entry) => statusOf(entry) === status)
        .map(listItem)
        .join("\n\n"),
    );
  return blocks.join("\n\n") + "\n";
}

// Rich editors such as Teams, Outlook and Slack paste the HTML; plain text boxes, Markdown
// ones included, paste the Markdown.
async function copyBoth(markdown: string, html: string) {
  if (
    typeof ClipboardItem === "function" &&
    typeof navigator.clipboard.write === "function"
  ) {
    await navigator.clipboard.write([
      new ClipboardItem({
        "text/plain": new Blob([markdown], { type: "text/plain" }),
        "text/html": new Blob([html], { type: "text/html" }),
      }),
    ]);
  } else {
    await navigator.clipboard.writeText(markdown);
  }
}

/** The day's summary on Today, to read and to copy as a standup update. The page shows the
 * same Markdown the copy holds. */
export function DaySummary({ day }: { day: Day }) {
  const heading = useId();
  const body = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 3000);
    return () => clearTimeout(timer);
  }, [copied]);
  if (!day.entries.length) return null;
  const markdown = summaryMarkdown(day);
  const copy = async () => {
    if (!markdown) return;
    setError(null);
    setCopied(false);
    try {
      await copyBoth(markdown, body.current?.innerHTML ?? "");
      setCopied(true);
    } catch (cause) {
      setError(
        new Error(
          "The summary was not copied: " +
            (cause instanceof Error ? cause.message : String(cause)),
        ),
      );
    }
  };
  return (
    <section className="day-report" aria-labelledby={heading}>
      <div className="section-heading day-heading">
        <h2 id={heading}>The day’s summary</h2>
        <div className="toolbar">
          <span role="status">{copied ? "Copied" : ""}</span>
          <button
            type="button"
            disabled={!markdown}
            onClick={() => void copy()}
          >
            Copy summary
          </button>
        </div>
      </div>
      {markdown ? (
        <div ref={body}>
          <Markdown text={markdown} />
        </div>
      ) : (
        <p className="muted">Nothing marked “Worked on” yet.</p>
      )}
      <ErrorMessage error={error} />
    </section>
  );
}
