import type { RecordBase } from "./types";

export type Repository = RecordBase & {
  name: string;
  url: string;
  description: string;
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
