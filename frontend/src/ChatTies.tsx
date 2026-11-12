import { useState } from "react";
import { contextStorageKey } from "./api";
import { Avatar } from "./Avatar";
import { ErrorMessage } from "./ui/Messages";
import { statusLabel } from "./ui/Status";
import { commands } from "./commands";
import { useData, useWrites } from "./hooks";
import { Icon } from "./icons";
import { LinkPicker } from "./LinkPicker";
import { localStore, readIdSet } from "./storage";
import type {
  Answer,
  ChatAgent,
  ChatOverview,
  Item,
  Roadmap,
  RunningSession,
} from "./apiTypes";

// Where Claude Code runs a session, as its record names it.
const ENTRYPOINTS: Record<string, string> = {
  "claude-desktop": "Claude desktop",
  cli: "Terminal",
  "claude-vscode": "VS Code",
};
export function runsIn(entrypoint: string) {
  return (
    ENTRYPOINTS[entrypoint] ??
    (entrypoint.startsWith("sdk") ? "Agent SDK" : entrypoint || "Claude Code")
  );
}
// Sessions hidden in this browser, kept by ID; a session that ended long ago stays only until this many newer ones are hidden.
const HIDDEN_LIMIT = 200;
const BINDINGS: Record<ChatAgent["binding"], string> = {
  registered: "registered with this chat",
  run: "reports this chat",
};
/** The last folder of a path, Windows or not. */
function folderName(path: string) {
  return (
    path
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .pop() ?? ""
  );
}
/** What a running session is doing, as Claude Code last said. */
export function sessionState(
  session: RunningSession,
  ago: (iso: string | null) => string,
) {
  if (session.status === "working") return "Working";
  if (session.status === "waiting")
    return "Waiting for " + (session.waitingFor || "you");
  const when = ago(session.statusAt);
  return "Idle" + (when ? ", last active " + when : "");
}

/** A tie that reports its own failure where it was asked for. */
function useTie() {
  const writes = useWrites();
  const [error, setError] = useState<unknown>(null);
  return {
    busy: writes.isPending,
    error,
    async send<T>(write: () => Promise<T>): Promise<T | undefined> {
      setError(null);
      try {
        return await writes.mutateAsync(write);
      } catch (failure) {
        setError(failure);
        return undefined;
      }
    },
  };
}

/** Labels that more than one of these agents goes by: their IDs tell them apart. */
function sharedLabels(agents: ChatAgent[]) {
  const seen = new Set<string>();
  const shared = new Set<string>();
  for (const a of agents) (seen.has(a.label) ? shared : seen).add(a.label);
  return shared;
}

/** One agent tied to a chat, and how. */
function AgentTie({
  agent,
  alike,
  otherId,
}: {
  agent: ChatAgent;
  // Another agent here goes by the same label.
  alike: boolean;
  // A session's agent tied under another of its IDs than the one it is tracked by.
  otherId?: string;
}) {
  return (
    <li className={agent.binding === "run" ? "claim" : undefined}>
      <Avatar name={agent.avatar} size={18} />
      <a href={"#agents/" + agent.id}>{agent.label}</a>
      <small>
        {alike && <>· id {agent.id.slice(0, 8)} </>}· {BINDINGS[agent.binding]}
        {!agent.active && ", credential revoked"}
      </small>
      {otherId && (
        <small
          title={`It is tied under ${agent.chatId}, but this session is tracked by ${otherId}, so its tracked chat won't list this agent.`}
        >
          · under another ID
        </small>
      )}
    </li>
  );
}

/**
 * The agents tied to a tracked chat under its own chat ID: those registered with it, then those
 * whose open runs report it.
 */
