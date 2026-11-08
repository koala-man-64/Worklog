import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { contextStorageKey } from "./api";
import { useCollapsed, useData } from "./hooks";
import { RepositoryBadges } from "./RepositoryControls";
import { NamedRuns } from "./Coordination";
import { ChatOutbox } from "./ChatOutbox";
import { ChatAgents, RunningSessions } from "./ChatTies";
import { RecordReminders } from "./RecordReminders";
import { Markdown } from "./ui/Markdown";
import { Empty, ErrorMessage } from "./ui/Messages";
import { Icon, type IconName } from "./icons";
import { localStore } from "./storage";
import type {
  Answer,
  ChatMessage,
  ChatOverview,
  ChatThread,
  Transcript,
} from "./apiTypes";

// The list notices new activity; an open conversation follows along closely.
const LIST_REFRESH_MS = 15_000;
const TRANSCRIPT_REFRESH_MS = 5_000;
const PAGE = 200;
// The server's MESSAGE_LIMIT: the most messages one request returns.
const MOST = 2_000;
const SOURCES = { claude: "Claude Code session", codex: "Codex thread" };
const TOOLS = { claude: "Claude", codex: "Codex" };
// The collapsed key of the sessions running on this machine, beside the groups' own.
const RUNNING = "running";

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
/** The tool that keeps the chat's transcript and how many agents it started; with no transcript, the provider and model it was tracked with. */
export function toolText(chat: ChatOverview) {
  const { source, threads } = chat.transcript;
  if (!source) return agentName(chat);
  const agents = threads.filter((t) => t.parent).length;
  return [
    TOOLS[source],
    agents && (agents === 1 ? "1 agent" : `${agents} agents`),
  ]
    .filter(Boolean)
    .join(" · ");
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

/** How finely the list shows activity: each chat, or chats gathered under their records. */
type Level = "chat" | "item" | "roadmap";
const LEVELS: [Level, string][] = [
  ["chat", "Chat"],
  ["item", "Work item"],
  ["roadmap", "Roadmap"],
];
/** The chats in a group and everything within it. */
type Tally = {
  total: number;
  working: number;
  found: number;
  lastActivityAt: string | null;
};
/** A roadmap or work item with the chats tracked on it and, for a roadmap, the records within it. */
export type ChatGroup = {
  key: string;
  kind: "roadmap" | "item" | "loose";
  id: string;
  title: string;
  archived: boolean;
  // Work item level: the roadmaps a group's record sits within.
  within: string[];
  groups: ChatGroup[];
  chats: ChatOverview[];
  tally: Tally;
};
const KINDS = { roadmap: "Roadmap", item: "Work item", loose: "" };
// A group leads with its record's sidebar icon, so it never reads as a chat, which leads with its state dot.
const ICONS: Record<ChatGroup["kind"], IconName> = {
  roadmap: "roadmaps",
  item: "work",
  loose: "work",
};
// Sub-roadmaps come before work items, and work items off every roadmap come last.
const RANK = { roadmap: 0, item: 1, loose: 2 };

/**
 * Gathers chats under the records they are tracked on. At the work item level each
 * record is one group; at the roadmap level work items and sub-roadmaps sit inside
 * their roadmaps, and work items on no roadmap share one group at the end.
 */
export function groupChats(
  chats: ChatOverview[],
  level: "item" | "roadmap",
): ChatGroup[] {
  const top: ChatGroup[] = [];
  const known = new Map<string, ChatGroup>();
  const group = (
    siblings: ChatGroup[],
    kind: ChatGroup["kind"],
    record: { id: string; title: string; archived: boolean },
    within: string[] = [],
  ) => {
    const key = `${level}:${kind}:${record.id}`;
    let found = known.get(key);
    if (!found) {
      found = {
        key,
        kind,
        ...record,
        within,
        groups: [],
        chats: [],
        tally: { total: 0, working: 0, found: 0, lastActivityAt: null },
      };
      known.set(key, found);
      siblings.push(found);
    }
    return found;
  };
  for (const chat of chats) {
    const owner = {
      id: chat.owner.id,
      title: chat.owner.title || "Untitled",
      archived: chat.owner.archived,
    };
    const roadmaps =
      chat.ownerType === "roadmap" && !chat.roadmaps.length
        ? [owner]
        : chat.roadmaps.map((r) => ({ ...r, title: r.title || "Untitled" }));
    if (level === "item") {
      const kind = chat.ownerType === "roadmap" ? "roadmap" : "item";
      const above = kind === "roadmap" ? roadmaps.slice(0, -1) : roadmaps;
      group(
        top,
        kind,
        owner,
        above.map((r) => r.title),
      ).chats.push(chat);
      continue;
    }
    let siblings = top;
    let parent: ChatGroup | undefined;
    for (const r of roadmaps) {
      parent = group(siblings, "roadmap", r);
      siblings = parent.groups;
    }
    if (chat.ownerType === "roadmap" && parent) parent.chats.push(chat);
    else {
      if (!parent) {
        parent = group(top, "loose", {
          id: "",
          title: "Not on a roadmap",
          archived: false,
        });
        siblings = parent.groups;
      }
      group(siblings, "item", owner).chats.push(chat);
    }
  }
  return finish(top, level === "roadmap");
}
function finish(groups: ChatGroup[], ranked: boolean): ChatGroup[] {
  for (const g of groups) {
    g.chats.sort(byActivity);
    finish(g.groups, ranked);
    g.tally = [
      ...g.chats.map((c) => ({
        total: 1,
        working: c.transcript.state === "working" ? 1 : 0,
        found: c.transcript.found ? 1 : 0,
        lastActivityAt: c.transcript.lastActivityAt,
      })),
      ...g.groups.map((child) => child.tally),
    ].reduce((sum, t) => ({
      total: sum.total + t.total,
      working: sum.working + t.working,
      found: sum.found + t.found,
      lastActivityAt:
        (t.lastActivityAt ?? "") > (sum.lastActivityAt ?? "")
          ? t.lastActivityAt
          : sum.lastActivityAt,
    }));
  }
  /** Groups with working chats first, then the most recently active. */
  return groups.sort(
    (a, b) =>
      (ranked ? RANK[a.kind] - RANK[b.kind] : 0) ||
      Number(b.tally.working > 0) - Number(a.tally.working > 0) ||
      (b.tally.lastActivityAt ?? "").localeCompare(
        a.tally.lastActivityAt ?? "",
      ) ||
      a.title.localeCompare(b.title),
  );
}
function everyKey(groups: ChatGroup[]): string[] {
  return groups.flatMap((g) => [g.key, ...everyKey(g.groups)]);
}
/** The groups that hold a chat, outermost first. */
function holding(groups: ChatGroup[], chatId: string): string[] {
  for (const g of groups) {
    if (g.chats.some((c) => c.id === chatId)) return [g.key];
    const below = holding(g.groups, chatId);
    if (below.length) return [g.key, ...below];
  }
  return [];
}
export function tallyText(t: Tally) {
  const when = ago(t.lastActivityAt);
  if (t.working)
    return (
      (t.total === 1 ? "1 chat" : `${t.working} of ${t.total} chats`) +
      " working" +
      (when ? ", active " + when : "")
    );
  return (
    (t.total === 1 ? "1 chat" : `${t.total} chats`) +
    (when
      ? ", last active " + when
      : t.found
        ? ""
        : t.total === 1
          ? ", no transcript on this machine"
          : ", no transcripts on this machine")
  );
}

export function Chats({ selected }: { selected?: string }) {
  const [archived, setArchived] = useState(false);
  const levelKey = contextStorageKey("chats-level", "list");
  const [level, setLevel] = useState<Level>(() => {
    const stored = localStore.read(levelKey).value;
    return LEVELS.find(([l]) => l === stored)?.[0] ?? "chat";
  });
  const { collapsed, toggle, reveal, setClosed } = useCollapsed(
    contextStorageKey("chats-collapsed", "list"),
  );
  const [revealed, setRevealed] = useState<string>();
  const list = useData<Answer<"/api/v1/chats">>(
    `/chats?archived=${archived}`,
    LIST_REFRESH_MS,
  );
  const chats = [...(list.data?.items ?? [])].sort(byActivity);
  const groups = level === "chat" ? [] : groupChats(chats, level);
  const keys = everyKey(groups);
  // With nothing chosen, the most recently active chat opens.
  const current =
    chats.find((c) => c.id === selected) ?? (selected ? undefined : chats[0]);
  // A chat opened from elsewhere shows in the list: expand the groups that hold it once.
  const shown = selected && `${level}:${selected}`;
  if (list.data && shown !== revealed) {
    setRevealed(shown);
    const holders = selected ? holding(groups, selected) : [];
    if (holders.some((k) => collapsed.has(k))) reveal(holders);
  }
  return (
    <div className="bounded-screen">
      <header>
        <h1>Chats</h1>
        <p className="lede">
          Reported repository context and observed transcript activity. Every
          message sent between you and your agents, read from the transcripts on
          this machine.
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
          <div className="chat-filters">
            <div
              className="thread-filter"
              role="group"
              aria-label="Show activity by"
            >
              {LEVELS.map(([value, label]) => (
                <button
                  type="button"
                  key={value}
                  aria-pressed={level === value}
                  onClick={() => {
                    setLevel(value);
                    localStore.write(levelKey, value);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            {keys.length > 0 && (
              <div className="toolbar compact">
                <button
                  type="button"
                  className="quiet"
                  disabled={!keys.some((k) => collapsed.has(k))}
                  onClick={() => setClosed(keys, false)}
                >
                  Expand all
                </button>
                <button
                  type="button"
                  className="quiet"
                  disabled={keys.every((k) => collapsed.has(k))}
                  onClick={() => setClosed(keys, true)}
                >
                  Collapse all
                </button>
              </div>
            )}
          </div>
          <div className="panel-body">
            {!archived && (
              <RunningSessions
                refreshMs={LIST_REFRESH_MS}
                ago={ago}
                closed={collapsed.has(RUNNING)}
                toggle={() => toggle(RUNNING)}
              />
            )}
            {list.isPending ? (
              <p>Loading chats…</p>
            ) : !chats.length ? (
              <Empty>
                {archived
                  ? "No archived chats."
                  : "Track an agent chat on a roadmap or work item with its Claude Code or Codex chat ID to follow it here."}
              </Empty>
            ) : level === "chat" ? (
              <ul className="chat-list">
                {chats.map((c) => (
                  <ChatListItem
                    key={c.id}
                    chat={c}
                    current={c.id === current?.id}
                  />
                ))}
              </ul>
            ) : (
              <ul className="chat-list">
                {groups.map((g) => (
                  <GroupItem
                    key={g.key}
                    group={g}
                    collapsed={collapsed}
                    toggle={toggle}
                    current={current?.id}
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

function GroupItem({
  group,
  collapsed,
  toggle,
  current,
}: {
  group: ChatGroup;
  collapsed: Set<string>;
  toggle: (key: string) => void;
  current?: string;
}) {
  const { tally } = group;
  const closed = collapsed.has(group.key);
  const kind = KINDS[group.kind];
  const href =
    group.kind === "roadmap"
      ? "#roadmaps/" + group.id
      : group.kind === "item"
        ? "#work/" + group.id
        : undefined;
  return (
    <li className="chat-group">
      <div className={"chat-group-head " + group.kind}>
        <span className={"chat-kind" + (tally.working ? " doing" : "")}>
          <Icon name={ICONS[group.kind]} />
        </span>
        <span className="chat-link-text">
          {href ? (
            <a href={href}>{group.title}</a>
          ) : (
            <strong>{group.title}</strong>
          )}
          <small>
            {[
              kind &&
                (group.archived ? "Archived " + kind.toLowerCase() : kind) +
                  (group.within.length
                    ? " in " + group.within.join(" › ")
                    : ""),
              tallyText(tally),
            ]
              .filter(Boolean)
              .join(" · ")}
          </small>
        </span>
        <button
          type="button"
          className="tree-toggle"
          aria-expanded={!closed}
          aria-label={(closed ? "Expand " : "Collapse ") + group.title}
          onClick={() => toggle(group.key)}
        >
          <Icon name={closed ? "expand" : "collapse"} size={14} />
        </button>
      </div>
      {!closed && (
        <ul className="chat-list" aria-label={"Chats in " + group.title}>
          {group.groups.map((g) => (
            <GroupItem
              key={g.key}
              group={g}
              collapsed={collapsed}
              toggle={toggle}
              current={current}
            />
          ))}
          {group.chats.map((c) => (
            <ChatListItem
              key={c.id}
              chat={c}
              current={c.id === current}
              grouped
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function ChatListItem({
  chat,
  current,
  grouped = false,
}: {
  chat: ChatOverview;
  current: boolean;
  // Under its record's group the chat need not name that record again.
  grouped?: boolean;
}) {
  const { transcript, owner } = chat;
  const tool = toolText(chat);
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
      {(tool || !grouped) && (
        <div className="chat-row chat-meta">
          {tool && <span className="person-badge">{tool}</span>}
          {!grouped && (
            <small>
              {owner.type === "roadmap" ? "Roadmap" : "Work item"}{" "}
              <a href={ownerHref}>{owner.title || "Untitled"}</a>
            </small>
          )}
        </div>
      )}
      <div className="chat-row chat-meta">
        <RepositoryBadges ids={chat.repositoryIds} legacy={chat.repos} />
      </div>
      <NamedRuns chatId={chat.chatId} />
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
        <ChatAgents chat={chat} />
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
      <ChatOutbox chat={chat} refreshMs={TRANSCRIPT_REFRESH_MS} time={clock} />
      <RecordReminders
        targetType="chat"
        targetId={chat.id}
        archived={!!chat.archived}
      />
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
        {m.messageType && m.messageType !== "MESSAGE" && (
          <span className="muted">
            · {m.messageType.toLowerCase().replace(/_/g, " ")}
          </span>
        )}
        {time}
      </div>
      {m.encrypted ? (
        <p className="message-encrypted muted">
          Encrypted by Codex. The transcript keeps no readable copy of this
          message.
        </p>
      ) : (
        <Markdown text={m.text ?? ""} untrusted />
      )}
      {m.truncated && (
        <small className="muted">
          Shortened here; the full message is in the transcript.
        </small>
      )}
    </li>
  );
}
