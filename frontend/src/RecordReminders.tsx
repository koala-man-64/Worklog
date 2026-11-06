import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { request } from "./api";
import { Avatar } from "./Avatar";
import { commands } from "./commands";
import { ErrorMessage } from "./components";
import { useCommand, useData } from "./hooks";
import {
  clockIn,
  targetHref,
  toTomorrow,
  useTimezone,
  whenLabel,
} from "./reminderTime";
import type {
  Reminder,
  ReminderAuthor,
  ReminderDetail,
  ReminderTarget,
  ReminderTargetType,
} from "./reminderTypes";
import type { List } from "./types";

/** The most open reminders a record's page or Today lists; the Reminders page has the rest. */
const SHOWN = 50;
const NOUNS: Record<ReminderTargetType, string> = {
  item: "work item",
  ticket: "ticket",
  roadmap: "roadmap",
  person: "person",
  repository: "repository",
  chat: "chat",
};

/**
 * Changes to one reminder, sent one after another, each from the revision the one before it left,
 * so a date saved as its field loses focus and the button that took the focus never conflict.
 */
export function useReminderWrites(reminder: Pick<Reminder, "id" | "revision">) {
  const client = useQueryClient();
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const revision = useRef(reminder.revision);
  const [pending, setPending] = useState(0);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    revision.current = Math.max(revision.current, reminder.revision);
  }, [reminder.revision]);
  const send = (path: string, method: string, fields: object) => {
    setPending((n) => n + 1);
    const sent = chain.current
      .catch(() => undefined)
      .then(() =>
        request<ReminderDetail>(path, method, {
          ...fields,
          revision: revision.current,
        }),
      );
    chain.current = sent
      .then(
        (saved) => {
          revision.current = saved.revision;
          setError(null);
        },
        (failure: unknown) => setError(failure),
      )
      .finally(() => {
        setPending((n) => n - 1);
        void client.invalidateQueries({
          predicate: (q) => q.queryKey[0] !== "session",
        });
      });
    return chain.current;
  };
  return {
    patch: (fields: object) =>
      send("/reminders/" + reminder.id, "PATCH", fields),
    command: (verb: "archive" | "recover") =>
      send(`/reminders/${reminder.id}/${verb}`, "POST", {}),
    pending: pending > 0,
    error,
  };
}

/** When a reminder is due, marked when that has passed. */
export function ReminderWhen({
  reminder,
  today,
}: {
  reminder: Pick<Reminder, "date" | "time" | "overdue">;
  today: string;
}) {
  return (
    <time
      className={"reminder-when" + (reminder.overdue ? " overdue" : "")}
      dateTime={reminder.date + (reminder.time ? "T" + reminder.time : "")}
    >
      {reminder.overdue && "Overdue · "}
      {whenLabel(reminder, today)}
    </time>
  );
}

/** The record a reminder names, linked to its page. */
export function TargetLink({ target }: { target: ReminderTarget }) {
  return (
    <>
      <a href={targetHref(target)}>{target.title || "Untitled"}</a>
      {target.archived && " (archived)"}
    </>
  );
}

/** Who set a reminder, when it was an agent. */
export function SetBy({ author }: { author: ReminderAuthor }) {
  return author.kind === "agent" ? (
    <span className="reminder-setter">
      <Avatar name={author.avatar} kind="agent" size={16} />
      Set by {author.label}
    </span>
  ) : null;
}

/** One open reminder in a list on Today or a record's page, with "Mark done" and, once it is due, a snooze. */
export function ReminderRow({
  reminder,
  today,
  showTarget = true,
}: {
  reminder: Reminder;
  today: string;
  showTarget?: boolean;
}) {
  const timezone = useTimezone();
  const writes = useReminderWrites(reminder);
  return (
    <li
      className={
        "reminder-row" +
        (reminder.overdue ? " overdue" : reminder.due ? " due" : "")
      }
    >
      <div className="reminder-main">
        <a className="item-title" href={"#reminders/" + reminder.id}>
          {reminder.title}
        </a>
        <small>
          <ReminderWhen reminder={reminder} today={today} />
          {showTarget && reminder.target && (
            <>
              {" · "}
              <TargetLink target={reminder.target} />
            </>
          )}
          {reminder.author.kind === "agent" && " · "}
          <SetBy author={reminder.author} />
        </small>
      </div>
      <div className="toolbar compact">
        {reminder.due && (
          <button
            type="button"
            className="quiet"
            disabled={writes.pending}
            aria-label={`Snooze ${reminder.title} to tomorrow`}
            onClick={() => void writes.patch(toTomorrow(reminder, timezone))}
          >
            Snooze to tomorrow
          </button>
        )}
        <button
          type="button"
          disabled={writes.pending}
          aria-label={`Mark ${reminder.title} done`}
          onClick={() => void writes.patch({ state: "done" })}
        >
          Mark done
        </button>
      </div>
      <ErrorMessage error={writes.error} />
    </li>
  );
}

