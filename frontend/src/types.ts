import type { Repository } from "./repositoryTypes";

/** A ticket's or a roadmap step's status. */
export type Status = "open" | "doing" | "blocked" | "done";
/** A work item's status, which can also be waiting on someone else. */
export type ItemStatus = Status | "waiting";
export type RecordBase = {
  id: string;
  revision: number;
  archived?: boolean;
  createdAt?: string;
  updatedAt?: string;
};
/** A person's tie to a work item, roadmap or repository; relationship is their role there. */
export type PersonAssignment = {
  id: string;
  personId: string;
  name: string;
  personTitle: string;
  personTeam: string;
  relationship: string;
  archived: boolean;
  createdAt: string;
};
export type Person = RecordBase & {
  name: string;
  title: string;
  team: string;
  /** Who they are and what to go to them for, in a sentence or two. */
  description: string;
  email: string;
  phone: string;
  /** A chat handle, such as a Teams or Slack name. */
  chat: string;
  /** Where they are, or their time zone. */
  location: string;
  notes: string;
  workItemCount?: number;
  roadmapCount?: number;
  repositoryCount?: number;
};
export type PersonRoadmapLink = {
  linkId: string;
  personId: string;
  roadmapId: string;
  relationship: string;
  createdAt: string;
  roadmap: {
    id: string;
    title: string;
    overview: string;
    archived: boolean;
    revision: number;
  };
};
export type PersonWorkItemLink = {
  linkId: string;
  personId: string;
  itemId: string;
  relationship: string;
  createdAt: string;
  item: Item;
};
export type PersonRepositoryLink = {
  linkId: string;
  personId: string;
  repositoryId: string;
  relationship: string;
  createdAt: string;
  repository: Repository;
};
export type PersonDetail = Person & {
  roadmaps: PersonRoadmapLink[];
  workItems: PersonWorkItemLink[];
  repositories: PersonRepositoryLink[];
  history: unknown[];
};
export type Item = RecordBase & {
  repositoryIds?: string[];
  isBacklog?: boolean;
  /** Stack rank, 1 first, among unfinished, unarchived work; null for done or archived work.
   * Lists, details and Today's entries carry it. */
  rank?: number | null;
  title: string;
  description: string;
  status: ItemStatus;
  adoUrl: string | null;
  roadmapId?: string | null;
  stepId: string | null;
  people?: PersonAssignment[];
};
export type Entry = RecordBase & {
  itemId: string;
  date: string;
  planned: boolean;
  worked: boolean;
  outcome: string;
  position: number;
  item: Item;
  // A day's entries only: what the work item's tabs hold, before the item is read.
  counts?: TabCounts;
  /** A day's entries only: the status the work item ended that day with, from its history;
   * work logged after its day has its status now. */
  statusOnDay?: ItemStatus;
};
export type TabCounts = {
  tasks: number;
  tasksDone: number;
  links: number;
  files: number;
  scratchpad: boolean;
};
/** POST /work-items/{id}/rank */
export type RankResult = { id: string; rank: number; rankedCount: number };
export type Day = {
  date: string;
  // Guards which entries the day holds and their order.
  revision: number;
  reflection: string;
  // Guards the reflection alone, so reordering or adding work leaves a draft able to save (v21).
  reflectionRevision: number;
  entries: Entry[];
  summary: { worked: Entry[]; completed: Entry[]; blocked: Entry[] };
  nextItemId: string | null;
};
export type Note = RecordBase & {
  title: string;
  text: string;
  ownerType: string;
  ownerId: string;
};
/** A work item's tasks come in the person's order. updatedAt is when the text or tick last changed. */
export type Task = RecordBase & {
  text: string;
  done: boolean;
  itemId: string;
  createdAt: string;
  updatedAt: string;
};
export type Attachment = RecordBase & {
  name: string;
  mime: string;
  size: number;
  sha256: string;
};
export type Reference = RecordBase & { path: string; itemId: string };
export type WebLink = RecordBase & {
  ownerType: string;
  ownerId: string;
  url: string;
  title: string;
  description: string;
};
export type AgentChat = RecordBase & {
  repositoryIds?: string[];
  ownerType: string;
  ownerId: string;
  chatId: string;
  title: string;
  createdDate: string;
  provider: string;
  model: string;
  repos: string[];
  // Roadmap chats only: the steps the chat is working on.
  stepIds: string[];
};
/** A question raised on a roadmap; answering it keeps the answer even if it is reopened. */
export type RoadmapQuestion = RecordBase & {
  roadmapId: string;
  title: string;
  details: string;
  answer: string;
  status: "open" | "answered";
};
/** A decision made on a roadmap, with why it was made. */
export type RoadmapDecision = RecordBase & {
  roadmapId: string;
  title: string;
  rationale: string;
  decidedDate: string;
};
/** One agent in a chat's transcript: the chat itself, or a subagent it started. */
export type ChatThread = {
  key: string;
  name: string;
  title: string;
  task: string;
  parent: string | null;
  model: string;
  state: "working" | "idle";
  lastActivityAt: string | null;
};
export type TranscriptSummary = {
  found: boolean;
  source: "claude" | "codex" | null;
  lastActivityAt: string | null;
  state: "working" | "idle";
  threads: ChatThread[];
  // Set when the transcript exists but could not be read.
  error?: string;
};
/**
 * An agent identity tied to a chat: registered with it, confirmed in it by you, or, only a
 * claim, with an open run that reports it. `chatId` is the chat ID as that tie stored it.
 */