export function ChatAgents({ chat }: { chat: ChatOverview }) {
  const alike = sharedLabels(chat.agents);
  return (
    <div className="chat-recipients chat-agents">
      <span className="muted">Agents</span>
      {chat.agents.length ? (
        <ul aria-label="Agents tied to this chat">
          {chat.agents.map((agent) => (
            <AgentTie
              key={agent.id}
              agent={agent}
              alike={alike.has(agent.label)}
            />
          ))}
        </ul>
      ) : (
        <small className="muted">
          None yet. An agent shows here once it registers with this chat or its
          run reports it.
        </small>
      )}
    </div>
  );
}

/**
 * The Claude Code sessions running on this machine that no work item or roadmap tracks yet.
 * Tying one to a work item or roadmap tracks it there and opens it. "Hide" keeps a session out of
 * this list in this browser while it runs.
 */
export function RunningSessions({
  refreshMs,
  ago,
  closed,
  toggle,
}: {
  refreshMs: number;
  ago: (iso: string | null) => string;
  closed: boolean;
  toggle: () => void;
}) {
  const running = useData<Answer<"/api/v1/claude-sessions">>(
    "/claude-sessions",
    refreshMs,
  );
  const hiddenKey = contextStorageKey("chats-hidden-sessions", "list");
  const [hidden, setHidden] = useState(() => readIdSet(hiddenKey));
  const [showHidden, setShowHidden] = useState(false);
  const sessions = (running.data?.items ?? []).filter(
    (s) => !s.tracked.some((t) => !t.archived),
  );
  const hiddenCount = sessions.filter((s) => hidden.has(s.sessionId)).length;
  const shown = sessions.filter((s) => showHidden || !hidden.has(s.sessionId));
  /** Hides a session in this browser, or shows it again. Past HIDDEN_LIMIT, the oldest are let go. */
  function setHiddenSession(id: string, hide: boolean) {
    const next = [...hidden].filter((h) => h !== id);
    if (hide) next.push(id);
    const kept = new Set(next.slice(-HIDDEN_LIMIT));
    setHidden(kept);
    localStore.write(hiddenKey, JSON.stringify([...kept]));
  }
  if (running.error)
    return (
      <section
        className="running-sessions"
        aria-label="Running on this machine"
      >
        <ErrorMessage error={running.error} />
      </section>
    );
  if (!sessions.length) return null;
  return (
    <section
      className="running-sessions"
      aria-labelledby="running-sessions-title"
    >
      <div className="running-head">
        <h3 id="running-sessions-title">Running on this machine</h3>
        <small className="muted">
          {sessions.length === 1
            ? "1 Claude Code session"
            : `${sessions.length} Claude Code sessions`}{" "}
          nothing tracks yet
        </small>
        <button
          type="button"
          className="tree-toggle"
          aria-expanded={!closed}
          aria-label={(closed ? "Expand" : "Collapse") + " running sessions"}
          onClick={toggle}
        >
          <Icon name={closed ? "expand" : "collapse"} size={14} />
        </button>
      </div>
      {!closed && (
        <>
          {shown.length > 0 && (
            <ul className="chat-list" aria-label="Sessions to track">
              {shown.map((s) => (
                <RunningSessionItem
                  key={s.sessionId}
                  session={s}
                  ago={ago}
                  hidden={hidden.has(s.sessionId)}
                  setHidden={(hide) => setHiddenSession(s.sessionId, hide)}
                />
              ))}
            </ul>
          )}
          {hiddenCount > 0 && (
            <button
              type="button"
              className="quiet"
              onClick={() => setShowHidden(!showHidden)}
            >
              {showHidden
                ? "Leave hidden sessions out"
                : hiddenCount === 1
                  ? "Show 1 hidden session"
                  : `Show ${hiddenCount} hidden sessions`}
            </button>
          )}
        </>
      )}
    </section>
  );
}

