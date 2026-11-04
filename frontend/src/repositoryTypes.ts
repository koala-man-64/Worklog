import type { PersonAssignment, RecordBase } from "./types";

export type Repository = RecordBase & {
  name: string;
  url: string;
  /** Host and path of the remote, shared by all of its addresses. */
  remoteIdentity: string;
  /** Set when another record already holds this remote. */
  duplicateOf: string | null;
  /** Set when a person merged this record into another, which now answers for it. */
  mergedInto: string | null;
  description: string;
  /** Who registered it; null when Worklog predates the record's history. */
  registeredBy: {
    kind: "human" | "agent";
    label: string;
    agentId: string | null;
  } | null;
  /** False until the person confirms or edits what an agent registered. */
  reviewed: boolean;
  /** The people tied to it, on the repository's own read only. */
  people?: PersonAssignment[];
};
/** Registering a remote that is already registered returns its record. */
export type RepositoryRegistration = Repository & { created: boolean };
/** What registering an address would do: link to `repository`, or add one when it is null. */
export type RepositoryResolution = {
  url: string;
  remoteIdentity: string;
  repository: Repository | null;
};
type CoordinationRecord = Pick<
  RecordBase,
  "id" | "revision" | "createdAt" | "updatedAt"
>;
export type AgentRun = CoordinationRecord & {
  agentId: string;
  credentialId: string;
  clientRunKey: string;
  displayName: string;
  provider: string;
  externalChatId: string | null;
  endedAt: string | null;
  endReason: string | null;
};
export type RepositoryContext = {
  repositoryId: string;
  repositoryName?: string;
  archived?: boolean;
  checkoutPath?: string;
  worktreePath?: string;
  branch?: string;
};
export type Participation = CoordinationRecord & {
  description: string;
  runId: string;
  itemId: string | null;
  contexts: RepositoryContext[];
  lastSeenAt: string;
  releasedAt: string | null;
  releaseReason: string | null;
  stale: boolean;
  run: AgentRun;
};
export type CoordinationUpdate = {
  id: string;
  participationId: string;
  runId: string;
  authorLabel: string;
  kind: "update" | "blocker" | "handoff";
  text: string;
  itemId: string | null;
  contexts: RepositoryContext[];
  createdAt: string;
};
