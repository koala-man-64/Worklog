import type { AgentRun, RepositoryContext } from "./repositoryTypes";

type Titled = { id: string; title: string; archived: boolean };
export type TieReason =
  "participation" | "role" | "update" | "message" | "run" | "chat";
type Tie = { via: TieReason[]; current: boolean; lastAt: string | null };
export type Ties<T> = { items: (T & Tie)[]; total: number };
export type RegistryWork = {
  participationId: string;
  runId: string;
  runName: string;
  description: string;
  lastSeenAt: string;
  stale: boolean;
  item: (Titled & { status: string }) | null;
  roadmap: Titled | null;
  contexts: RepositoryContext[];
};
export type RegistryRole = {
  id: string;
  label: string;
  contextType: "item" | "roadmap" | "step";
  contextId: string;
  contextTitle: string;
  roadmap: Titled | null;
  updatedAt: string;
};
export type AgentSummary = {
  id: string;
  label: string;
  active: boolean;
  createdAt: string;
  lastActivityAt: string | null;
  providers: string[];
  openRuns: number;
  runCount: number;
  messageCount: number;
  working: RegistryWork[];
  roles: RegistryRole[];
};
export type TrackedChat = {
  id: string;
  chatId: string;
  title: string;
  provider: string;
  model: string;
  archived: boolean;
  ownerType: "item" | "roadmap";
  ownerId: string;
  ownerTitle: string;
};
export type AgentDetail = AgentSummary & {
  credentials: {
    id: string;
    label: string;
    createdAt: string;
    revoked: boolean;
    registeredBy: { id: string; label: string } | null;
  }[];
  runs: { items: (AgentRun & { chats: TrackedChat[] })[]; total: number };
  workItems: Ties<Titled & { status: string; roadmap: Titled | null }>;
  roadmaps: Ties<Titled>;
  repositories: Ties<{ id: string; name: string; archived: boolean }>;
  chats: Ties<TrackedChat>;
};
export type AgentActivity =
  | {
      kind: "message";
      id: string;
      createdAt: string;
      text: string;
      truncated: boolean;
      discussionId: string;
      discussionTitle: string;
      chatId: string | null;
      chats: { id: string; title: string }[];
    }
  | {
      kind: "update";
      id: string;
      createdAt: string;
      text: string;
      truncated: boolean;
      updateKind: "update" | "blocker" | "handoff";
      runName: string;
      participationId: string;
      item: { id: string; title: string | null } | null;
      contexts: RepositoryContext[];
    };
