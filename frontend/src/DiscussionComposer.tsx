import { useEffect, useRef, useState, type FormEvent } from "react";
import { contextStorageKey } from "./api";
import { ErrorMessage } from "./components";
import {
  useContextSearch,
  ContextPicker,
  type ContextChoice,
} from "./discussionContexts";
import { localStore } from "./storage";
import type { ChatOverview } from "./types";
import type {
  AcknowledgementInputTarget,
  AcknowledgementMode,
  DiscussionParticipants,
  DiscussionRole,
} from "./discussionTypes";

type Draft = {
  title: string;
  text: string;
  contexts: ContextChoice[];
  chatId: string;
  askForAcknowledgement: boolean;
  acknowledgementMode: AcknowledgementMode;
  acknowledgementTargets: AcknowledgementInputTarget[];
};

const emptyDraft: Draft = {
  title: "",
  text: "",
  contexts: [],
  chatId: "",
  askForAcknowledgement: false,
  acknowledgementMode: "any_agent",
  acknowledgementTargets: [],
};

function readDraft(key: string) {
  const stored = localStore.read(key);
  if (!stored.value) return { value: emptyDraft, warning: !stored.ok };
  try {
    const parsed = JSON.parse(stored.value) as Partial<Draft>;
    return {
      value: {
        ...emptyDraft,
        ...parsed,
        contexts: Array.isArray(parsed.contexts) ? parsed.contexts : [],
        acknowledgementTargets: Array.isArray(parsed.acknowledgementTargets)
          ? parsed.acknowledgementTargets
          : [],
      },
      warning: !stored.ok,
    };
  } catch {
    return { value: emptyDraft, warning: !stored.ok };
  }
}

function useDiscussionDraft(scope: string) {
  const key = contextStorageKey("discussion-draft", scope);
  const [draft, setDraft] = useState(() => readDraft(key).value);
  const [storageWarning, setStorageWarning] = useState(
    () => readDraft(key).warning,
  );
  const [dirty, setDirty] = useState(false);
  const version = useRef(0);

  useEffect(() => {
    if (!dirty) return;
    setStorageWarning(!localStore.write(key, JSON.stringify(draft)));
  }, [dirty, draft, key]);

  function update<K extends keyof Draft>(name: K, value: Draft[K]) {
    version.current += 1;
    setDraft((current) => ({ ...current, [name]: value }));
    setDirty(true);
  }

  function clear() {
    version.current += 1;
    localStore.remove(key);
    setDraft(emptyDraft);
    setDirty(false);
    setStorageWarning(false);
  }

  return { draft, update, clear, storageWarning, version };
}

function titleFromText(text: string) {
  return text.split(/\r?\n/)[0].trim().slice(0, 120) || "Untitled discussion";
}

