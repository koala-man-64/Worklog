import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useData } from "./hooks";
import { Empty, ErrorMessage, Markdown } from "./components";
import type {
  ChatMessage,
  ChatOverview,
  ChatThread,
  List,
  Transcript,
} from "./types";

// The list notices new activity; an open conversation follows along closely.
const LIST_REFRESH_MS = 15_000;
const TRANSCRIPT_REFRESH_MS = 5_000;
const PAGE = 200;
// The server's MESSAGE_LIMIT: the most messages one request returns.
const MOST = 2_000;
const SOURCES = { claude: "Claude Code session", codex: "Codex thread" };

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31_536_000],
  ["month", 2_592_000],
  ["week", 604_800],
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];
export function ago(iso: string | null, now = Date.now()) {
  if (!iso) return "";
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  for (const [unit, size] of UNITS)
    if (Math.abs(seconds) >= size)
      return relative.format(Math.round(seconds / size), unit);
  return "just now";
}
function clock(iso: string | null) {
  if (!iso) return "";
  const moment = new Date(iso);
  return moment.toDateString() === new Date().toDateString()
    ? moment.toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      })
    : moment.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
}
function agentName(chat: ChatOverview | Transcript["chat"]) {
  return [chat.provider, chat.model].filter(Boolean).join(" · ");
}
/** Working chats first, then the most recently active. */
function byActivity(a: ChatOverview, b: ChatOverview) {
  const working = (c: ChatOverview) =>
    c.transcript.state === "working" ? 0 : 1;
  return (
    working(a) - working(b) ||
    (b.transcript.lastActivityAt ?? "").localeCompare(
      a.transcript.lastActivityAt ?? "",
    ) ||
    b.createdDate.localeCompare(a.createdDate)
  );
}
function StateDot({ state }: { state: "working" | "idle" | "missing" }) {
  return (
    <span
      className={
        "status-dot " +
        (state === "working"
          ? "doing"
          : state === "missing"
            ? "missing"
            : "open")
      }
      aria-hidden="true"
    />
  );
}
function activity(summary: { state: string; lastActivityAt: string | null }) {
  const when = ago(summary.lastActivityAt);
  return summary.state === "working"
    ? "Working" + (when ? ", active " + when : "")
    : "Idle" + (when ? ", last active " + when : "");
}

