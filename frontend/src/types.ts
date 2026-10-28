export type Status = "open" | "doing" | "blocked" | "done";
export type RecordBase = {
  id: string;
  revision: number;
  archived?: boolean;
  createdAt?: string;
  updatedAt?: string;
};
export type Item = RecordBase & {
  isBacklog?: boolean;
  title: string;
  description: string;
  status: Status;
  adoUrl: string | null;
  stepId: string | null;
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
export type Detail = Item & {
  linkedStep?: {
    id: string;
    title: string;
    roadmapId: string;
    roadmapTitle: string;
  } | null;
  notes: Note[];
  tasks: Task[];
  attachments: Attachment[];
  references: Reference[];
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
  history: unknown[];
  imports?: { id: string; name: string; sha256: string; createdAt: string }[];
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
