/** An agent a message sent now would reach: an open run that reported the chat's ID. */
export type OutboxRecipient = {
  runId: string;
  runName: string;
  provider: string;
  startedAt: string;
  agentId: string;
  agentLabel: string;
  avatar: string | null;
};
/** Why a delivery ended without the agent receiving the message. */
export type DeliveryReason =
  | "run_ended"
  | "human_closed"
  | "credential_revoked"
  | "credential_rotated"
  | "credential_retired"
  | "chat_archived"
  | "chat_changed"
  | "restore"
  | "not_picked_up";
/** One recipient's copy of a message, as it stands now. */
export type OutboxDelivery = {
  runId: string;
  runName: string;
  agentId: string;
  agentLabel: string;
  avatar: string | null;
  status: "pending" | "delivered" | "expired";
  reason: DeliveryReason | null;
  expiresAt: string;
  deliveredAt: string | null;
  expiredAt: string | null;
};
/** A message the person wrote into a chat for its agents. */
export type OutboxMessage = {
  id: string;
  chatId: string;
  text: string;
  authorId: string;
  authorLabel: string;
  createdAt: string;
  deliveries: OutboxDelivery[];
};
/** A chat's messages, newest first, with who a new message would reach. */
export type Outbox = {
  chat: { id: string; chatId: string; archived: boolean };
  recipients: OutboxRecipient[];
  items: OutboxMessage[];
  total: number;
  limit: number;
  offset: number;
};
