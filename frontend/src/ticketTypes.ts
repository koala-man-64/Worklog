import type { Status } from "./types";

export type TicketKind = "bug" | "incident" | "task";
export type TicketPriority = "low" | "normal" | "high" | "urgent";
/** The person ("local-user") or an agent identity, as it stands now. */
export type TicketPerson = {
  id: string;
  label: string;
  kind: "agent" | "human";
  /** Generated avatar name; null for the person. */
  avatar: string | null;
};
export type Ticket = {
  id: string;
  number: number;
  /** T-12: what agents cite in commits, discussions and other tickets. */
  key: string;
  title: string;
  /** Lists carry the first 1,000 characters; `truncated` says some were cut. */
  description: string;
  resolution: string;
  truncated: boolean;
  kind: TicketKind;
  priority: TicketPriority;
  status: Status;
  labels: string[];
  repositoryIds: string[];
  itemId: string | null;
  item: { id: string; title: string; status: Status; archived: boolean } | null;
  assigneeId: string | null;
  assignee: (TicketPerson & { active: boolean }) | null;
  author: TicketPerson;
  commentCount: number;
  archived: boolean;
  revision: number;
  createdAt: string;
  updatedAt: string;
  closedAt: string | null;
  lastActivityAt: string;
};
export type TicketComment = {
  id: string;
  ticketId: string;
  text: string;
  createdAt: string;
  author: TicketPerson;
};
export type TicketDetail = Ticket & {
  /** The latest comments, oldest first; `total` counts them all. */
  comments: { items: TicketComment[]; total: number };
  history: unknown[];
};