export type ChatAgent = {
  id: string;
  label: string;
  avatar: string | null;
  active: boolean;
  binding: "registered" | "confirmed" | "run";
  chatId: string;
};
/** An agent chat with the record it belongs to, the steps it works on, and its transcript. */
export type ChatOverview = AgentChat & {
  owner: {
    type: "item" | "roadmap";
    id: string;
    title: string;
    archived: boolean;
  };
  // The roadmaps the owner sits within, top level first; a roadmap's own chat ends with that roadmap.
  roadmaps: { id: string; title: string; archived: boolean }[];
  steps: { id: string; title: string; status: Status; retired: boolean }[];
  agents: ChatAgent[];
  transcript: TranscriptSummary;
};
/** A Claude Code session running on this machine, with what already tracks it and who works in it. */
export type RunningSession = {
  sessionId: string;
  // The Claude desktop session running it, when the desktop app does.
  hostSessionId: string | null;
  // The ID to track it by: the one its agents use, else one a record tracks it under, else its own.
  chatId: string;
  name: string;
  cwd: string;
  entrypoint: string;
  kind: string;
  status: "working" | "waiting" | "idle";
  waitingFor: string;
  startedAt: string | null;
  statusAt: string | null;
  version: string;
  model: string;
  tracked: {
    id: string;
    title: string;
    archived: boolean;
    ownerType: "item" | "roadmap";
    ownerId: string;
    ownerTitle: string;
  }[];
  agents: ChatAgent[];
};
export type ChatMessage = {
  id: string;
  thread: string;
  at: string | null;
  kind: "user" | "assistant" | "agent" | "tools";
  author: string;
  authorThread?: string | null;
  recipient?: string;
  text?: string;
  truncated?: boolean;
  // Codex messages between agents: their type, such as NEW_TASK, and whether only an encrypted copy exists.
  messageType?: string;
  encrypted?: boolean;
  // Tool runs only.
  count?: number;
  until?: string | null;
  tools?: { name: string; summary: string }[];
};
export type Transcript = TranscriptSummary & {
  chat: AgentChat;
  messages: ChatMessage[];
  total: number;
  more: boolean;
};
export type GithubRef = RecordBase & {
  ownerType: string;
  ownerId: string;
  kind: "commit" | "pr";
  url: string;
  reference: string;
  title: string;
  description: string;
  chatId?: string | null;
  chatTitle?: string | null;
};
export type Detail = Item & {
  linkedRoadmap?: {
    id: string;
    title: string;
    archived?: boolean;
  } | null;
  linkedStep?: {
    id: string;
    title: string;
    roadmapId: string;
    roadmapTitle: string;
    retired?: boolean;
  } | null;
  notes: Note[];
  tasks: Task[];
  attachments: Attachment[];
  references: Reference[];
  links?: WebLink[];
  chats?: AgentChat[];
  github?: GithubRef[];
  history: unknown[];
  dailyEntries: Entry[];
  // How many items hold a stack rank.
  rankedCount: number;
};
export type Step = RecordBase & {
  title: string;
  description: string;
  parentId: string | null;
  position: number;
  status: Status;
  retired?: boolean;
  notes?: Note[];
  attachments?: Attachment[];
};
export type RoadmapRef = { id: string; title: string; archived: boolean };
export type Roadmap = RecordBase & {
  title: string;
  overview: string;
  parentId?: string | null;
  // Detail only: the path from the top level down, and direct sub-roadmaps.
  ancestors?: RoadmapRef[];
  children?: RoadmapRef[];
  steps: Step[];
  versions: { number: number; createdAt: string; note: string }[];
  notes: Note[];
  attachments: Attachment[];
  links?: WebLink[];
  chats?: AgentChat[];
  github?: GithubRef[];
  questions?: RoadmapQuestion[];
  decisions?: RoadmapDecision[];
  history: unknown[];
  imports?: { id: string; name: string; sha256: string; createdAt: string }[];
  workItems?: Item[];
  people?: PersonAssignment[];
};
export type Operation = { action: string; payload: Record<string, unknown> };
export type Proposal = RecordBase & {
  title: string;
  rationale: string;
  status: string;
  // Applied when its agent submitted it, because approval was off.
  autoApproved: boolean;
  // When its agent submitted it; null until then.
  submittedAt: string | null;
  actorLabel: string;
  // The directory its agent registered from; null for the person, a credential
  // issued in Settings, or an agent that registered without one.
  actorDirectory: string | null;
  operations: Operation[];
  attachments: Attachment[];
};
/**
 * A proposal as a history lists it, the latest first. number is its place in the sequence, 1 the
 * first: for a work item or roadmap, the order proposals were applied to it; for an agent, the order
 * it made them.
 */
