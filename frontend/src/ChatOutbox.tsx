import { useRef, useState, type FormEvent } from "react";
import { contextStorageKey } from "./api";
import { Avatar } from "./Avatar";
import { ErrorMessage, Markdown } from "./components";
import { useAction, useData } from "./hooks";
import { localStore } from "./storage";
import type {
  DeliveryReason,
  Outbox,
  OutboxDelivery,
  OutboxRecipient,
  OutboxRun,
} from "./outboxTypes";

const PAGE = 20;
// The server's page limit: the most messages one request returns.
const MOST = 200;
const REASONS: Record<DeliveryReason, string> = {
  run_ended: "the run ended",
  human_closed: "you ended the run",
  credential_revoked: "its credential was revoked",
  credential_rotated: "its credential was replaced",
  credential_retired: "its agent retired",
  chat_archived: "the chat was archived",
  chat_changed: "the chat's ID changed",
  restore: "Worklog was restored from a backup",
  unconfirmed: "its agent isn't confirmed in this chat",
  not_picked_up: "not picked up within 2 hours",
};

export function deliveryState(
  delivery: OutboxDelivery,
  time: (iso: string | null) => string,
) {
  if (delivery.status === "delivered")
    return `Delivered ${time(delivery.deliveredAt)}`;
  if (delivery.status === "pending") return "Waiting for pickup";
  // A reason this version doesn't know, from a newer one, reads as plain expiry.
  return (
    "Not delivered: " +
    ((delivery.reason && REASONS[delivery.reason]) || "it expired")
  );
}

/** Where an agent identity comes from, which its free-text label cannot show. */
function origin(run: OutboxRun, binding?: OutboxRecipient["binding"]) {
  if (run.registeredBy)
    return `registered by ${run.registeredBy.label || "an agent"}`;
  return binding === "registered"
    ? "registered with this chat"
    : "issued in Settings";
}

/** One run on the chat: its agent, the run, and where the agent comes from. */
function RunName({ run, alike }: { run: OutboxRun; alike: boolean }) {
  return (
    <>
      <Avatar name={run.avatar} size={18} />
      {run.agentLabel}
      {/* Two identities under one label: their IDs tell them apart. */}
      {alike && <small>· id {run.agentId.slice(0, 8)}</small>}
      <small>· {run.runName}</small>
    </>
  );
}

/** The unsent message for one chat, kept in this browser across reloads. */
function useChatDraft(chatId: string) {
  const key = contextStorageKey("chat-message-draft", chatId);
  const [text, setText] = useState(() => localStore.read(key).value ?? "");
  const [storageWarning, setStorageWarning] = useState(
    () => !localStore.read(key).ok,
  );
  const version = useRef(0);
  return {
    text,
    storageWarning,
    version,
    set(value: string) {
      version.current += 1;
      setText(value);
      setStorageWarning(
        !(value ? localStore.write(key, value) : localStore.remove(key)),
      );
    },
    clear() {
      version.current += 1;
      setText("");
      setStorageWarning(!localStore.remove(key));
    },
  };
}

/**
 * Messages the person writes into a chat for the agents working in it. An agent
 * bound to this chat, because it registered with it or the person confirmed it
 * here, receives each one once through `worklog agent inbox` while it has an
 * open run that reported this chat's ID. Any other run that reports the ID only
 * claims the chat, and receives nothing until the person confirms its agent. An
 * archived chat receives nothing.
 */
