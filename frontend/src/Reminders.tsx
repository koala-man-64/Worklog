import { useEffect, useId, useState } from "react";
import { commands } from "./commands";
import { Pages } from "./Coordination";
import { ChangeHistory } from "./sections/History";
import { DraftField } from "./ui/DraftField";
import { Empty, ErrorMessage } from "./ui/Messages";
import {
  exactDiscussionDate,
  formatDiscussionDate,
} from "./discussionFormatting";
import { useData } from "./hooks";
import { Icon } from "./icons";
import {
  NewReminder,
  ReminderWhen,
  SetBy,
  TargetLink,
  useReminderWrites,
} from "./RecordReminders";
import {
  clockIn,
  inAnHour,
  toTomorrow,
  useTimezone,
  whenLabel,
} from "./reminderTime";
import type { Answer, ReminderDetail } from "./apiTypes";

const PAGE = 50;
// What the list shows: open reminders by when they come due, those due now, done ones, or all.
const VIEWS = { open: "Open", due: "Due", done: "Done", all: "All" } as const;
type View = keyof typeof VIEWS;
// Lists check again each minute, so a timed reminder comes due on its own.
const REFRESH_MS = 60_000;

/**
 * The sidebar's link to Reminders, with how many are due now. The count describes the link
 * rather than naming it, so the link is "Reminders" whatever is due, like every other link.
 */
export function RemindersLink({ current }: { current: boolean }) {
  const due = useData<Answer<"/api/v1/reminders">>(
    "/reminders?state=due&limit=1",
    REFRESH_MS,
  );
  const count = due.data?.total ?? 0;
  const description = useId();
  return (
    <a
      href="#reminders"
      aria-current={current ? "page" : undefined}
      aria-describedby={count ? description : undefined}
    >
      <Icon name="reminders" size={18} />
      Reminders
      {count > 0 && (
        <>
          <span className="nav-count" aria-hidden="true">
            {count}
          </span>
          <span id={description} hidden>
            {count} due
          </span>
        </>
      )}
    </a>
  );
}

/**
 * Reminders, beside the one open: `#reminders/<id>` opens one, and with nothing chosen the first
 * in the list opens. The person and their agents set them; only the person archives them.
 */
