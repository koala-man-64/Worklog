import type { ItemStatus, Status } from "./types";

/** A release is planned until it ships or is called off. */
export type ReleaseStatus = "planned" | "released" | "cancelled";
/** Who needs to know (notify), what to test (test), or what to prepare before the release. */
export type CheckKind = "notify" | "test" | "prepare";
/** What a release holds: a work item, a roadmap, a roadmap step or a GitHub reference. */
export type ReleaseTarget = "item" | "roadmap" | "step" | "github";
export type Release = {
  id: string;
  title: string;
  description: string;
  /** A YYYY-MM-DD day, or null while the release is not dated. */
  releaseDate: string | null;
  status: ReleaseStatus;
  archived: boolean;
  revision: number;
  createdAt: string;
  updatedAt: string;
  /** Planned, with its date passed. */
  pastDue: boolean;
};
/** A list row's counts: active work items and the done ones, open checks of each kind, and open checks past due. */
export type ReleaseCounts = {
  items: number;
  itemsDone: number;
  notify: number;
  test: number;
  prepare: number;
  pastDue: number;
};
export type ReleaseRow = Release & { counts: ReleaseCounts };
export type ReleaseCheck = {
  id: string;
  releaseId: string;
  kind: CheckKind;
  text: string;
  /** Markdown. */
  details: string;
  /** Someone from People: who to tell, who tests, or who handles it. */
  personId: string | null;
  personName?: string | null;
  personArchived?: boolean;
  dueDate: string | null;
  status: ItemStatus;
  archived: boolean;
  revision: number;
  createdAt: string;
  updatedAt: string;
  /** Not done, with its due date passed. */
  pastDue: boolean;
};
export type ReleaseItem = {
  id: string;
  title: string;
  status: ItemStatus;
  archived: boolean;
  addedAt: string;
};
export type ReleaseRoadmap = {
  id: string;
  title: string;
  archived: boolean;
  addedAt: string;
  /** Its leaf steps, and how many of them are done, as its progress bar counts them. */
  steps: number;
  stepsDone: number;
};
export type ReleaseStep = {
  id: string;
  title: string;
  status: Status;
  retired: boolean;
  roadmapId: string;
  roadmapTitle: string;
  roadmapArchived: boolean;
  addedAt: string;
};
/** A PR or commit: one the release holds itself (direct), or one on a work item or roadmap it holds. */
export type ReleaseChange = {
  id: string;
  kind: "pr" | "commit";
  url: string;
  reference: string;
  title: string;
  ownerType: "item" | "roadmap";
  ownerId: string;
  ownerTitle: string | null;
  /** The tracked agent chat that made it. */
  chatId: string | null;
  chatTitle: string | null;
  provider: string | null;
  model: string | null;
  direct: boolean;
  archived: boolean;
  createdAt: string;
};
/** Who changed the release or one of its checks, and how, without the records: /history has those. */
export type ReleaseEvent = {
  id: string;
  entityId: string;
  kind: string;
  actorId: string;
  actorLabel: string;
  actorKind: "agent" | "human";
  createdAt: string;
};
export type ReleaseDetail = Release & {
  items: ReleaseItem[];
  roadmaps: ReleaseRoadmap[];
  steps: ReleaseStep[];
  changes: ReleaseChange[];
  /** Every check, archived ones too, by due date (undated last), then as added. */
  checks: ReleaseCheck[];
  /** The latest 100 changes to the release and its checks, newest first. */
  history: ReleaseEvent[];
};
