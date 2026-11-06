import { useQuery } from "@tanstack/react-query";
import { bootstrap } from "./api";
import type {
  Reminder,
  ReminderTarget,
  ReminderTargetType,
} from "./reminderTypes";

/**
 * The Worklog timezone, from the session the app opened with and Settings updates. Reading it
 * never fetches; until there is a session, the browser's own timezone stands in.
 */
export function useTimezone(): string {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: bootstrap,
    enabled: false,
    staleTime: Infinity,
  });
  return (
    session.data?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone
  );
}

/** The date, YYYY-MM-DD, and the time, HH:MM, at a moment by the clock of a timezone. */
export function clockIn(
  timezone: string,
  at: number = Date.now(),
): { date: string; time: string } {
  const parts: Record<string, string> = {};
  for (const part of new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(at)))
    parts[part.type] = part.value;
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    time: `${parts.hour}:${parts.minute}`,
  };
}

/** The calendar day after a YYYY-MM-DD date. */
export function nextDay(date: string): string {
  const next = new Date(date + "T12:00:00Z");
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString().slice(0, 10);
}

/**
 * Where "Snooze 1 hour" moves a reminder: an hour from now by the timezone's clock. In the hour a
 * clock is set back, an hour on reads the same as now and the reminder would stay due, so it moves
 * to two hours on, the first time after that hour.
 */
export function inAnHour(timezone: string, now: number = Date.now()) {
  const from = clockIn(timezone, now);
  const later = clockIn(timezone, now + 3_600_000);
  return later.date + later.time > from.date + from.time
    ? later
    : clockIn(timezone, now + 7_200_000);
}

/** Where "Snooze to tomorrow" moves a reminder: tomorrow by the timezone's clock, at its own time. */
export const toTomorrow = (
  reminder: Pick<Reminder, "time">,
  timezone: string,
  now: number = Date.now(),
) => ({ date: nextDay(clockIn(timezone, now).date), time: reminder.time });

/** A reminder's day and time as a list shows it: "Today · 14:25", "Tomorrow · All day", "Wed, Nov 4 · 16:00". */
export function whenLabel(
  reminder: Pick<Reminder, "date" | "time">,
  today: string,
): string {
  const day =
    reminder.date === today
      ? "Today"
      : reminder.date === nextDay(today)
        ? "Tomorrow"
        : new Date(reminder.date + "T12:00:00").toLocaleDateString(undefined, {
            weekday: "short",
            month: "short",
            day: "numeric",
            year:
              reminder.date.slice(0, 4) === today.slice(0, 4)
                ? undefined
                : "numeric",
          });
  return `${day} · ${reminder.time ?? "All day"}`;
}

const ROUTES: Record<ReminderTargetType, string> = {
  item: "work",
  ticket: "tickets",
  roadmap: "roadmaps",
  person: "people",
  repository: "repositories",
  chat: "chats",
};
/** The page of the record a reminder names. */
export const targetHref = (target: Pick<ReminderTarget, "type" | "id">) =>
  `#${ROUTES[target.type]}/${target.id}`;
