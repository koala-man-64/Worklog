/** The kinds of record a reminder may name. */
export type ReminderTargetType =
  "item" | "ticket" | "roadmap" | "person" | "repository" | "chat";
/** The record a reminder names, as it stands now: a ticket's title starts with its key. */
export type ReminderTarget = {
  type: ReminderTargetType;
  id: string;
  title: string;
  archived: boolean;
};
/** Who set a reminder: the person, or an agent identity with its avatar. */
export type ReminderAuthor = {
  id: string;
  label: string;
  kind: "agent" | "human";
  avatar: string | null;
};
/** A reminder as lists carry it, without its notes. */
export type Reminder = {
  id: string;
  title: string;
  /** The day it is due, YYYY-MM-DD, by the Worklog timezone. */
  date: string;
  /** The time on that day, HH:MM; null when it is due all day. */
  time: string | null;
  state: "open" | "done";
  /** Open, and its day, or its time on that day, has come. */
  due: boolean;
  /** Open, and its day has passed. */
  overdue: boolean;
  target: ReminderTarget | null;
  author: ReminderAuthor;
  archived: boolean;
  revision: number;
  createdAt: string;
  updatedAt: string;
  doneAt: string | null;
};
/** Who changed a reminder and how, without the records: /history has those. */
export type ReminderChange = {
  id: string;
  kind: string;
  actorId: string;
  actorLabel: string;
  actorKind: "agent" | "human";
  createdAt: string;
};
/** A reminder with its notes, as its own page, a create or an edit returns it. */
export type ReminderDetail = Reminder & {
  notes: string;
  /** The latest 100 changes, newest first; only a reminder's own page carries them. */
  history?: ReminderChange[];
};
