export type Status = "open" | "doing" | "blocked" | "done";
export type RecordBase = {
  id: string;
  revision: number;
  archived?: boolean;
  createdAt?: string;
  updatedAt?: string;
};
export type PersonAssignment = {
  id: string;
  personId: string;
  name: string;
  personRole: string;
  relationship: string;
  archived: boolean;
  createdAt: string;
};
export type Person = RecordBase & {
  name: string;
  role: string;
  notes: string;
  workItemCount?: number;
  roadmapCount?: number;
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
export type PersonDetail = Person & {
  roadmaps: PersonRoadmapLink[];
  workItems: PersonWorkItemLink[];
  history: unknown[];
};
export type Item = RecordBase & {
  repositoryIds?: string[];
  isBacklog?: boolean;
  title: string;
  description: string;
  status: Status;
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
};
export type Day = {
  date: string;
  revision: number;
  reflection: string;
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
export type Task = RecordBase & { text: string; done: boolean; itemId: string };
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
  transcript: TranscriptSummary;
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
  operations: Operation[];
  attachments: Attachment[];
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
    // Active identities this one registered, directly or through them; revoking it revokes them.
    registeredActive: number;
  }[];
  agents: { id: string; label: string; avatar: string | null }[];
};
export type List<T> = {
  items: T[];
  total: number;
  collectionRevision?: number;
};
