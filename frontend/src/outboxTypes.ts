/** An open agent run that reported the chat's ID. */
export type OutboxRun = {
  runId: string;
  runName: string;
  provider: string;
  startedAt: string;
  agentId: string;
  agentLabel: string;
  avatar: string | null;
  /**
   * The agent identity that registered this one; null for a credential issued
   * in Settings. Agents choose their own labels, so this tells two apart.
   */
  registeredBy: { id: string; label: string } | null;
};
/**
 * An agent a message sent now would reach: an open run of an identity bound to
 * the chat, because it registered with the chat or the person confirmed it here.
 */
export type OutboxRecipient = OutboxRun & {
  binding: "registered" | "confirmed";
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
  | "unconfirmed"
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
/**
 * A chat's messages, newest first, with who a new message would reach and the
 * runs that only claim the chat: they receive nothing until the person confirms
 * their agent.
 */
export type Outbox = {
  chat: { id: string; chatId: string; archived: boolean };
  recipients: OutboxRecipient[];
  unconfirmed: OutboxRun[];
  items: OutboxMessage[];
  total: number;
  limit: number;
  offset: number;
};