export function Chats({ selected }: { selected?: string }) {
  const [archived, setArchived] = useState(false);
  const list = useData<List<ChatOverview>>(
    `/chats?archived=${archived}`,
    LIST_REFRESH_MS,
  );
  const chats = [...(list.data?.items ?? [])].sort(byActivity);
  // With nothing chosen, the most recently active chat opens.
  const current =
    chats.find((c) => c.id === selected) ?? (selected ? undefined : chats[0]);
  return (
    <div className="bounded-screen">
      <header>
        <h1>Chats</h1>
        <p className="lede">
          Who is working on what, and every message sent between you and your
          agents, read from the transcripts on this machine.
        </p>
      </header>
      <ErrorMessage error={list.error} />
      <div className="split chats-split">
        <div
          className="panel"
          role="region"
          aria-label="Who is working on what"
        >
          <div className="panel-head">
            <h2>Who is working on what</h2>
            <label className="muted">
              <input
                type="checkbox"
                checked={archived}
                onChange={(e) => setArchived(e.target.checked)}
              />{" "}
              Show archive
            </label>
          </div>
          <div className="panel-body">
            {list.isPending ? (
              <p>Loading chats…</p>
            ) : !chats.length ? (
              <Empty>
                {archived
                  ? "No archived chats."
                  : "Track an agent chat on a roadmap or work item with its Claude Code or Codex chat ID to follow it here."}
              </Empty>
            ) : (
              <ul className="chat-list">
                {chats.map((c) => (
                  <ChatListItem
                    key={c.id}
                    chat={c}
                    current={c.id === current?.id}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>
        <div className="pane detail" role="region" aria-label="Chat history">
          {current ? (
            <ChatHistory key={current.id} chat={current} />
          ) : selected && list.data ? (
            <Empty>This chat is not in the list. It may be archived.</Empty>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ChatListItem({
  chat,
  current,
}: {
  chat: ChatOverview;
  current: boolean;
}) {
  const { transcript, owner } = chat;
  const agents = transcript.threads.filter((t) => t.parent);
  const agent = agentName(chat);
  const ownerHref =
    owner.type === "roadmap" ? "#roadmaps/" + owner.id : "#work/" + owner.id;
  return (
    <li className={current ? "selected" : undefined}>
      <a
        className="chat-link"
        href={"#chats/" + chat.id}
        aria-current={current ? "true" : undefined}
      >
        <StateDot state={transcript.found ? transcript.state : "missing"} />
        <span className="chat-link-text">
          <strong>{chat.title}</strong>
          <small>
            {transcript.found
              ? activity(transcript)
              : (transcript.error ?? "No transcript on this machine")}
          </small>
        </span>
      </a>
      <div className="chat-row chat-meta">
        {agent && <span className="person-badge">{agent}</span>}
        <small>
          {owner.type === "roadmap" ? "Roadmap" : "Work item"}{" "}
          <a href={ownerHref}>{owner.title || "Untitled"}</a>
        </small>
      </div>
      {chat.steps.length > 0 && (
        <div className="chat-row chat-meta">
          <small>Working on</small>
          {chat.steps.map((s) => (
            <a key={s.id} href={`#roadmaps/${owner.id}/${s.id}`}>
              {s.title}
            </a>
          ))}
        </div>
      )}
      {agents.length > 0 && (
        <ul className="chat-agents" aria-label={"Agents in " + chat.title}>
          {agents.map((t) => (
            <li key={t.key}>
              <StateDot state={t.state} />
              <span>
                {t.name}
                {t.task && (
                  <>
                    {" · "}
                    <small>{t.task}</small>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function ChatHistory({ chat }: { chat: ChatOverview }) {
  const [thread, setThread] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const transcript = useData<Transcript>(
    `/chats/${chat.id}/transcript?limit=${limit}` +
      (thread ? "&thread=" + encodeURIComponent(thread) : ""),
    TRANSCRIPT_REFRESH_MS,
  );
  const data = transcript.data;
  const threads = data?.threads ?? chat.transcript.threads;
  const agent = agentName(chat);
  return (
    <>
      <div className="chat-history-head">
        <h2>{chat.title}</h2>
        <div className="chat-row">
          {agent && <span className="person-badge">{agent}</span>}
          <code>{chat.chatId}</code>
          {data?.found && data.source && (
            <small>
              {SOURCES[data.source]}, {activity(data).toLowerCase()}
            </small>
          )}
        </div>
        {threads.length > 1 && (
          <div
            className="thread-filter"
            role="group"
            aria-label="Show messages involving"
          >
            <button
              type="button"
              aria-pressed={thread === null}
              onClick={() => setThread(null)}
            >
              Everyone
            </button>
            {threads.map((t) => (
              <ThreadButton
                key={t.key}
                thread={t}
                pressed={thread === t.key}
                onClick={() => setThread(t.key)}
              />
            ))}
          </div>
        )}
      </div>
      <ErrorMessage error={transcript.error} />
      {transcript.isPending ? (
        <p>Reading the transcript…</p>
      ) : data && !data.found ? (
        <Empty>
          No transcript on this machine has this chat ID. Worklog reads Claude
          Code sessions, Claude desktop sessions (local_ IDs), and Codex
          threads.
        </Empty>
      ) : data && !data.messages.length ? (
        <Empty>No messages yet.</Empty>
      ) : data ? (
        <Messages
          key={thread ?? "everyone"}
          messages={data.messages}
          more={data.more && limit < MOST}
          hidden={data.total - data.messages.length}
          earlier={() => setLimit(Math.min(limit + PAGE, MOST))}
          loading={transcript.isPlaceholderData}
        />
      ) : null}
    </>
  );
}

function ThreadButton({
  thread,
  pressed,
  onClick,
}: {
  thread: ChatThread;
  pressed: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      title={[thread.title || thread.task, thread.model]
        .filter(Boolean)
        .join(" · ")}
      onClick={onClick}
    >
      <StateDot state={thread.state} />
      <span>
        {thread.name}
        {thread.task && (
          <>
            {" · "}
            <small>{thread.task}</small>
          </>
        )}
      </span>
    </button>
  );
}

function Messages({
  messages,
  more,
  hidden,
  earlier,
  loading,
}: {
  messages: ChatMessage[];
  more: boolean;
  // Earlier messages in the transcript that are not shown.
  hidden: number;
  earlier: () => void;
  loading: boolean;
}) {
  const end = useRef<HTMLDivElement>(null);
  const last = messages[messages.length - 1]?.id;
  const seen = useRef<string | undefined>(undefined);
  // Side by side, the conversation scrolls in its own pane and follows new messages
  // unless the person scrolled up to read. Stacked, the page stays where they put it.
  const pinned = useRef(true);
  useEffect(() => {
    const marker = end.current;
    const box = marker && scroller(marker);
    if (!marker || !box) return;
    const update = () => {
      pinned.current =
        marker.getBoundingClientRect().top -
          box.getBoundingClientRect().bottom <
        80;
    };
    box.addEventListener("scroll", update, { passive: true });
    return () => box.removeEventListener("scroll", update);
  }, []);
  useLayoutEffect(() => {
    const box = end.current && scroller(end.current);
    if (!last || last === seen.current) return;
    seen.current = last;
    if (box && pinned.current) box.scrollTop = box.scrollHeight;
  }, [last]);
  return (
    <div className="chat-messages" aria-busy={loading || undefined}>
      {more ? (
        <button
          type="button"
          className="quiet"
          disabled={loading}
          onClick={earlier}
        >
          {loading ? "Loading earlier messages…" : "Show earlier messages"}
        </button>
      ) : (
        hidden > 0 && (
          <small className="muted">
            {hidden === 1
              ? "1 earlier message is in the transcript but not shown here."
              : `${hidden} earlier messages are in the transcript but not shown here.`}
          </small>
        )
      )}
      <ol className="messages" aria-label="Messages">
        {messages.map((m) => (
          <Message key={m.id} message={m} />
        ))}
      </ol>
      <div ref={end} />
    </div>
  );
}

function scroller(node: HTMLElement): HTMLElement | null {
  for (let parent = node.parentElement; parent; parent = parent.parentElement)
    if (/(auto|scroll)/.test(getComputedStyle(parent).overflowY)) return parent;
  return null;
}

function Message({ message: m }: { message: ChatMessage }) {
  const time = m.at ? (
    <time dateTime={m.at} title={new Date(m.at).toLocaleString()}>
      {clock(m.at)}
    </time>
  ) : null;
  if (m.kind === "tools") {
    const names = Object.entries(
      (m.tools ?? []).reduce<Record<string, number>>(
        (counts, t) => ({ ...counts, [t.name]: (counts[t.name] ?? 0) + 1 }),
        {},
      ),
    )
      .map(([name, n]) => (n > 1 ? `${name} ×${n}` : name))
      .join(", ");
    return (
      <li className="message tools">
        <details>
          <summary>
            <span>
              {m.author} ran {m.count === 1 ? "1 tool" : `${m.count} tools`}
              {names && <span className="muted">: {names}</span>}
            </span>
            {time}
          </summary>
          <ul>
            {(m.tools ?? []).map((t, i) => (
              <li key={i}>
                <code>{t.name}</code> {t.summary}
              </li>
            ))}
            {(m.count ?? 0) > (m.tools?.length ?? 0) && (
              <li className="muted">
                and {(m.count ?? 0) - (m.tools?.length ?? 0)} more
              </li>
            )}
          </ul>
        </details>
      </li>
    );
  }
  return (
    <li className={"message " + m.kind}>
      <div className="message-head">
        <strong>{m.author}</strong>
        {m.kind === "agent" && m.recipient && (
          <>
            <span className="muted">to</span>
            <strong>{m.recipient}</strong>
          </>
        )}
        {time}
      </div>
      <Markdown text={m.text ?? ""} untrusted />
      {m.truncated && (
        <small className="muted">
          Shortened here; the full message is in the transcript.
        </small>
      )}
    </li>
  );
}