export type ProposalEntry = {
  number: number;
  id: string;
  title: string;
  status: string;
  autoApproved: boolean;
  actorLabel: string;
  // The agent identity that made it; null for one the person wrote.
  agentId: string | null;
  createdAt: string;
  submittedAt: string | null;
  appliedAt: string | null;
  updatedAt: string;
  operationCount: number;
  // A work item's or roadmap's history only: its changes made to that record, by their number.
  changes?: { number: number; action: string }[];
};
export type Settings = {
  timezone: string;
  today: string;
  version: string;
  // False applies an agent's proposal as soon as it is submitted.
  requireProposalApproval: boolean;
  // False keeps agents from registering repositories; they propose them instead.
  agentRepositoryRegistration: boolean;
  storage: {
    databaseBytes: number;
    backupBytes: number;
    warning: string | null;
  };
  backups: { name: string; size: number; createdAt: string }[];
  credentials: {
    id: string;
    label: string;
    createdAt: string;
    revoked: boolean;
    agentId: string | null;
    // The agent whose credential registered this one; null when issued in Settings.
    registeredBy: { id: string; label: string } | null;
    // The chat a registered identity works in; null when issued in Settings.
    chatId: string | null;
    // Active identities this one registered, directly or through them; revoking it revokes them.
    registeredActive: number;
  }[];
  agents: { id: string; label: string; avatar: string | null }[];
};
export type List<T> = {
  items: T[];
  total: number;
  collectionRevision?: number;
  // Work lists: how many items hold a stack rank, whatever the filters.
  rankedCount?: number;
};
