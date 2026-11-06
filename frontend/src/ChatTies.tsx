import { useState } from "react";
import { contextStorageKey } from "./api";
import { Avatar } from "./Avatar";
import { ErrorMessage, statusLabel } from "./components";
import { useAction, useData } from "./hooks";
import { Icon } from "./icons";
import { LinkPicker } from "./LinkPicker";
import { localStore, readIdSet } from "./storage";
import type { AgentSummary } from "./agentTypes";
import type {
  ChatAgent,
  ChatOverview,
  Item,
  List,
  Roadmap,
  RunningSession,
} from "./types";

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
  confirmed: "confirmed by you",
  run: "reports this chat, not confirmed",
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

/** A tie or untie that reports its own failure where it was asked for. */
function useTie() {
  const action = useAction();
  const [error, setError] = useState<unknown>(null);
  return {
    busy: action.isPending,
    error,
    async send<T>(path: string, body: unknown): Promise<T | undefined> {
      setError(null);
      try {
        return (await action.mutateAsync({ path, body })) as T;
      } catch (failure) {
        setError(failure);
        return undefined;
      }
    },
  };
}

function AgentPicker({
  tied,
  onPick,
  disabled,
}: {
  // Agents already bound: they are not offered again.
  tied: Set<string>;
  onPick: (agentId: string) => void;
  disabled: boolean;
}) {
  return (
    <LinkPicker<AgentSummary>
      action="Tie agent"
      label="Agent to tie"
      placeholder="Find agents…"
      path={(q) => `/agents?limit=20&q=${encodeURIComponent(q)}`}
      // A revoked agent can no longer run, so tying it would reach nothing.
      linked={(agent) => tied.has(agent.id) || !agent.active}
      // Agents choose their own labels, so each also says what tells it apart.
      option={(agent) => (
        <>
          <span>{agent.label}</span>
          <small>
            {[
              agent.providers.join(", ") || "Provider unspecified",
              agent.openRuns === 1
                ? "1 open run"
                : `${agent.openRuns} open runs`,
              "id " + agent.id.slice(0, 8),
            ].join(" · ")}
          </small>
        </>
      )}
      none="No agents yet. An agent appears here once it registers, or once you issue it a credential in Settings."
      onPick={(agent) => onPick(agent.id)}
      disabled={disabled}
    />
  );
}

/** Labels that more than one of these agents goes by: their IDs tell them apart. */
function sharedLabels(agents: ChatAgent[]) {
  const seen = new Set<string>();
  const shared = new Set<string>();
  for (const a of agents) (seen.has(a.label) ? shared : seen).add(a.label);
  return shared;
}

/** One agent tied to a chat, with how, and what you can change about it. */
function AgentTie({
  agent,
  busy,
  alike,
  otherId,
  untie,
  confirm,
}: {
  agent: ChatAgent;
  busy: boolean;
  // Another agent here goes by the same label.
  alike: boolean;
  // A session's agent tied under another of its IDs than the one it is tracked by.
  otherId?: string;
  untie?: () => void;
  confirm?: () => void;
}) {
  return (
    <li className={agent.binding === "run" ? "unconfirmed" : undefined}>
      <Avatar name={agent.avatar} size={18} />
      <a href={"#agents/" + agent.id}>{agent.label}</a>
      <small>
        {alike && <>· id {agent.id.slice(0, 8)} </>}· {BINDINGS[agent.binding]}
        {!agent.active && ", credential revoked"}
      </small>
      {otherId && (
        <small
          title={`It is tied under ${agent.chatId}, but this session is tracked by ${otherId}, so what you write into its chat won't reach it.`}
        >
          · under another ID
        </small>
      )}
      {agent.binding === "confirmed" && untie && (
        <button
          type="button"
          aria-label={`Untie ${agent.label}`}
          disabled={busy}
          onClick={untie}
        >
          Untie
        </button>
      )}
      {agent.binding === "run" && confirm && (
        <button
          type="button"
          aria-label={`Confirm ${agent.label} works in this session`}
          disabled={busy}
          onClick={confirm}
        >
          Confirm
        </button>
      )}
    </li>
  );
}

/**
 * The agents tied to a tracked chat under its own chat ID: those registered with it and those
 * you confirmed in it. "Tie agent" confirms another, so what you write into the chat reaches
 * its open runs here; "Untie" takes a confirmation back. Both go through the chat itself, as
 * "Confirm" does, so its history keeps them and an archived chat refuses them. Runs that only
 * report the chat's ID wait for confirmation under "Message this chat's agents".
 */
export function ChatAgents({ chat }: { chat: ChatOverview }) {
  const tie = useTie();
  const bound = chat.agents.filter((a) => a.binding !== "run");
  const alike = sharedLabels(bound);
  const path = `/chats/${chat.id}/confirmations`;
  return (
    <div className="chat-recipients chat-agents">
      <span className="muted">Agents</span>
      {bound.length ? (
        <ul aria-label="Agents tied to this chat">
          {bound.map((agent) => (
            <AgentTie
              key={agent.id}
              agent={agent}
              busy={tie.busy}
              alike={alike.has(agent.label)}
              untie={
                chat.archived
                  ? undefined
                  : () => void tie.send(path + "/remove", { agentId: agent.id })
              }
            />
          ))}
        </ul>
      ) : (
        <small className="muted">None tied yet.</small>
      )}
      {!chat.archived && (
        <AgentPicker
          tied={new Set(bound.map((a) => a.id))}
          onPick={(agentId) => void tie.send(path, { agentId })}
          disabled={tie.busy}
        />
      )}
      <ErrorMessage error={tie.error} />
    </div>
  );
}

/**
 * The Claude Code sessions running on this machine that no work item or roadmap tracks yet.
 * Tying one to a work item or roadmap tracks it there and opens it; tying an agent confirms
 * that agent works in it, before or after it is tracked. "Hide" keeps a session out of this
 * list in this browser while it runs.
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
  const running = useData<List<RunningSession>>("/claude-sessions", refreshMs);
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
  const bound = new Set(
    s.agents.filter((a) => a.binding !== "run").map((a) => a.id),
  );
  const alike = sharedLabels(s.agents);
  async function track(ownerType: "item" | "roadmap", ownerId: string) {
    const chat = await tie.send<{ id: string }>("/chats", {
      ownerType,
      ownerId,
      chatId: s.chatId,
      title: name,
      provider: "Anthropic",
      model: s.model,
    });
    // Tracked now, the session leaves this list and opens among the chats.
    if (chat) window.location.hash = "#chats/" + chat.id;
  }
  /**
   * Confirms an agent in this session under the ID its run reports, since delivery matches that
   * ID exactly; an agent with no run here yet is confirmed under the ID the session is tracked by.
   */
  const confirm = (agentId: string) => {
    const claim = s.agents.find((a) => a.id === agentId && a.binding === "run");
    void tie.send("/chat-confirmations", {
      chatId: claim?.chatId ?? s.chatId,
      agentId,
    });
  };
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
                busy={tie.busy}
                alike={alike.has(agent.label)}
                otherId={agent.chatId === s.chatId ? undefined : s.chatId}
                // A confirmation is taken back under the ID it was given.
                untie={() =>
                  void tie.send("/chat-confirmations/remove", {
                    chatId: agent.chatId,
                    agentId: agent.id,
                  })
                }
                confirm={() => confirm(agent.id)}
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
        <AgentPicker tied={bound} onPick={confirm} disabled={tie.busy} />
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