function RunningSessionItem({
  session: s,
  ago,
  hidden,
  setHidden,
}: {
  session: RunningSession;
  ago: (iso: string | null) => string;
  hidden: boolean;
  setHidden: (hide: boolean) => void;
}) {
  const tie = useTie();
  const name = s.name || "Claude Code session";
  const folder = folderName(s.cwd);
  const started = ago(s.startedAt);
  // Records whose chats for this session are archived: tying them again would track it twice.
  const archivedOn = new Set(
    s.tracked.map((t) => t.ownerType + ":" + t.ownerId),
  );
  const alike = sharedLabels(s.agents);
  async function track(ownerType: "item" | "roadmap", ownerId: string) {
    const chat = await tie.send(() =>
      commands.createChat({
        ownerType,
        ownerId,
        chatId: s.chatId,
        title: name,
        provider: "Anthropic",
        model: s.model,
      }),
    );
    // Tracked now, the session leaves this list and opens among the chats.
    if (chat) window.location.hash = "#chats/" + chat.id;
  }
  return (
    <li className={hidden ? "hidden-session" : undefined}>
      <div className="chat-link">
        <span
          className={
            "status-dot " +
            (s.status === "working"
              ? "doing"
              : s.status === "waiting"
                ? "waiting"
                : "open")
          }
          aria-hidden="true"
        />
        <span className="chat-link-text">
          <strong>{name}</strong>
          <small>{sessionState(s, ago)}</small>
        </span>
      </div>
      <div className="chat-row chat-meta">
        <span className="person-badge">{runsIn(s.entrypoint)}</span>
        {folder && <small title={s.cwd}>{folder}</small>}
        {started && <small>Started {started}</small>}
        {s.model && <small>{s.model}</small>}
      </div>
      {s.tracked.length > 0 && (
        <div className="chat-row chat-meta">
          <small>Archived chat on</small>
          {s.tracked.map((t) => (
            <a
              key={t.id}
              href={
                (t.ownerType === "roadmap" ? "#roadmaps/" : "#work/") +
                t.ownerId
              }
            >
              {t.ownerTitle || "Untitled"}
            </a>
          ))}
        </div>
      )}
      {s.agents.length > 0 && (
        <div className="chat-recipients chat-agents chat-meta">
          <span className="muted">Agents</span>
          <ul aria-label={`Agents tied to ${name}`}>
            {s.agents.map((agent) => (
              <AgentTie
                key={agent.id}
                agent={agent}
                alike={alike.has(agent.label)}
                otherId={agent.chatId === s.chatId ? undefined : s.chatId}
              />
            ))}
          </ul>
        </div>
      )}
      <div
        className="chat-ties chat-meta"
        role="group"
        aria-label={`Tie ${name}`}
      >
        <LinkPicker<Item>
          action="Tie work item"
          label="Work item to tie"
          placeholder="Find work…"
          path={(q) =>
            `/work-items?view=all&archived=false&limit=20&q=${encodeURIComponent(q)}`
          }
          linked={(item) => archivedOn.has("item:" + item.id)}
          option={(item) => (
            <>
              <span>{item.title}</span>
              <small>{statusLabel(item.status)}</small>
            </>
          )}
          none="No work items yet."
          onPick={(item) => void track("item", item.id)}
          disabled={tie.busy}
        />
        <LinkPicker<Roadmap>
          action="Tie roadmap"
          label="Roadmap to tie"
          placeholder="Find roadmaps…"
          path={(q) => `/roadmaps?archived=false&q=${encodeURIComponent(q)}`}
          linked={(roadmap) => archivedOn.has("roadmap:" + roadmap.id)}
          option={(roadmap) => <span>{roadmap.title}</span>}
          none="No roadmaps yet."
          onPick={(roadmap) => void track("roadmap", roadmap.id)}
          disabled={tie.busy}
        />
        <button
          type="button"
          className="quiet"
          aria-label={(hidden ? "Unhide " : "Hide ") + name}
          onClick={() => setHidden(!hidden)}
        >
          {hidden ? "Unhide" : "Hide"}
        </button>
      </div>
      <ErrorMessage error={tie.error} />
    </li>
  );
}
