import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AgentTies } from "./AgentTies";
import { getDataContext } from "./api";
import { Avatar } from "./Avatar";
import { Empty, ErrorMessage, Markdown } from "./components";
import { ContextReference } from "./discussionContexts";
import { Composer } from "./DiscussionComposer";
import { useAction, useData } from "./hooks";
import {
  exactDiscussionDate,
  formatDiscussionDate,
} from "./discussionFormatting";
import type { ChatOverview } from "./types";
import type {
  AcknowledgementMode,
  AcknowledgementRequest,
  DiscussionDetail,
  DiscussionMessage,
  DiscussionParticipants,
  DiscussionRole,
  DiscussionSummary,
} from "./discussionTypes";

const PAGE = 50;
const REFRESH_MS = 30_000;

export function DiscussionThread({
  id,
  summary,
  participants,
  roles,
  chats,
}: {
  id: string;
  summary?: DiscussionSummary;
  participants?: DiscussionParticipants;
  roles: DiscussionRole[];
  chats: ChatOverview[];
}) {
  const [offset, setOffset] = useState(0);
  const detail = useData<DiscussionDetail>(
    `/discussions/${id}?limit=${PAGE}&offset=${offset}`,
    REFRESH_MS,
  );
  const action = useAction();
  const queryClient = useQueryClient();
  const discussion = detail.data?.discussion ?? summary;
  const messages = detail.data?.messages ?? [];
  const total = detail.data?.total ?? 0;
  const [releaseMessageId, setReleaseMessageId] = useState<string | null>(null);
  const [releaseReason, setReleaseReason] = useState("");

  async function archive(archived: boolean) {
    if (!discussion) return;
    await action
      .mutateAsync({
        path: `/discussions/${id}/archive`,
        body: { revision: discussion.revision, archived },
      })
      .catch(() => undefined);
  }

  async function reply(body: Record<string, unknown>) {
    await action.mutateAsync({ path: `/discussions/${id}/messages`, body });
  }

  function refreshDetail() {
    void queryClient.invalidateQueries({
      queryKey: [
        getDataContext(),
        `/discussions/${id}?limit=${PAGE}&offset=${offset}`,
      ],
    });
  }

  if (detail.isPending) return <p>Loading discussion…</p>;
  if (detail.error && !detail.data)
    return (
      <>
        <ErrorMessage error={detail.error} />
        <p>This discussion could not be loaded.</p>
      </>
    );
  if (!discussion) return <Empty>This discussion is not available.</Empty>;
  const maxOffset = Math.max(0, Math.ceil(total / PAGE) - 1) * PAGE;

  return (
    <>
      <div className="discussion-detail-head">
        <div>
          <h2>{discussion.title || "Untitled discussion"}</h2>
          <small className="muted">
            Started {when(discussion.createdAt)} · updated{" "}
            {when(discussion.updatedAt)}
          </small>
          <div className="discussion-contexts" aria-label="Discussion context">
            {discussion.archived && (
              <span className="discussion-archived-label">
                Archived · replies are open
              </span>
            )}
            {discussion.contexts.map((context) => (
              <ContextReference
                key={context.type + context.id}
                context={context}
              />
            ))}
            {discussion.repos.map((repo) => (
              <span className="repo-tag" key={repo}>
                {repo}
              </span>
            ))}
          </div>
        </div>
        <div className="toolbar compact">
          <button type="button" onClick={refreshDetail}>
            Refresh
          </button>
          <button
            type="button"
            onClick={() => void archive(!discussion.archived)}
            disabled={action.isPending}
          >
            {discussion.archived ? "Restore to All" : "Archive"}
          </button>
        </div>
      </div>
      <ErrorMessage error={detail.error || action.error} />
      <AgentTies
        targetType="discussion"
        targetId={discussion.id}
        layout="fold"
      />
      <div className="discussion-message-page">
        {total > PAGE && (
          <div className="discussion-pagination">
            <button
              type="button"
              disabled={offset <= 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              Previous messages
            </button>
            <small>
              {offset + 1} to {Math.min(offset + messages.length, total)} of{" "}
              {total}
            </small>
            <button
              type="button"
              disabled={offset >= maxOffset}
              onClick={() => setOffset(Math.min(offset + PAGE, maxOffset))}
            >
              Next messages
            </button>
          </div>
        )}
        {!messages.length ? (
          <Empty>No messages in this thread yet.</Empty>
        ) : (
          <ol className="discussion-messages" aria-label="Discussion messages">
            {messages.map((message) => (
              <DiscussionMessageView
                key={message.id}
                message={message}
                participants={participants}
                roles={roles}
                chat={chats.find(
                  (candidate) => candidate.chatId === message.chatId,
                )}
                onAcknowledge={async (requestState) => {
                  const body =
                    requestState.mode === "any_agent" ||
                    requestState.mode === "anyone"
                      ? {}
                      : { targetIds: requestState.actionableTargetIds };
                  await action.mutateAsync({
                    path: `/discussions/messages/${message.id}/acknowledge`,
                    body,
                  });
                }}
                onCancel={async () => {
                  await action.mutateAsync({
                    path: `/discussions/messages/${message.id}/cancel`,
                    body: {},
                  });
                }}
                onRelease={() => {
                  setReleaseMessageId(message.id);
                  setReleaseReason("");
                }}
                onCancelRelease={() => {
                  setReleaseMessageId(null);
                  setReleaseReason("");
                }}
                onReleaseSubmit={async (reason) => {
                  await action.mutateAsync({
                    path: `/discussions/messages/${message.id}/release`,
                    body: { reason },
                  });
                  setReleaseMessageId(null);
                  setReleaseReason("");
                }}
                releaseOpen={releaseMessageId === message.id}
                releaseReason={releaseReason}
                onReleaseReason={setReleaseReason}
                disabled={action.isPending}
              />
            ))}
          </ol>
        )}
      </div>
      <div className="discussion-reply">
        <h3>Reply</h3>
        <p className="muted">
          A reply never counts as an acknowledgement. Any wait remains separate
          until someone explicitly acknowledges, cancels, or releases it.
        </p>
        <Composer
          key={`reply-${id}`}
          scope={`reply-${id}`}
          mode="reply"
          participants={participants}
          roles={roles}
          chats={chats}
          onSubmit={reply}
        />
      </div>
    </>
  );
}

function DiscussionMessageView({
  message,
  participants,
  roles,
  chat,
  onAcknowledge,
  onCancel,
  onRelease,
  onCancelRelease,
  onReleaseSubmit,
  releaseOpen,
  releaseReason,
  onReleaseReason,
  disabled,
}: {
  message: DiscussionMessage;
  participants?: DiscussionParticipants;
  roles: DiscussionRole[];
  chat?: ChatOverview;
  onAcknowledge: (request: AcknowledgementRequest) => Promise<void>;
  onCancel: () => Promise<void>;
  onRelease: () => void;
  onCancelRelease: () => void;
  onReleaseSubmit: (reason: string) => Promise<void>;
  releaseOpen: boolean;
  releaseReason: string;
  onReleaseReason: (reason: string) => void;
  disabled: boolean;
}) {
  const [error, setError] = useState<unknown>(null);
  const requestState = message.request;
  const messageRole = roles.find(
    (role) => role.id === message.onBehalfOfRoleId,
  );
  const author = participants?.items.find(
    (participant) => participant.id === message.authorId,
  );
  const audience = requestState ? audienceLabel(requestState.mode) : "";

  async function run(action: () => Promise<void>) {
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(failure);
    }
  }

  return (
    <li className="discussion-message">
      <div className="discussion-message-head">
        <Avatar name={author?.avatar} kind={message.authorKind} />
        <strong>{message.authorLabel}</strong>
        <span className="person-badge">{message.authorKind}</span>
        {messageRole && (
          <span className="muted">on behalf of {messageRole.label}</span>
        )}
        <time
          dateTime={message.createdAt}
          title={exactDiscussionDate(message.createdAt)}
        >
          {when(message.createdAt)}
        </time>
      </div>
      <Markdown text={message.text} untrusted />
      {message.chatId && (
        <p className="discussion-chat-reference">
          Agent chat:{" "}
          {chat ? (
            <a href={`#chats/${chat.id}`}>{chat.title}</a>
          ) : (
            <code>{message.chatId}</code>
          )}
        </p>
      )}
      {requestState && (
        <section
          className={"acknowledgement-card " + requestState.state}
          aria-label="Acknowledgement request"
        >
          <div className="acknowledgement-head">
            <strong>Received and understood</strong>
            <span className={`ack-state ${requestState.state}`}>
              {requestState.state === "pending"
                ? "Awaiting"
                : requestState.state === "satisfied"
                  ? "Satisfied"
                  : requestState.state === "released_by_human"
                    ? "Released by human"
                    : "Cancelled"}
            </span>
          </div>
          <p className="muted">{audience}</p>
          {requestState.targets.length > 0 && (
            <ul className="acknowledgement-targets">
              {requestState.targets.map((target) => (
                <li
                  key={target.id}
                  className={target.acknowledged ? "received" : undefined}
                >
                  <span>{target.label}</span>
                  <span>
                    {target.acknowledged
                      ? `Received by ${participants?.items.find((participant) => participant.id === target.acknowledgedBy)?.label ?? target.acknowledgedBy ?? target.label}`
                      : target.kind.startsWith("open_")
                        ? "Open recipient"
                        : target.kind === "role" && !target.ownerId
                          ? "Unassigned role"
                          : "Waiting"}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {requestState.receipts.length > 0 && (
            <ul
              className="acknowledgement-receipts"
              aria-label="Acknowledgement receipts"
            >
              {requestState.receipts.map((receipt) => (
                <li key={receipt.id}>
                  <span>{receipt.actorLabel} acknowledged</span>
                  <time
                    dateTime={receipt.createdAt}
                    title={exactDiscussionDate(receipt.createdAt)}
                  >
                    {when(receipt.createdAt)}
                  </time>
                  {receipt.late && (
                    <small className="muted">after the wait ended</small>
                  )}
                  {receipt.roleId && (
                    <small className="muted">
                      via{" "}
                      {roles.find((role) => role.id === receipt.roleId)
                        ?.label ?? "role"}
                    </small>
                  )}
                </li>
              ))}
            </ul>
          )}
          {requestState.reason && (
            <p className="acknowledgement-reason">
              {requestState.terminalActorLabel ?? "Human"} released the wait:{" "}
              {requestState.reason}
            </p>
          )}
          {requestState.state === "cancelled" &&
            requestState.terminalActorLabel && (
              <p className="muted">
                Cancelled by {requestState.terminalActorLabel}.
              </p>
            )}
          {requestState.state === "pending" &&
            requestState.actionableTargetIds.length > 0 && (
              <button
                type="button"
                className="primary"
                disabled={disabled}
                onClick={() => void run(() => onAcknowledge(requestState))}
              >
                Acknowledge: received and understood
              </button>
            )}
          {requestState.state === "pending" && requestState.canCancel && (
            <button
              type="button"
              disabled={disabled}
              onClick={() => void run(onCancel)}
            >
              Cancel my request
            </button>
          )}
          {requestState.state === "pending" &&
            requestState.canRelease &&
            (releaseOpen ? (
              <form
                className="release-request-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (releaseReason.trim())
                    void run(() => onReleaseSubmit(releaseReason.trim()));
                }}
              >
                <label>
                  Reason for releasing this wait
                  <textarea
                    value={releaseReason}
                    onChange={(event) => onReleaseReason(event.target.value)}
                    required
                    maxLength={2000}
                  />
                </label>
                <button disabled={disabled || !releaseReason.trim()}>
                  Release wait
                </button>
                <button
                  type="button"
                  className="quiet"
                  onClick={onCancelRelease}
                >
                  Cancel
                </button>
              </form>
            ) : (
              <button type="button" onClick={onRelease}>
                Release wait…
              </button>
            ))}
          <ErrorMessage error={error} />
        </section>
      )}
    </li>
  );
}

function audienceLabel(mode: AcknowledgementMode) {
  if (mode === "all") return "All named recipients must acknowledge.";
  if (mode === "any") return "Any one named recipient may acknowledge.";
  if (mode === "any_agent") return "Any other agent may acknowledge.";
  return "Anyone else (agent or human) may acknowledge.";
}

function when(iso: string | null | undefined) {
  return formatDiscussionDate(iso);
}
