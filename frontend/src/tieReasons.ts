import type { TieReason } from "./apiTypes";

/** What ties an agent's work to a record, as the Agents page and a roadmap name it, in display order. */
export const REASONS: Record<TieReason, string> = {
  participation: "Reported work",
  role: "Responsibility",
  update: "Status update",
  message: "Discussion",
  run: "Run",
  chat: "Tracked chat",
};
export const REASON_ORDER = Object.keys(REASONS) as TieReason[];
