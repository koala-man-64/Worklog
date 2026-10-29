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
  ownerType: string;
  ownerId: string;
  chatId: string;
  title: string;
  createdDate: string;
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
export type Roadmap = RecordBase & {
  title: string;
  overview: string;
  steps: Step[];
  versions: { number: number; createdAt: string; note: string }[];
  notes: Note[];
  attachments: Attachment[];
  links?: WebLink[];
  chats?: AgentChat[];
  github?: GithubRef[];
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
  actorLabel: string;
  operations: Operation[];
  attachments: Attachment[];
};
export type Settings = {
  timezone: string;
  today: string;
  version: string;
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
  }[];
};
export type List<T> = {
  items: T[];
  total: number;
  collectionRevision?: number;
};