/** Sets a reminder from its title, its day (today unless changed) and, if given, its time. */
export function NewReminder({
  target,
  today,
  created,
  disabled = false,
}: {
  target?: { type: ReminderTargetType; id: string };
  today: string;
  created?: (reminder: ReminderDetail) => void;
  disabled?: boolean;
}) {
  const [title, setTitle] = useState("");
  const [date, setDate] = useState(today);
  const [time, setTime] = useState("");
  const create = useCommand(commands.createReminder);
  return (
    <form
      className="reminder-add"
      onSubmit={(e) => {
        e.preventDefault();
        if (!title.trim() || !date) return;
        create.mutate(
          {
            title: title.trim(),
            notes: "",
            date,
            time: time || null,
            targetType: target?.type ?? null,
            targetId: target?.id ?? null,
          },
          {
            onSuccess: (reminder) => {
              setTitle("");
              setTime("");
              created?.(reminder);
            },
          },
        );
      }}
    >
      <input
        aria-label="New reminder"
        placeholder="Remind me to…"
        maxLength={1000}
        disabled={disabled}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <input
        type="date"
        aria-label="New reminder date"
        required
        disabled={disabled}
        value={date}
        onChange={(e) => setDate(e.target.value)}
      />
      <input
        type="time"
        aria-label="New reminder time"
        disabled={disabled}
        value={time}
        onChange={(e) => setTime(e.target.value)}
      />
      <button
        className="primary"
        disabled={disabled || create.isPending || !title.trim() || !date}
      >
        Add reminder
      </button>
      <ErrorMessage error={create.error} />
    </form>
  );
}

/**
 * The open reminders about one record, on that record's page, with "Add reminder" unless the
 * record is archived. Done ones stay on the Reminders page.
 */
export function RecordReminders({
  targetType,
  targetId,
  archived = false,
  heading = "h3",
}: {
  targetType: ReminderTargetType;
  targetId: string;
  archived?: boolean;
  heading?: "h2" | "h3";
}) {
  const today = clockIn(useTimezone()).date;
  const list = useData<List<Reminder>>(
    `/reminders?state=open&targetType=${targetType}&targetId=${encodeURIComponent(targetId)}&limit=${SHOWN}`,
    60_000,
  );
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const Heading = heading;
  return (
    <section className="reminder-section">
      <Heading>
        Reminders
        {total > 0 && (
          <>
            {" "}
            <span className="count">{total}</span>
          </>
        )}
      </Heading>
      {!archived && (
        <NewReminder
          target={{ type: targetType, id: targetId }}
          today={today}
        />
      )}
      <ErrorMessage error={list.error} />
      {list.isPending ? (
        <p>Loading reminders…</p>
      ) : !list.error && !items.length ? (
        <p className="muted">No open reminders for this {NOUNS[targetType]}.</p>
      ) : (
        <ul className="reminder-rows">
          {items.map((reminder) => (
            <ReminderRow
              key={reminder.id}
              reminder={reminder}
              today={today}
              showTarget={false}
            />
          ))}
        </ul>
      )}
      {total > items.length && (
        <p className="muted">
          {total - items.length} more on <a href="#reminders">Reminders</a>.
        </p>
      )}
    </section>
  );
}

/**
 * Today's reminders: on today, every open one due by the end of the day, overdue ones first; on
 * another day, the open ones due on it. Nothing shows when there are none. It checks again each
 * minute, so a timed reminder comes due on its own.
 */
export function TodayReminders({
  date,
  today,
}: {
  date: string;
  today: string;
}) {
  const list = useData<List<Reminder>>(
    `/reminders?state=open&${date === today ? "until" : "date"}=${date}&limit=${SHOWN}`,
    60_000,
  );
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  if (!list.error && !items.length) return null;
  return (
    <section className="today-reminders" aria-labelledby="today-reminders">
      <div className="section-heading day-heading">
        <h2 id="today-reminders">
          Reminders <span className="count">{total}</span>
        </h2>
      </div>
      <ErrorMessage error={list.error} />
      <ul className="reminder-rows">
        {items.map((reminder) => (
          <ReminderRow key={reminder.id} reminder={reminder} today={today} />
        ))}
      </ul>
      {total > items.length && (
        <p className="muted">
          {total - items.length} more on <a href="#reminders">Reminders</a>.
        </p>
      )}
    </section>
  );
}
