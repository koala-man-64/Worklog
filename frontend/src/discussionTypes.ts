/** What a discussion can be tagged with (backend discussions.TAG_KINDS); roles keep to the first three. */
export type DiscussionContextType =
  | "item"
  | "roadmap"
  | "step"
  | "repository"
  | "ticket"
  | "release"
  | "person"
  | "resource"
  | "question"
  | "decision";

export type DiscussionContext = {
  type: DiscussionContextType;
  id: string;
  /** The tagged record's title as Worklog found it, and what a page needs to link to it. */
  title?: string | null;
  archived?: boolean;
  roadmapId?: string;
  /** A repository's tag on its own discussion, which stays on it. */
  home?: boolean;
  /** Who added the tag; null for a work context a role brings in. */
  addedBy?: { id: string; label: string; kind: "human" | "agent" } | null;
  addedAt?: string | null;
};

export type DiscussionSummary = {
  id: string;
  title: string;
  archived: boolean;
  revision: number;
  createdAt: string;
  updatedAt: string;
  pinned?: boolean;
  pinnedAt?: string | null;
  contexts: DiscussionContext[];
  /** Free-text repository labels older threads carry; a new thread takes none. */
  repos: string[];
  pendingCount: number;
  /** On a repository's own discussion, the repository it is for. */
  repository?: { id: string; name: string } | null;
};

export type DiscussionList = {
  items: DiscussionSummary[];
  total: number;
};

export type DiscussionParticipant = {
  id: string;
  label: string;
  kind: "human" | "agent";
  active: boolean;
  /** Generated avatar name; null for the person. */
  avatar: string | null;
};

/** Every avatar, which ones active agents already show, and a fresh draw. */
export type AvatarCatalog = {
  items: { name: string; available: boolean }[];
  suggested: string;
  current: string | null;
};

export type DiscussionParticipants = {
  items: DiscussionParticipant[];
  self: Pick<DiscussionParticipant, "id" | "label" | "kind">;
};

export type AcknowledgementMode = "all" | "any" | "any_agent" | "anyone";
export type AcknowledgementState =
  "pending" | "satisfied" | "released_by_human" | "cancelled";

export type AcknowledgementInputTarget = {
  kind: "agent" | "human" | "role";
  id: string;
  label?: string;
};

export type AcknowledgementTarget = {
  id: string;
  kind: "agent" | "human" | "role" | "open_agent" | "open_any";
  recipientId: string | null;
  label: string;
  ownerId: string | null;
  ownershipGeneration: number | null;
  acknowledged: boolean;
  acknowledgedBy: string | null;
};

export type AcknowledgementReceipt = {
  id: string;
  targetId: string;
  actorId: string;
  actorLabel: string;
  actorKind: "human" | "agent";
  roleId: string | null;
  ownershipGeneration: number | null;
  createdAt: string;
  late: boolean;
};

export type AcknowledgementRequest = {
  id: string;
  mode: AcknowledgementMode;
  state: AcknowledgementState;
  targets: AcknowledgementTarget[];
  receipts: AcknowledgementReceipt[];
  actionableTargetIds: string[];
  canCancel: boolean;
  canRelease: boolean;
  classifications?: string[];
  reason?: string | null;
  terminalAt?: string | null;
  terminalActorId?: string | null;
  terminalActorLabel?: string | null;
  terminalActorKind?: "human" | "agent" | null;
  authorityRoleId?: string | null;
  authorityGeneration?: number | null;
};

export type DiscussionMessage = {
  id: string;
  discussionId: string;
  text: string;
  authorId: string;
  authorLabel: string;
  authorKind: "human" | "agent";
  createdAt: string;
  chatId: string | null;
  onBehalfOfRoleId: string | null;
  ownershipGeneration: number | null;
  request: AcknowledgementRequest | null;
};

export type DiscussionDetail = {
  discussion: DiscussionSummary;
  messages: DiscussionMessage[];
  total: number;
};

export type DiscussionRoleOffer = {
  id: string;
  fromId: string;
  toId: string;
  toLabel: string;
  note: string;
};

export type DiscussionRole = {
  id: string;
  contextType: DiscussionContextType;
  contextId: string;
  contextTitle?: string;
  label: string;
  ownerId: string | null;
  ownerLabel: string | null;
  revision: number;
  generation: number;
  offer: DiscussionRoleOffer | null;
};

export type DiscussionRoles = {
  items: DiscussionRole[];
  total: number;
};

export type DiscussionSearchResult = {
  id: string;
  type: string;
  title: string;
  snippet: string;
  ownerId?: string;
  routeType?: string;
  routeId?: string;
};

export type DiscussionSearchResults = {
  items: DiscussionSearchResult[];
  total: number;
};

export type DiscussionResume = {
  roles: DiscussionRole[];
  discussions: DiscussionSummary[];
  requests: DiscussionMessage[];
  cursor: string;
  counts: { roles: number; discussions: number; requests: number };
  limit: number;
  offset: number;
  hasMore: boolean;
};
