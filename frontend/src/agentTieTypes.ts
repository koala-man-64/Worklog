/** What an agent can be tied to, in the order lists show them (backend agent_links.TYPES). */
export const TIE_TYPES = [
  "item",
  "roadmap",
  "step",
  "repository",
  "person",
  "resource",
  "discussion",
  "chat",
  "proposal",
  "ticket",
  "agent",
  "question",
  "decision",
] as const;
export type TieType = (typeof TIE_TYPES)[number];

/**
 * The tied record as Worklog found it: its title and whether it is archived (a step
 * retired, a repository merged, an agent with no live credential), with what a page
 * needs to link to it.
 */
export type TieTarget = {
  id: string;
  title: string;
  archived: boolean;
  status?: string;
  roadmapId?: string;
  roadmapTitle?: string;
  number?: number;
  ownerType?: string;
  ownerId?: string;
  mergedInto?: string | null;
  avatar?: string | null;
};
export type TiedAgent = {
  id: string;
  label: string;
  avatar: string | null;
  active: boolean;
};
export type AgentTie = {
  id: string;
  agentId: string;
  agent: TiedAgent | null;
  targetType: TieType;
  targetId: string;
  target: TieTarget | null;
  relationship: string;
  /** "human" when the person set the tie or last changed it; "agent" when the agent did. */
  setBy: "human" | "agent";
  createdAt: string;
};

export const TIE_LABELS: Record<TieType, { one: string; many: string }> = {
  item: { one: "Work item", many: "Work items" },
  roadmap: { one: "Roadmap", many: "Roadmaps" },
  step: { one: "Step", many: "Steps" },
  repository: { one: "Repository", many: "Repositories" },
  person: { one: "Person", many: "People" },
  resource: { one: "Resource", many: "Resources" },
  discussion: { one: "Discussion", many: "Discussions" },
  chat: { one: "Tracked chat", many: "Tracked chats" },
  proposal: { one: "Proposal", many: "Proposals" },
  ticket: { one: "Ticket", many: "Tickets" },
  agent: { one: "Agent", many: "Agents" },
  question: { one: "Question", many: "Questions" },
  decision: { one: "Decision", many: "Decisions" },
};

/** Where a tied record opens: a step on its roadmap's outline, a question or decision on its roadmap. */
export function tieHref(
  type: TieType,
  id: string,
  target: TieTarget | null,
): string {
  const roadmap = target?.roadmapId
    ? "#roadmaps/" + target.roadmapId
    : "#roadmaps";
  switch (type) {
    case "item":
      return "#work/" + id;
    case "roadmap":
      return "#roadmaps/" + id;
    case "step":
      return target?.roadmapId ? `${roadmap}/${id}` : roadmap;
    case "question":
    case "decision":
      return roadmap;
    case "repository":
      return "#repositories/" + id;
    case "person":
      return "#people/" + id;
    case "resource":
      return "#resources/" + id;
    case "discussion":
      return "#discussions/" + id;
    case "chat":
      return "#chats/" + id;
    case "proposal":
      return "#review/" + id;
    case "ticket":
      return "#tickets/" + id;
    case "agent":
      return "#agents/" + id;
  }
}

/** Why a tied record no longer takes new ties, in the words its own page uses. */
export function archivedWord(type: TieType): string {
  if (type === "step") return "retired";
  if (type === "agent") return "credential revoked";
  return "archived";
}

/** The query string that reads the ties on some records of one type. */
export function tiesOn(type: TieType, ids: string[]): string {
  return (
    "/agent-links?targetType=" +
    type +
    ids.map((id) => "&targetId=" + encodeURIComponent(id)).join("")
  );
}

/** A record a picker offers: a list's own record, a search result, or an agent. */
export type PickedRecord = {
  id: string;
  title?: string;
  name?: string;
  label?: string;
  kind?: string;
  active?: boolean;
};
export function pickedTitle(record: PickedRecord): string {
  return record.title ?? record.name ?? record.label ?? record.id;
}
// Kinds of record the search index finds by type; the rest have list routes of their own.
const SEARCHED: Partial<Record<TieType, true>> = {
  step: true,
  question: true,
  decision: true,
  discussion: true,
  chat: true,
};
/** Where a picker reads the records of one type that match what was typed. */
export function pickerPath(type: TieType, q: string): string {
  const words = encodeURIComponent(q);
  if (SEARCHED[type])
    return `/search?archived=false&limit=20&type=${type}&q=${words}`;
  switch (type) {
    case "item":
      return `/work-items?view=all&archived=false&limit=20&q=${words}`;
    case "roadmap":
      return `/roadmaps?archived=false&q=${words}`;
    case "repository":
      return `/repositories?archived=false&limit=20&q=${words}`;
    case "person":
      return `/people?archived=false&q=${words}`;
    case "resource":
      return `/resources?limit=20&q=${words}`;
    case "ticket":
      return `/tickets?status=all&limit=20&q=${words}`;
    case "proposal":
      return "/proposals";
    default:
      return "/discussions/participants";
  }
}
/** Searches a picker makes in the browser, over a list its route returns whole. */
export function pickerFilter(
  type: TieType,
  agentId: string,
): ((record: PickedRecord, q: string) => boolean) | undefined {
  const matches = (record: PickedRecord, q: string) =>
    !q || pickedTitle(record).toLowerCase().includes(q.toLowerCase());
  if (type === "proposal") return matches;
  if (type === "agent")
    return (record, q) =>
      record.kind === "agent" &&
      record.active !== false &&
      record.id !== agentId &&
      matches(record, q);
  return undefined;
}
/** What a picker says before anything is typed, or when its type has nothing at all. */
export function pickerNone(type: TieType): string {
  return SEARCHED[type]
    ? "Type to search."
    : `No ${TIE_LABELS[type].many.toLowerCase()} yet.`;
}