export function Reminders({ selected }: { selected?: string }) {
  const today = clockIn(useTimezone()).date;
  const [view, setView] = useState<View>("open");
  const [archived, setArchived] = useState(false);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const list = useData<Answer<"/api/v1/reminders">>(
    `/reminders?state=${view}&archived=${archived}&q=${encodeURIComponent(q)}&limit=${PAGE}&offset=${offset}`,
    REFRESH_MS,
  );
  const items = list.data?.items ?? [];
  // A reminder that opened by itself, as the first in the list, stays open once it leaves the
  // list, say when it is marked done, so the next press never lands on the one after it.
  const [opened, setOpened] = useState<string | undefined>();
  const first = items[0]?.id;
  useEffect(() => {
    if (!selected && !opened && first) setOpened(first);
  }, [selected, opened, first]);
  const current = selected ?? opened ?? first;
  const nothingYet =
    view === "open" && !archived && !q && list.data?.total === 0;
  // Any filter change starts again from the first page.
  const filter =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setOffset(0);
    };
  return (
    <div className="bounded-screen">
      <header>
        <h1>Reminders</h1>
        <p className="lede">
          What to come back to, on a day or at a time on it. Due reminders show
          on Today and beside Reminders in the sidebar. Your agents set them for
          you with <code>worklog agent reminders</code>.
        </p>
      </header>
      <ErrorMessage error={list.error} />
      <div className="split reminders-split">
        <div className="panel" role="region" aria-label="Reminder list">
          <div className="panel-head">
            <div
              className="status-toggles"
              role="group"
              aria-label="Show reminders"
            >
              {(Object.keys(VIEWS) as View[]).map((v) => (
                <button
                  key={v}
                  type="button"
                  aria-pressed={view === v}
                  onClick={() => filter(setView)(v)}
                >
                  {VIEWS[v]}
                </button>
              ))}
            </div>
            <label className="muted">
              <input
                type="checkbox"
                checked={archived}
                onChange={(e) => filter(setArchived)(e.target.checked)}
              />{" "}
              Show archive
            </label>
          </div>
          <div className="reminder-tools">
            {!archived && (
              <NewReminder
                today={today}
                created={(reminder) => {
                  location.hash = "#reminders/" + reminder.id;
                }}
              />
            )}
            <input
              type="search"
              aria-label="Find reminders"
              placeholder="Find reminders…"
              value={q}
              onChange={(e) => filter(setQ)(e.target.value)}
            />
          </div>
          <div className="panel-body">
            {list.isPending ? (
              <p>Loading reminders…</p>
            ) : !items.length ? (
              <p className="muted reminder-none">
                {archived
                  ? view === "all"
                    ? "No archived reminders."
                    : "No archived reminders in this view."
                  : q
                    ? "No reminder matches those words."
                    : view === "due"
                      ? "Nothing is due."
                      : view === "done"
                        ? "No reminder is done yet."
                        : "No reminders yet."}
              </p>
            ) : (
              <ul className="reminder-list">
                {items.map((r) => (
                  <li key={r.id}>
                    <a
                      href={"#reminders/" + r.id}
                      aria-current={r.id === current ? "page" : undefined}
                      className={
                        r.state === "done"
                          ? "done"
                          : r.overdue
                            ? "overdue"
                            : r.due
                              ? "due"
                              : undefined
                      }
                    >
                      <span className="reminder-name">{r.title}</span>
                      <small>
                        {r.state === "done" ? (
                          "Done · " + whenLabel(r, today)
                        ) : (
                          <ReminderWhen reminder={r} today={today} />
                        )}
                        {r.target && " · " + (r.target.title || "Untitled")}
                        {r.author.kind === "agent" &&
                          " · set by " + r.author.label}
                      </small>
                    </a>
                  </li>
                ))}
              </ul>
            )}
            <Pages
              offset={offset}
              total={list.data?.total ?? 0}
              change={setOffset}
              label="reminders"
            />
          </div>
        </div>
        <div
          className="pane detail reminder-pane"
          role="region"
          aria-label="Reminder"
        >
          {current ? (
            <ReminderView key={current} id={current} today={today} />
          ) : nothingYet ? (
            <Empty>
              Set a reminder for what you want to come back to: a follow-up, a
              merge once its checks pass, a renewal. Give it a day, and a time
              if one matters; it shows on Today once it is due.
            </Empty>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ReminderView({ id, today }: { id: string; today: string }) {
  const query = useData<ReminderDetail>("/reminders/" + id, REFRESH_MS);
  if (!query.data)
    return (
      <>
        <ErrorMessage error={query.error} />
        {!query.error && <p>Loading reminder…</p>}
      </>
    );
  return <ReminderPage reminder={query.data} today={today} />;
}

/**
 * One reminder: whether it is done or due, when it is due, which saves as its field loses focus,
 * the record it names, its title and notes, and who set it. Snoozing shows once it is due.
 */
function ReminderPage({
  reminder: r,
  today,
}: {
  reminder: ReminderDetail;
  today: string;
}) {
  const timezone = useTimezone();
  const writes = useReminderWrites(r);
  // A button's own write keeps the buttons from taking a second press until it lands. A date or
  // time saved as its field loses focus does not, so the press that took the focus still counts,
  // and its write follows the save.
  const [acting, setActing] = useState(false);
  const act = (write: () => Promise<unknown>) => {
    setActing(true);
    void write().finally(() => setActing(false));
  };
  const [date, setDate] = useState(r.date);
  const [time, setTime] = useState(r.time ?? "");
  // Each field takes its saved value back only when that value changes, so the save of one never
  // drops what is being typed into the other.
  useEffect(() => {
    setDate(r.date);
  }, [r.date]);
  useEffect(() => {
    setTime(r.time ?? "");
  }, [r.time]);
  // A day half typed or emptied goes back to the saved one. A time half typed goes back to the
  // saved one, and an emptied time makes the reminder due all day.
  const saveDate = (field: HTMLInputElement) => {
    if (field.validity.badInput || !date) return setDate(r.date);
    if (date !== r.date) void writes.patch({ date });
  };
  const saveTime = (field: HTMLInputElement) => {
    if (field.validity.badInput) return setTime(r.time ?? "");
    if ((time || null) !== r.time) void writes.patch({ time: time || null });
  };
  return (
    <article className="reminder" aria-labelledby={"reminder-" + r.id}>
      <header className="title-row reminder-head">
        <h2 id={"reminder-" + r.id}>{r.title}</h2>
        <div className="toolbar">
          {!r.archived &&
            (r.state === "open" ? (
              <button
                className="primary"
                disabled={acting}
                onClick={() => act(() => writes.patch({ state: "done" }))}
              >
                Mark done
              </button>
            ) : (
              <button
                disabled={acting}
                onClick={() => act(() => writes.patch({ state: "open" }))}
              >
                Reopen
              </button>
            ))}
          <button
            disabled={acting}
            onClick={() =>
              act(() => writes.command(r.archived ? "recover" : "archive"))
            }
          >
            {r.archived ? "Recover" : "Archive"}
          </button>
        </div>
      </header>
      <ErrorMessage error={writes.error} />
      {r.archived && (
        <p className="warning">
          Archived: this reminder is out of your lists, Today and the
          sidebar&rsquo;s count. Recover it to change it.
        </p>
      )}
      <p className="reminder-state">
        {r.state === "done" && r.doneAt ? (
          <>
            Done{" "}
            <time dateTime={r.doneAt} title={exactDiscussionDate(r.doneAt)}>
              {formatDiscussionDate(r.doneAt)}
            </time>
            {" · was due " + whenLabel(r, today)}
          </>
        ) : (
          <>
            {r.overdue ? "" : r.due ? "Due now · " : "Coming up · "}
            <ReminderWhen reminder={r} today={today} />
          </>
        )}
      </p>
      {r.state === "open" && r.due && !r.archived && (
        <div className="toolbar">
          <button
            disabled={acting}
            onClick={() => act(() => writes.patch(inAnHour(timezone)))}
          >
            Snooze 1 hour
          </button>
          <button
            disabled={acting}
            onClick={() => act(() => writes.patch(toTomorrow(r, timezone)))}
          >
            Snooze to tomorrow
          </button>
        </div>
      )}
      <div className="reminder-when-fields">
        <label>
          Date
          <input
            type="date"
            required
            disabled={r.archived}
            value={date}
            onChange={(e) => setDate(e.target.value)}
            onBlur={(e) => saveDate(e.currentTarget)}
          />
        </label>
        <label>
          Time
          <input
            type="time"
            disabled={r.archived}
            value={time}
            onChange={(e) => setTime(e.target.value)}
            onBlur={(e) => saveTime(e.currentTarget)}
          />
        </label>
        {r.time && !r.archived && (
          <button
            type="button"
            className="quiet"
            disabled={acting}
            onClick={() =>
              act(() => {
                setTime("");
                return writes.patch({ time: null });
              })
            }
          >
            All day
          </button>
        )}
      </div>
      {r.target && (
        <p className="reminder-target">
          About <TargetLink target={r.target} />{" "}
          {!r.archived && (
            <button
              type="button"
              className="quiet"
              disabled={acting}
              onClick={() =>
                act(() => writes.patch({ targetType: null, targetId: null }))
              }
            >
              Unlink
            </button>
          )}
        </p>
      )}
      <DraftField
        draftKey={"reminder-title-" + r.id}
        label="Title"
        initial={r.title}
        revision={r.revision}
        multiline={false}
        maxLength={1000}
        disabled={r.archived}
        save={(title, revision) =>
          commands.updateReminder(r.id, { revision, title })
        }
      />
      <DraftField
        draftKey={"reminder-notes-" + r.id}
        label="Notes (Markdown)"
        initial={r.notes}
        revision={r.revision}
        disabled={r.archived}
        placeholder="What to do, links, anything you will want then."
        save={(notes, revision) =>
          commands.updateReminder(r.id, { revision, notes })
        }
      />
      <p className="muted reminder-updated">
        {r.author.kind === "agent" ? <SetBy author={r.author} /> : "Set by you"}{" "}
        <time dateTime={r.createdAt} title={exactDiscussionDate(r.createdAt)}>
          {formatDiscussionDate(r.createdAt)}
        </time>
      </p>
      <ChangeHistory id={r.id} changes={r.history?.length ?? 0} />
    </article>
  );
}