export function Composer({
  scope,
  mode,
  participants,
  roles,
  chats,
  onSubmit,
}: {
  scope: string;
  mode: "new" | "reply";
  participants?: DiscussionParticipants;
  roles: DiscussionRole[];
  chats: ChatOverview[];
  onSubmit: (body: Record<string, unknown>) => Promise<unknown>;
}) {
  const { draft, update, clear, storageWarning, version } =
    useDiscussionDraft(scope);
  const [error, setError] = useState<unknown>(null);
  const [isPending, setIsPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [contextQuery, setContextQuery] = useState("");
  const contextSearch = useContextSearch(contextQuery);
  const activeRecipients = (participants?.items ?? []).filter(
    (participant) =>
      participant.active && participant.id !== participants?.self.id,
  );
  const recipientRoles = roles.filter(
    (role) => role.ownerId !== participants?.self.id,
  );
  const namedMode =
    draft.acknowledgementMode === "all" || draft.acknowledgementMode === "any";

  function toggleRecipient(target: AcknowledgementInputTarget) {
    const key = target.kind + ":" + target.id;
    const selected = new Set(
      draft.acknowledgementTargets.map((entry) => entry.kind + ":" + entry.id),
    );
    if (selected.has(key)) selected.delete(key);
    else selected.add(key);
    const options = [
      ...activeRecipients.map((participant) => ({
        kind: participant.kind,
        id: participant.id,
        label: participant.label,
      })),
      ...recipientRoles.map((role) => ({
        kind: "role" as const,
        id: role.id,
        label: roleRecipientLabel(role, participants),
      })),
    ];
    const visibleKeys = new Set(
      options.map((option) => option.kind + ":" + option.id),
    );
    update("acknowledgementTargets", [
      ...draft.acknowledgementTargets.filter((entry) => {
        const entryKey = entry.kind + ":" + entry.id;
        return !visibleKeys.has(entryKey) && selected.has(entryKey);
      }),
      ...options.filter((option) =>
        selected.has(option.kind + ":" + option.id),
      ),
    ]);
  }

  function targetSelected(target: AcknowledgementInputTarget) {
    return draft.acknowledgementTargets.some(
      (entry) => entry.kind === target.kind && entry.id === target.id,
    );
  }

  function targetLabel(target: AcknowledgementInputTarget) {
    if (target.kind === "role")
      return (
        target.label ??
        roles.find((role) => role.id === target.id)?.label ??
        "Role"
      );
    return (
      participants?.items.find((participant) => participant.id === target.id)
        ?.label ??
      target.label ??
      "Participant"
    );
  }

  function buildBody(): Record<string, unknown> {
    const body: Record<string, unknown> = {
      text: draft.text.trim(),
      chatId: draft.chatId || null,
      acknowledgement: draft.askForAcknowledgement
        ? {
            mode: draft.acknowledgementMode,
            targets: namedMode
              ? draft.acknowledgementTargets.map(({ kind, id }) => ({
                  kind,
                  id,
                }))
              : [],
          }
        : null,
    };
    if (mode === "new") {
      body.title = draft.title.trim() || titleFromText(draft.text);
      // A new thread takes no free-text repository labels (schema v21): it is tagged instead.
      body.contexts = draft.contexts.map(({ type, id }) => ({ type, id }));
    }
    return body;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.text.trim()) return;
    setError(null);
    setNotice("");
    setIsPending(true);
    const submittedVersion = version.current;
    try {
      await onSubmit(buildBody());
      if (version.current === submittedVersion) clear();
      else
        setNotice(
          "Sent. Edits made while it was sending are still saved as a draft.",
        );
    } catch (failure) {
      setError(failure);
    } finally {
      setIsPending(false);
    }
  }

  const eligibleTargets = [
    ...activeRecipients.map((participant) => ({
      kind: participant.kind,
      id: participant.id,
      label: participant.label,
    })),
    ...recipientRoles.map((role) => ({
      kind: "role" as const,
      id: role.id,
      label: roleRecipientLabel(role, participants),
    })),
  ];
  const canSubmit =
    !!draft.text.trim() &&
    !isPending &&
    (!draft.askForAcknowledgement ||
      !namedMode ||
      draft.acknowledgementTargets.length > 0);

  return (
    <form className="discussion-composer" onSubmit={submit}>
      {mode === "new" && (
        <label>
          Title (optional)
          <input
            value={draft.title}
            onChange={(event) => update("title", event.target.value)}
            placeholder="Defaults to the first line of the post"
            maxLength={500}
          />
        </label>
      )}
      <label>
        {mode === "new" ? "Post" : "Reply"}
        <textarea
          aria-label={mode === "new" ? "Post message" : "Reply message"}
          autoFocus={mode === "new"}
          value={draft.text}
          onChange={(event) => update("text", event.target.value)}
          placeholder={
            mode === "new"
              ? "Share information or ask a question…"
              : "Write a reply…"
          }
          required
        />
      </label>
      {mode === "new" && (
        <div className="discussion-composer-scope">
          <ContextPicker
            label="Link existing context (optional)"
            query={contextQuery}
            onQueryChange={setContextQuery}
            selected={draft.contexts}
            onAdd={(choice) => {
              update("contexts", [...draft.contexts, choice]);
              setContextQuery("");
            }}
            onRemove={(choice) =>
              update(
                "contexts",
                draft.contexts.filter(
                  (context) =>
                    context.type !== choice.type || context.id !== choice.id,
                ),
              )
            }
            results={contextSearch.data?.items ?? []}
            loading={contextSearch.isFetching}
          />
        </div>
      )}
      <label>
        Related agent chat (optional)
        <select
          value={draft.chatId}
          onChange={(event) => update("chatId", event.target.value)}
        >
          <option value="">No linked agent chat</option>
          {chats.map((chat) => (
            <option key={chat.id} value={chat.chatId}>
              {chat.title} · {chat.chatId}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="acknowledgement-request-input">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={draft.askForAcknowledgement}
            onChange={(event) =>
              update("askForAcknowledgement", event.target.checked)
            }
          />
          Request acknowledgement (received and understood)
        </label>
        {draft.askForAcknowledgement && (
          <>
            <label>
              Who may acknowledge?
              <select
                value={draft.acknowledgementMode}
                onChange={(event) => {
                  update(
                    "acknowledgementMode",
                    event.target.value as AcknowledgementMode,
                  );
                  if (
                    event.target.value === "any_agent" ||
                    event.target.value === "anyone"
                  )
                    update("acknowledgementTargets", []);
                }}
              >
                <option value="any_agent">Any other agent</option>
                <option value="anyone">Anyone else (agent or human)</option>
                <option value="any">Any one named recipient</option>
                <option value="all">All named recipients</option>
              </select>
            </label>
            {namedMode && (
              <fieldset className="acknowledgement-recipient-list">
                <legend>Named recipients (the author is excluded)</legend>
                {!participants ? (
                  <p className="muted">Loading participants…</p>
                ) : eligibleTargets.length === 0 ? (
                  <p className="muted">
                    No other active participants or roles are available.
                  </p>
                ) : (
                  eligibleTargets.map((target) => (
                    <label
                      className="checkbox-label"
                      key={target.kind + target.id}
                    >
                      <input
                        type="checkbox"
                        checked={targetSelected(target)}
                        onChange={() => toggleRecipient(target)}
                      />
                      {target.label}
                      {target.kind === "role" && <small>Role</small>}
                    </label>
                  ))
                )}
                {draft.acknowledgementTargets.length > 0 && (
                  <p className="muted">
                    Selected:{" "}
                    {draft.acknowledgementTargets.map(targetLabel).join(", ")}
                  </p>
                )}
              </fieldset>
            )}
          </>
        )}
      </fieldset>
      {storageWarning && (
        <p className="warning" role="status">
          Browser storage is unavailable; this draft is kept only while this
          page stays open.
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <ErrorMessage error={error} />
      <div className="toolbar">
        <button className="primary" disabled={!canSubmit}>
          {isPending
            ? "Sending…"
            : mode === "new"
              ? "Post discussion"
              : "Send reply"}
        </button>
      </div>
    </form>
  );
}

function roleRecipientLabel(
  role: DiscussionRole,
  participants?: DiscussionParticipants,
) {
  const owner = participants?.items.find(
    (participant) => participant.id === role.ownerId,
  );
  const contextTypeLabel =
    role.contextType === "item"
      ? "work item"
      : role.contextType === "roadmap"
        ? "roadmap"
        : "step";
  const context =
    role.contextTitle || `${contextTypeLabel} ${role.contextId.slice(0, 8)}`;
  const ownership = !role.ownerId
    ? "unassigned"
    : owner?.active
      ? `owned by ${owner.label}`
      : `owner ${role.ownerLabel ?? role.ownerId.slice(0, 8)} inactive`;
  return `${role.label} (${context}, ${ownership})`;
}
