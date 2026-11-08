/**
 * What the backend's routes take and answer, from its OpenAPI schema (generatedApi.ts, which
 * `npm run generate:api` writes from docs/openapi.json). Nothing here describes a shape by hand:
 * each name is a generated schema, or a route's answer, under the name the pages use for it.
 */
import type { components, paths } from "./generatedApi";

export type Schema = components["schemas"];
type Method = "get" | "post" | "put" | "patch" | "delete";
type Operation<P extends keyof paths, M extends Method> = NonNullable<paths[P][M]>;

// A method a route lacks is `never` here; conditional types infer `unknown` from `never`, so the
// helpers below check for it first.
type Lacks<P extends keyof paths, M extends Method> = [
  Operation<P, M>,
] extends [never]
  ? true
  : false;

/** A route's answer: what it sends with 200; never for a method the route lacks. */
export type Answer<P extends keyof paths, M extends Method = "get"> =
  Lacks<P, M> extends true
    ? never
    : Operation<P, M> extends {
          responses: { 200: { content: { "application/json": infer A } } };
        }
      ? A
      : never;

/** What a route takes as its JSON body; never for a method the route lacks. */
export type Body<P extends keyof paths, M extends Method> =
  Lacks<P, M> extends true
    ? never
    : Operation<P, M> extends {
          requestBody?: { content: { "application/json": infer B } };
        }
      ? B
      : never;

/** Who wrote a reminder, a ticket or a comment; the person has no avatar. */
export type Author = Schema["Author"];
/** One change in a record's own history, without the records: /history has those. */
export type Change = Schema["Change"];

// Tickets.
export type Ticket = Schema["TicketListed"];
export type TicketDetail = Schema["Ticket"];
export type TicketComment = Schema["TicketComment"];
export type TicketKind = Ticket["kind"];
export type TicketPriority = Ticket["priority"];

// Releases.
export type ReleaseRow = Schema["ReleaseListed"];
export type ReleaseDetail = Schema["Release"];
export type ReleaseStatus = ReleaseRow["status"];
export type ReleaseCounts = Schema["ReleaseCounts"];
export type ReleaseCheck = Schema["ReleaseCheck"];
export type CheckKind = ReleaseCheck["kind"];
export type ReleaseTarget = Schema["ReleaseHolding"]["targetType"];
export type ReleaseItem = Schema["ReleaseItem"];
export type ReleaseRoadmap = Schema["ReleaseRoadmap"];
export type ReleaseStep = Schema["ReleaseStep"];
export type ReleaseChange = Schema["ReleaseChange"];
export type ReleaseEvent = Schema["ReleaseHistoryEntry"];

// Reminders.
export type Reminder = Schema["ReminderListed"];
/** A reminder with its notes, as a create or an edit returns it. */
export type ReminderRecord = Schema["ReminderRecord"];
/** A reminder with its notes and history, as its own page reads it. */
export type ReminderDetail = Schema["Reminder"];
export type ReminderTarget = Schema["ReminderTarget"];
export type ReminderTargetType = ReminderTarget["type"];

// Resources.
export type Resource = Schema["ResourceListed"];
export type ResourcePage = Schema["ResourcePage"];
export type ResourceDetail = Schema["Resource"];