export function ChatOutbox({
  chat,
  refreshMs,
  time,
}: {
  chat: { id: string; chatId: string };
  refreshMs: number;
  time: (iso: string | null) => string;
}) {
  const [limit, setLimit] = useState(PAGE);
  const outbox = useData<Outbox>(
    `/chats/${chat.id}/messages?limit=${limit}`,
    refreshMs,
  );
  const send = useAction();
  const binding = useAction();
  const draft = useChatDraft(chat.id);
  const [error, setError] = useState<unknown>(null);
  const [bindingError, setBindingError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const data = outbox.data;
  const recipients = data?.recipients ?? [];
  const unconfirmed = data?.unconfirmed ?? [];
  // Labels that more than one identity on this chat goes by.
  const identities = new Map<string, Set<string>>();
  for (const run of [...recipients, ...unconfirmed])
    identities.set(
      run.agentLabel,
      (identities.get(run.agentLabel) ?? new Set()).add(run.agentId),
    );
  const alike = (run: OutboxRun) =>
    (identities.get(run.agentLabel)?.size ?? 0) > 1;
  const canSend =
    !!draft.text.trim() && recipients.length > 0 && !send.isPending;

  /** Confirm that an agent works in this chat, or remove that confirmation. */
  async function setConfirmation(agentId: string, confirmed: boolean) {
    setBindingError(null);
    try {
      await binding.mutateAsync({
        path: `/chats/${chat.id}/confirmations${confirmed ? "" : "/remove"}`,
        body: { agentId },
      });
    } catch (failure) {
      setBindingError(failure);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSend) return;
    setError(null);
    setNotice("");
    const sentVersion = draft.version.current;
    try {
      await send.mutateAsync({
        path: `/chats/${chat.id}/messages`,
        body: { text: draft.text.trim() },
      });
      if (draft.version.current === sentVersion) draft.clear();
      else
        setNotice(
          "Sent. Edits made while it was sending are still saved as a draft.",
        );
    } catch (failure) {
      setError(failure);
    }
  }

  return (
    <section className="chat-outbox" aria-labelledby={`outbox-${chat.id}`}>
      <h3 id={`outbox-${chat.id}`}>Message this chat’s agents</h3>
      <ErrorMessage error={outbox.error} />
      {!!data?.items.length && (
        <>
          {data.total > data.items.length && limit < MOST && (
            <button
              type="button"
              onClick={() => setLimit(Math.min(limit + PAGE, MOST))}
            >
              Show earlier messages you sent
            </button>
          )}
          <ol className="chat-outbox-sent" aria-label="Messages you sent">
            {[...data.items].reverse().map((message) => (
              <li key={message.id}>
                <small>You sent {time(message.createdAt)}</small>
                <Markdown text={message.text} />
                <ul className="chat-deliveries" aria-label="Deliveries">
                  {message.deliveries.map((d) => (
                    <li key={d.runId} className={d.status}>
                      <Avatar name={d.avatar} size={18} />
                      {d.agentLabel}: {deliveryState(d, time)}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </>
      )}
      {data?.chat.archived ? (
        <p className="muted">
          This chat is archived. Archived chats don’t receive messages, and
          Worklog never recovers a chat to deliver one.
        </p>
      ) : (
        <form className="chat-outbox-form" onSubmit={submit}>
          {!data ? (
            outbox.isPending && (
              <p className="muted">Checking who would receive it…</p>
            )
          ) : recipients.length || unconfirmed.length ? (
            <>
              {recipients.length ? (
                <div className="chat-recipients">
                  <span className="muted">Reaches</span>
                  <ul aria-label="Agents a message reaches">
                    {recipients.map((r) => (
                      <li key={r.runId} title={`Run: ${r.runName}`}>
                        <RunName run={r} alike={alike(r)} />
                        <small>· {origin(r, r.binding)}</small>
                        {r.binding === "confirmed" && (
                          <>
                            <small>· confirmed by you</small>
                            <button
                              type="button"
                              aria-label={`Remove your confirmation of ${r.agentLabel}`}
                              disabled={binding.isPending}
                              onClick={() => setConfirmation(r.agentId, false)}
                            >
                              Remove
                            </button>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="muted">
                  No agent bound to this chat has an open run, so a message
                  would reach no one yet.
                </p>
              )}
              {unconfirmed.length > 0 && (
                <>
                  <div className="chat-recipients">
                    <span className="muted">Not confirmed</span>
                    <ul aria-label="Agents waiting for your confirmation">
                      {unconfirmed.map((r) => (
                        <li
                          key={r.runId}
                          className="unconfirmed"
                          title={`Run: ${r.runName}, started ${time(r.startedAt)}`}
                        >
                          <RunName run={r} alike={alike(r)} />
                          <small>· {origin(r)}</small>
                          <button
                            type="button"
                            aria-label={`Confirm ${r.agentLabel} works in this chat`}
                            disabled={binding.isPending}
                            onClick={() => setConfirmation(r.agentId, true)}
                          >
                            Confirm
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <p className="muted">
                    These runs report this chat's ID, but their agents didn't
                    register with this chat, so they receive nothing. Confirm
                    an agent only if you know it works here.
                  </p>
                </>
              )}
              <ErrorMessage error={bindingError} />
            </>
          ) : (
            <p className="muted">
              No open agent run reports this chat's ID, so a message would reach
              no one. An agent ties itself to this chat with{" "}
              <code>worklog agent runs start --chat {chat.chatId}</code>.
            </p>
          )}
          <label>
            Message
            <textarea
              aria-label="Message to this chat's agents"
              value={draft.text}
              onChange={(event) => draft.set(event.target.value)}
              placeholder="Agents receive it the next time they run worklog agent inbox"
            />
          </label>
          {draft.storageWarning && (
            <p className="warning" role="status">
              Browser storage is unavailable; this draft is kept only while this
              page stays open.
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          <ErrorMessage error={error} />
          <div className="toolbar">
            <button className="primary" disabled={!canSend}>
              {send.isPending ? "Sending…" : "Send"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
