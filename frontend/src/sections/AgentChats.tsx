import { useId, useState } from "react";
import { RepositoryBadges, RepositoryPicker } from "../RepositoryControls";
import { commands } from "../commands";
import { useWrites } from "../hooks";
import type { AgentChat, Step } from "../apiTypes";
import { ErrorMessage } from "../ui/Messages";
// Common providers seed the suggestions; providers and models already used join them.
const PROVIDERS = ["Anthropic", "OpenAI", "Google"];
// A chat links registered repositories. Its free-text labels, from before schema v24, are read
// only (K8), so the form neither shows nor sends them.
type ChatFields = {
  repositoryIds?: string[];
  title: string;
  chatId: string;
  provider: string;
  model: string;
  createdDate?: string;
};
function agentName(chat: AgentChat) {
  return [chat.provider, chat.model].filter(Boolean).join(" · ");
}
function ChatForm({
  label,
  submit,
  chats,
  initial,
  disabled = false,
  onSave,
  onCancel,
}: {
  label: string;
  submit: string;
  chats: AgentChat[];
  initial?: AgentChat;
  disabled?: boolean;
  onSave: (fields: ChatFields) => Promise<unknown>;
  onCancel?: () => void;
}) {
  const id = useId();
  const [repositoryIds, setRepositoryIds] = useState<string[]>(
    initial?.repositoryIds ?? [],
  );
  const blank = {
    title: "",
    chatId: "",
    provider: "",
    model: "",
    createdDate: "",
  };
  const [values, setValues] = useState(() =>
    initial
      ? {
          title: initial.title,
          chatId: initial.chatId,
          provider: initial.provider,
          model: initial.model,
          createdDate: initial.createdDate,
        }
      : blank,
  );
  const field = (name: keyof typeof blank) => ({
    value: values[name],
    disabled,
    onChange: (e: { target: { value: string } }) =>
      setValues({ ...values, [name]: e.target.value }),
  });
  const providers = [
    ...new Set([...PROVIDERS, ...chats.map((c) => c.provider)]),
  ].filter(Boolean);
  const models = [...new Set(chats.map((c) => c.model))].filter(Boolean);
  const ready = !!values.title.trim() && !!values.chatId.trim();
  return (
    <form
      className="inline chat-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!ready || disabled) return;
        await onSave({
          title: values.title.trim(),
          chatId: values.chatId.trim(),
          provider: values.provider.trim(),
          model: values.model.trim(),
          ...(repositoryIds.length || initial?.repositoryIds?.length
            ? { repositoryIds }
            : {}),
          ...(values.createdDate ? { createdDate: values.createdDate } : {}),
        })
          .then(() => {
            if (!initial) {
              setValues(blank);
              setRepositoryIds([]);
            }
          })
          .catch(() => undefined);
      }}
    >
      <input
        aria-label={label + " title"}
        placeholder="Chat title…"
        required
        {...field("title")}
      />
      <input
        aria-label={label + " ID"}
        placeholder="Chat ID…"
        required
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        {...field("chatId")}
      />
      <input
        aria-label={label + " provider"}
        placeholder="AI provider…"
        list={id + "-providers"}
        {...field("provider")}
      />
      <input
        aria-label={label + " model"}
        placeholder="Model…"
        list={id + "-models"}
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        {...field("model")}
      />
      <RepositoryPicker
        label={label + " registered repositories"}
        folded
        value={repositoryIds}
        onChange={setRepositoryIds}
        disabled={disabled}
      />
      <input
        aria-label={label + " date created"}
        type="date"
        {...field("createdDate")}
      />
      <datalist id={id + "-providers"}>
        {providers.map((p) => (
          <option key={p} value={p} />
        ))}
      </datalist>
      <datalist id={id + "-models"}>
        {models.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <button type="submit" disabled={disabled || !ready}>
        {submit}
      </button>
      {onCancel && (
        <button type="button" className="quiet" onClick={onCancel}>
          Cancel
        </button>
      )}
    </form>
  );
}
/** Who a chat is: its title, provider and model, chat ID, repositories, and the steps it works on, with a link to its messages. */
function ChatSummary({
  chat,
  steps = [],
  roadmapId,
}: {
  chat: AgentChat;
  steps?: Step[];
  roadmapId?: string;
}) {
  const agent = agentName(chat);
  const working = steps.filter((s) => chat.stepIds.includes(s.id));
  return (
    <div className="chat-summary">
      <div className="chat-row">
        <strong>{chat.title}</strong>
        {agent && <span className="person-badge">{agent}</span>}
        <a href={"#chats/" + chat.id} aria-label={"Messages of " + chat.title}>
          Messages
        </a>
      </div>
      <div className="chat-row">
        <code>{chat.chatId}</code>
        <RepositoryBadges ids={chat.repositoryIds} legacy={chat.repos} />
        <small>Created {chat.createdDate}</small>
      </div>
      {working.length > 0 && (
        <div className="chat-row">
          <small>Working on</small>
          {working.map((s) => (
            <a key={s.id} href={`#roadmaps/${roadmapId}/${s.id}`}>
              {s.title}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
function CopyChatId({ chat }: { chat: AgentChat }) {
  return (
    <button
      type="button"
      aria-label={"Copy ID of " + chat.title}
      onClick={() => navigator.clipboard.writeText(chat.chatId)}
    >
      Copy ID
    </button>
  );
}
export function AgentChatsSection({
  ownerType,
  ownerId,
  chats = [],
  steps,
  disabled = false,
}: {
  ownerType: "item" | "roadmap";
  ownerId: string;
  chats?: AgentChat[];
  // A roadmap's active steps, to show what each chat is working on.
  steps?: Step[];
  disabled?: boolean;
}) {
  const action = useWrites();
  const [editing, setEditing] = useState<string | null>(null);
  const active = chats.filter((c) => !c.archived);
  return (
    <section>
      <h3>
        Agent chats <span className="count">{active.length}</span>
      </h3>
      {active.map((c) =>
        editing === c.id && !disabled ? (
          <div className="line" key={c.id}>
            <ChatForm
              label="Edited chat"
              submit="Save chat"
              chats={active}
              initial={c}
              onSave={(fields) =>
                action
                  .mutateAsync(() =>
                    commands.updateChat(c.id, {
                      revision: c.revision,
                      ...fields,
                    }),
                  )
                  .then(() => setEditing(null))
              }
              onCancel={() => setEditing(null)}
            />
          </div>
        ) : (
          <div className="line chat-line" key={c.id}>
            <ChatSummary chat={c} steps={steps} roadmapId={ownerId} />
            <CopyChatId chat={c} />
            {!disabled && (
              <>
                <button
                  type="button"
                  className="quiet"
                  aria-label={"Edit chat " + c.title}
                  onClick={() => setEditing(c.id)}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className="quiet"
                  aria-label={"Archive chat " + c.title}
                  onClick={() =>
                    action.mutate(() =>
                      commands.setArchived("chats", c.id, true, {
                        revision: c.revision,
                      }),
                    )
                  }
                >
                  Archive
                </button>
              </>
            )}
          </div>
        ),
      )}
      {!disabled && (
        <ChatForm
          label="Agent chat"
          submit="Add chat"
          chats={active}
          disabled={action.isPending}
          onSave={(fields) =>
            action.mutateAsync(() =>
              commands.createChat({ ownerType, ownerId, ...fields }),
            )
          }
        />
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
/** The chats working on one roadmap step: assign the roadmap's chats or track a new one here. */
export function StepAgentChats({
  roadmapId,
  step,
  steps,
  chats = [],
  disabled = false,
}: {
  roadmapId: string;
  step: Step;
  // The roadmap's active steps, to find chats working on a parent of this one.
  steps: Step[];
  chats?: AgentChat[];
  disabled?: boolean;
}) {
  const action = useWrites();
  const [choice, setChoice] = useState("");
  const active = chats.filter((c) => !c.archived);
  const assigned = active.filter((c) => c.stepIds.includes(step.id));
  const available = active.filter((c) => !c.stepIds.includes(step.id));
  const ancestors: Step[] = [];
  let parent = steps.find((s) => s.id === step.parentId);
  while (parent && !ancestors.includes(parent)) {
    ancestors.push(parent);
    parent = steps.find((s) => s.id === parent?.parentId);
  }
  const inherited = available.flatMap((c) => {
    const through = ancestors.find((a) => c.stepIds.includes(a.id));
    return through ? [{ chat: c, through }] : [];
  });
  const assign = (chat: AgentChat, stepIds: string[]) =>
    action.mutateAsync(() =>
      commands.updateChat(chat.id, { revision: chat.revision, stepIds }),
    );
  return (
    <section>
      <h3>
        Agent chats <span className="count">{assigned.length}</span>
      </h3>
      {assigned.map((c) => (
        <div className="line chat-line" key={c.id}>
          <ChatSummary chat={c} />
          <CopyChatId chat={c} />
          {!disabled && (
            <button
              type="button"
              className="quiet"
              aria-label={"Unassign " + c.title}
              disabled={action.isPending}
              onClick={() =>
                assign(
                  c,
                  c.stepIds.filter((s) => s !== step.id),
                ).catch(() => undefined)
              }
            >
              Unassign
            </button>
          )}
        </div>
      ))}
      {!assigned.length && (
        <p className="muted">No agent chat is working on this step.</p>
      )}
      {inherited.map(({ chat, through }) => (
        <p className="muted" key={chat.id}>
          {chat.title}
          {agentName(chat) && ` (${agentName(chat)})`} is working on the parent
          step{" "}
          <a href={`#roadmaps/${roadmapId}/${through.id}`}>{through.title}</a>.
        </p>
      ))}
      {!disabled && available.length > 0 && (
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            const chat = available.find((c) => c.id === choice);
            if (!chat) return;
            await assign(chat, [...chat.stepIds, step.id])
              .then(() => setChoice(""))
              .catch(() => undefined);
          }}
        >
          <select
            aria-label="Agent chat to assign"
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
          >
            <option value="">Assign one of this roadmap’s chats…</option>
            {available.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
                {agentName(c) && ` (${agentName(c)})`}
              </option>
            ))}
          </select>
          <button type="submit" disabled={!choice || action.isPending}>
            Assign chat
          </button>
        </form>
      )}
      {!disabled && (
        <ChatForm
          label="Agent chat"
          submit="Add chat to step"
          chats={active}
          disabled={action.isPending}
          onSave={(fields) =>
            action.mutateAsync(() =>
              commands.createChat({
                ownerType: "roadmap",
                ownerId: roadmapId,
                ...fields,
                stepIds: [step.id],
              }),
            )
          }
        />
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
