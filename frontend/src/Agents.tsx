import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { getDataContext } from "./api";
import { Avatar } from "./Avatar";
import { Icon } from "./icons";
import { useData } from "./hooks";
import { Empty, ErrorMessage } from "./ui/Messages";
import { Pager } from "./ui/Pager";
import { EndRun } from "./Coordination";
import { AgentProposals } from "./ProposalHistory";
import {
  exactDiscussionDate,
  formatDiscussionDate,
} from "./discussionFormatting";
import { REASONS as reasons } from "./tieReasons";
import type {
  AgentDetail,
  AgentSummary,
  Answer,
  RegistryRole,
  RegistryWork,
  TieReason,
} from "./apiTypes";

const PAGE = 50;
// The rolodex holds as many cards as one read of the agents gives.
const CARDS = 200;
// A card names this many repositories and roadmaps; the agent's page lists them all.
const SHOWN = 8;
const REFRESH = 30_000;
function timestamp(value: string) {
  return <time dateTime={value}>{new Date(value).toLocaleString()}</time>;
}
const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
// Reports are provenance: a fresh one says the agent spoke lately, not that it is running.
const STATES = {
  reporting: "Reporting work",
  stale: "Stale reports",
  quiet: "No current reports",
  revoked: "Credential revoked",
} as const;
function tone(agent: AgentSummary): keyof typeof STATES {
  if (!agent.active) return "revoked";
  if (agent.working.some((work) => !work.stale)) return "reporting";
  return agent.working.length ? "stale" : "quiet";
}
const state = (agent: AgentSummary) => STATES[tone(agent)];
export function Agents({ selected }: { selected?: string }) {
  return selected ? <AgentView key={selected} id={selected} /> : <Rolodex />;
}

// The card open when the rolodex was last left, so coming back, as from an agent's page, opens it again.
let lastCard: { context: string; id: string } | null = null;
function remember(id: string) {
  lastCard = { context: getDataContext(), id };
}
function remembered() {
  return lastCard?.context === getDataContext() ? lastCard.id : null;
}
/** Whether an agent's label, providers, repositories or roadmaps hold the words, ignoring case. */
function matches(agent: AgentSummary, words: string) {
  const q = words.trim().toLocaleLowerCase();
  return (
    !q ||
    [
      agent.label,
      ...agent.providers,
      ...agent.repositories.items.map((r) => r.name),
      ...agent.roadmaps.items.map((r) => r.title),
    ].some((text) => text.toLocaleLowerCase().includes(q))
  );
}
/** Agents choose their own labels, so an index entry also says where it worked and when it last did anything. */
function hint(agent: AgentSummary) {
  return [
    agent.repositories.items[0]?.name,
    agent.roadmaps.items[0]?.title,
    agent.lastActivityAt
      ? formatDiscussionDate(agent.lastActivityAt)
      : "No activity yet",
  ]
    .filter(Boolean)
    .join(" · ");
}
/** Scrolls an index entry into its own list's view, leaving the page where it is. */
function reveal(entry: HTMLElement) {
  const list = entry.closest(".panel-body");
  if (!list) return;
  // The first tab of a group brings the group's heading with it.
  const first = !entry.closest("li")?.previousElementSibling;
  const { top } = (
    (first && entry.closest("section")) ||
    entry
  ).getBoundingClientRect();
  const { bottom } = entry.getBoundingClientRect();
  const shown = list.getBoundingClientRect();
  if (top < shown.top) list.scrollTop -= shown.top - top;
  else if (bottom > shown.bottom) list.scrollTop += bottom - shown.bottom;
}
type Flip = "next" | "back" | null;
/**
 * Every agent as a card in a rolodex: an index of their tabs, active agents first, beside the open
 * card, which names the repositories and roadmaps the agent's work ties it to. Previous card, Next
 * card and the arrow keys flip through them, round from the last to the first.
 */
function Rolodex() {
  const [offset, setOffset] = useState(0);
  const [words, setWords] = useState("");
  const [open, setOpen] = useState(remembered);
  const [flip, setFlip] = useState<Flip>(null);
  const entries = useRef(new Map<string, HTMLButtonElement>());
  // Set when the keys flipped from the index, whose focus follows the card.
  const follow = useRef(false);
  const list = useData<Answer<"/api/v1/agents">>(
    `/agents?limit=${CARDS}&offset=${offset}`,
    REFRESH,
  );
  const all = list.data?.items ?? [];
  const cards = all.filter((agent) => matches(agent, words));
  const found = cards.findIndex((agent) => agent.id === open);
  const at = found < 0 ? 0 : found;
  const card = cards[at];
  const cardId = card?.id;
  useEffect(() => {
    if (!cardId) return;
    remember(cardId);
    const entry = entries.current.get(cardId);
    if (!entry) return;
    reveal(entry);
    if (follow.current) entry.focus();
    follow.current = false;
  }, [cardId]);
  /** Opens the card at `index`, round from either end; `focus` moves focus to its index entry. */
  function show(index: number, focus = false) {
    const next = cards[(index + cards.length) % cards.length];
    if (!next || next.id === cardId) return;
    follow.current = focus;
    setFlip(index < at ? "back" : "next");
    setOpen(next.id);
  }
  function keys(fromIndex: boolean) {
    return (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || !cards.length)
        return;
      const to = {
        ArrowDown: at + 1,
        ArrowRight: at + 1,
        ArrowUp: at - 1,
        ArrowLeft: at - 1,
        Home: 0,
        End: cards.length - 1,
      }[event.key];
      if (to === undefined) return;
      event.preventDefault();
      show(to, fromIndex);
    };
  }
  const groups = [
    { title: "Active", cards: cards.filter((agent) => agent.active) },
    { title: "Retired", cards: cards.filter((agent) => !agent.active) },
  ].filter((group) => group.cards.length);
  const nothingYet = list.data?.total === 0;
  return (
    <div className="bounded-screen">
      <header>
        <h1>Agents</h1>
        <p className="lede">
          A card for every agent, with the repositories and roadmaps its runs,
          reports, messages and responsibilities tie it to.
        </p>
      </header>
      <ErrorMessage error={list.error} />
      {nothingYet ? (
        <Empty>
          No agents yet. Create an agent credential in Settings; each agent gets
          a card here with the repositories and roadmaps it works on.
        </Empty>
      ) : (
        <div className="split agents-split">
          <div className="panel" role="region" aria-label="Agent index">
            <div className="panel-head">
              <h2>
                Index <span className="count">{list.data?.total ?? 0}</span>
              </h2>
            </div>
            <div className="rolodex-find">
              <input
                type="search"
                aria-label="Find agents"
                placeholder="Name, repository or roadmap…"
                value={words}
                onChange={(e) => {
                  setWords(e.target.value);
                  // Words that hide the open card open the first card they find instead.
                  const found = all.filter((agent) =>
                    matches(agent, e.target.value),
                  );
                  if (found[0] && !found.some((agent) => agent.id === cardId)) {
                    setFlip(null);
                    setOpen(found[0].id);
                  }
                }}
              />
            </div>
            <div className="panel-body">
              {list.isPending ? (
                <p>Loading agents…</p>
              ) : !cards.length && words.trim() ? (
                <p className="muted">No agent matches those words.</p>
              ) : null}
              {groups.map((group) => (
                <section key={group.title} aria-label={group.title + " agents"}>
                  <h3 className="rolodex-group">
                    {group.title}{" "}
                    <span className="count">{group.cards.length}</span>
                  </h3>
                  <ul className="rolodex-index" onKeyDown={keys(true)}>
                    {group.cards.map((agent) => (
                      <li key={agent.id}>
                        <button
                          type="button"
                          ref={(node) => {
                            if (node) entries.current.set(agent.id, node);
                            else entries.current.delete(agent.id);
                          }}
                          aria-current={
                            agent.id === cardId ? "true" : undefined
                          }
                          // One tab stop for the index: the arrow keys move within it.
                          tabIndex={agent.id === cardId ? 0 : -1}
                          onClick={() => show(cards.indexOf(agent))}
                        >
                          <Avatar name={agent.avatar} />
                          <span className="rolodex-entry">
                            <span
                              className="rolodex-entry-name"
                              title={agent.label}
                            >
                              {agent.label}
                            </span>
                            <small>{hint(agent)}</small>
                          </span>
                          <span
                            className={"rolodex-dot " + tone(agent)}
                            title={state(agent)}
                          />
                          <span className="visually-hidden">
                            {state(agent)}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
              <Pager
                offset={offset}
                total={list.data?.total ?? 0}
                onChange={setOffset}
                label="agents"
                limit={CARDS}
              />
            </div>
          </div>
          <section
            className="pane rolodex-stage"
            role="region"
            aria-label="Agent card"
          >
            {card && (
              <div className="rolodex-deck">
                {[2, 1].map(
                  (depth) =>
                    cards.length > depth && (
                      <div
                        key={depth}
                        className={"rolodex-peek depth-" + depth}
                        aria-hidden="true"
                      >
                        <span className="rolodex-tab">
                          {cards[(at + depth) % cards.length].label}
                        </span>
                      </div>
                    ),
                )}
                {/* A card that opens in place of one gone from the list arrives without a flip. */}
                <AgentCard
                  key={card.id}
                  agent={card}
                  flip={found < 0 ? null : flip}
                />
              </div>
            )}
            {card && (
              <div
                className="rolodex-controls"
                role="group"
                aria-label="Flip through the cards"
                onKeyDown={keys(false)}
              >
                <button
                  type="button"
                  disabled={cards.length < 2}
                  onClick={() => show(at - 1)}
                >
                  <Icon name="back" /> Previous card
                </button>
                <span aria-live="polite" aria-atomic="true">
                  Card {at + 1} of {cards.length}
                  <span className="visually-hidden">: {card.label}</span>
                </span>
                <button
                  type="button"
                  disabled={cards.length < 2}
                  onClick={() => show(at + 1)}
                >
                  Next card <Icon name="expand" />
                </button>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
/** The open card: who the agent is and what state it is in, then where its work took it. */
function AgentCard({ agent, flip }: { agent: AgentSummary; flip: Flip }) {
  const title = useId();
  const page = "#agents/" + agent.id;
  return (
    <article
      className={"rolodex-card" + (flip ? " flip-" + flip : "")}
      aria-labelledby={title}
    >
      <span className="rolodex-tab">
        <span className={"rolodex-dot " + tone(agent)} /> {state(agent)}
      </span>
      <header className="rolodex-card-head">
        <Avatar name={agent.avatar} size={56} />
        <div>
          <h2 id={title}>
            <a href={page}>{agent.label}</a>
          </h2>
          <AgentFacts agent={agent} />
        </div>
      </header>
      <div className="rolodex-card-ties">
        <CardTies
          title="Repositories"
          ties={agent.repositories}
          name={(repository) => repository.name}
          href={(repository) => "#repositories/" + repository.id}
          empty="No repositories yet."
          page={page}
        />
        <CardTies
          title="Roadmaps"
          ties={agent.roadmaps}
          name={(roadmap) => roadmap.title}
          href={(roadmap) => "#roadmaps/" + roadmap.id}
          empty="No roadmaps yet."
          page={page}
        />
      </div>
      {agent.working.length > 0 && (
        <div className="rolodex-card-section">
          <h3>Working now</h3>
          <WorkingOn work={agent.working} />
        </div>
      )}
      {agent.roles.length > 0 && (
        <div className="rolodex-card-section">
          <h3>Responsibilities</h3>
          <Roles roles={agent.roles} />
        </div>
      )}
      <footer className="rolodex-card-foot">
        <a href={page}>Open full record</a>
      </footer>
    </article>
  );
}
/** The repositories or roadmaps on a card: current ties first, each with when and why it is tied. */
function CardTies<T extends Tied>({
  title,
  ties,
  name,
  href,
  empty,
  page,
}: {
  title: string;
  ties: { items: T[]; total: number };
  name: (tie: T) => string;
  href: (tie: T) => string;
  empty: string;
  page: string;
}) {
  const shown = ties.items.slice(0, SHOWN);
  const rest = ties.total - shown.length;
  return (
    <section className="rolodex-ties" aria-label={title}>
      <h3>
        {title} <span className="count">{ties.total}</span>
      </h3>
      {shown.length ? (
        <ul>
          {shown.map((tie) => (
            <li key={tie.id}>
              <span className="rolodex-tie-name">
                <a href={href(tie)}>{name(tie)}</a>
                {tie.archived && <small> (archived)</small>}
              </span>
              {tie.current ? (
                <span className="rolodex-current">Current</span>
              ) : (
                tie.lastAt && (
                  <time
                    dateTime={tie.lastAt}
                    title={exactDiscussionDate(tie.lastAt)}
                  >
                    {formatDiscussionDate(tie.lastAt)}
                  </time>
                )
              )}
              <small className="rolodex-why">
                {tie.via.map((via) => reasons[via]).join(" · ")}
              </small>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{empty}</p>
      )}
      {rest > 0 && (
        <a className="rolodex-more" href={page}>
          {rest} more on its page
        </a>
      )}
    </section>
  );
}
function AgentFacts({ agent }: { agent: AgentSummary }) {
  return (
    <p className="muted">
      {agent.providers.join(", ") || "Provider unspecified"} · {agent.openRuns}{" "}
      open of {count(agent.runCount, "run")} ·{" "}
      {count(agent.messageCount, "message")} ·{" "}
      {agent.lastActivityAt ? (
        <>Last active {timestamp(agent.lastActivityAt)}</>
      ) : (
        "No activity yet"
      )}
    </p>
  );
}
function WorkingOn({ work }: { work: RegistryWork[] }) {
  return work.length ? (
    <ul className="agent-work" aria-label="Reported work">
      {work.map((w) => (
        <li key={w.participationId}>
          {w.item ? (
            <>
              <span className="muted">Working on</span>
              <a href={"#work/" + w.item.id}>{w.item.title}</a>
            </>
          ) : (
            <span className="muted">Working in</span>
          )}
          {w.roadmap && (
            <a className="person-badge" href={"#roadmaps/" + w.roadmap.id}>
              {w.roadmap.title}
            </a>
          )}
          {w.contexts.map((context, i) => (
            <a
              className="person-badge"
              key={i}
              href={"#repositories/" + context.repositoryId}
            >
              {context.repositoryName || context.repositoryId}
              {context.branch ? " · " + context.branch : ""}
            </a>
          ))}
          <small>
            {w.runName} run · {w.stale ? "Stale report · " : ""}Last reported{" "}
            {timestamp(w.lastSeenAt)}
          </small>
          {w.description && <p className="preserve-lines">{w.description}</p>}
        </li>
      ))}
    </ul>
  ) : null;
}
function roleHref(role: RegistryRole) {
  if (role.contextType === "item") return "#work/" + role.contextId;
  if (role.contextType === "roadmap") return "#roadmaps/" + role.contextId;
  return role.roadmap
    ? `#roadmaps/${role.roadmap.id}/${role.contextId}`
    : "#roadmaps";
}
function Roles({ roles }: { roles: RegistryRole[] }) {
  return roles.length ? (
    <ul className="agent-work" aria-label="Responsibilities">
      {roles.map((role) => (
        <li key={role.id}>
          <span className="muted">{role.label} for</span>
          <a href={roleHref(role)}>{role.contextTitle}</a>
          {role.roadmap && role.contextType !== "roadmap" && (
            <a className="person-badge" href={"#roadmaps/" + role.roadmap.id}>
              {role.roadmap.title}
            </a>
          )}
        </li>
      ))}
    </ul>
  ) : null;
}
/** What every list of an agent's ties holds: the record, and why and when it is tied. */
type Tied = {
  id: string;
  archived: boolean;
  via: TieReason[];
  current: boolean;
  lastAt: string | null;
};
function TieList<T extends Tied>({
  title,
  ties,
  empty,
  children,
}: {
  title: string;
  ties: { items: T[]; total: number };
  empty: string;
  children: (tie: T) => ReactNode;
}) {
  return (
    <section className="agent-ties" aria-label={title}>
      <h3>{title}</h3>
      {!ties.items.length && <p className="muted">{empty}</p>}
      {ties.items.map((tie) => (
        <div className="line" key={tie.id}>
          <span className="agent-tie">
            {children(tie)}
            {tie.archived && <small> (archived)</small>}
          </span>
          <span className="person-badges">
            {tie.current && <span className="badge">Current</span>}
            {tie.via.map((via) => (
              <span className="badge" key={via}>
                {reasons[via]}
              </span>
            ))}
          </span>
          {tie.lastAt && <small>{timestamp(tie.lastAt)}</small>}
        </div>
      ))}
      {ties.total > ties.items.length && (
        <p className="muted">
          Showing {ties.items.length} of {ties.total}.
        </p>
      )}
    </section>
  );
}
function AgentView({ id }: { id: string }) {
  const query = useData<AgentDetail>(
    "/agents/" + encodeURIComponent(id),
    REFRESH,
  );
  // Back on the rolodex, this agent's card is the one open.
  useEffect(() => remember(id), [id]);
  const agent = query.data;
  if (!agent)
    return (
      <>
        <ErrorMessage error={query.error} />
        {query.isPending && <p>Loading agent…</p>}
      </>
    );
  return (
    <>
      <a className="back" href="#agents">
        All agents
      </a>
      <header className="title-row">
        <h1 className="agent-title">
          <Avatar name={agent.avatar} size={40} /> {agent.label}
        </h1>
        <span className="badge">{state(agent)}</span>
      </header>
      <AgentFacts agent={agent} />
      <p className="muted">
        Apart from its ties and the chats you confirmed it in, everything here
        comes from what this agent reported. Reports are not proof that a
        process is alive and become stale after two hours.
      </p>
      <section>
        <h2>Working now</h2>
        {agent.working.length ? (
          <WorkingOn work={agent.working} />
        ) : (
          <Empty>No current participation reported.</Empty>
        )}
      </section>
      <section>
        <h2>Responsibilities</h2>
        {agent.roles.length ? (
          <Roles roles={agent.roles} />
        ) : (
          <p className="muted">No responsibility roles owned.</p>
        )}
      </section>
      <section>
        <h2>From its work</h2>
        <p className="muted">
          What its runs, participation, messages and roles tie it to, and the
          chats you confirmed it in.
        </p>
        <TieList
          title="Work items"
          ties={agent.workItems}
          empty="No work items."
        >
          {(item) => (
            <>
              <a href={"#work/" + item.id}>{item.title}</a>{" "}
              <small>{item.status}</small>
              {item.roadmap && (
                <a
                  className="person-badge"
                  href={"#roadmaps/" + item.roadmap.id}
                >
                  {item.roadmap.title}
                </a>
              )}
            </>
          )}
        </TieList>
        <TieList title="Roadmaps" ties={agent.roadmaps} empty="No roadmaps.">
          {(roadmap) => <a href={"#roadmaps/" + roadmap.id}>{roadmap.title}</a>}
        </TieList>
        <TieList
          title="Repositories"
          ties={agent.repositories}
          empty="No repositories."
        >
          {(repository) => (
            <a href={"#repositories/" + repository.id}>{repository.name}</a>
          )}
        </TieList>
        <TieList
          title="Tracked chats"
          ties={agent.chats}
          empty="No tracked chat carries a chat ID this agent reported."
        >
          {(chat) => (
            <>
              <a href={"#chats/" + chat.id}>{chat.title}</a>{" "}
              <small>
                {[chat.provider, chat.model].filter(Boolean).join(" · ") ||
                  "Provider unspecified"}{" "}
                · {chat.ownerTitle}
              </small>
            </>
          )}
        </TieList>
      </section>
      <section>
        <h2>Runs</h2>
        {!agent.runs.items.length && <p className="muted">No runs started.</p>}
        {agent.runs.items.map((run) => (
          <article className="line" key={run.id}>
            <strong>{run.displayName}</strong>
            <small>
              {run.provider || "Provider unspecified"}
              {run.createdAt && <> · Started {timestamp(run.createdAt)}</>}
              {run.endReason && <> · {run.endReason.replaceAll("_", " ")}</>}
            </small>
            {run.chats.map((chat) => (
              <a
                className="person-badge"
                key={chat.id}
                href={"#chats/" + chat.id}
              >
                {chat.title}
              </a>
            ))}
            {run.externalChatId && !run.chats.length && (
              <code>{run.externalChatId}</code>
            )}
            <EndRun run={run} />
          </article>
        ))}
        {agent.runs.total > agent.runs.items.length && (
          <p className="muted">
            Showing the latest {agent.runs.items.length} of {agent.runs.total}{" "}
            runs.
          </p>
        )}
      </section>
      <Activity id={id} />
      <AgentProposals id={id} />
      <section>
        <h2>Credentials</h2>
        {agent.credentials.map((credential) => (
          <div className="line" key={credential.id}>
            <strong>{credential.label}</strong>
            <small>
              {credential.registeredBy ? (
                <>
                  Registered by{" "}
                  <a href={"#agents/" + credential.registeredBy.id}>
                    {credential.registeredBy.label}
                  </a>
                </>
              ) : (
                "Issued"
              )}{" "}
              {credential.chatId && (
                <>
                  for chat <code>{credential.chatId}</code>{" "}
                </>
              )}
              {timestamp(credential.createdAt)} ·{" "}
              {credential.revoked ? "Revoked" : "Active"}
            </small>
          </div>
        ))}
        <p className="muted">
          Issue, rotate and revoke credentials in{" "}
          <a href="#settings">Settings</a>.
        </p>
      </section>
    </>
  );
}
function Activity({ id }: { id: string }) {
  const [offset, setOffset] = useState(0);
  const query = useData<Answer<"/api/v1/agents/{identity}/activity">>(
    `/agents/${encodeURIComponent(id)}/activity?limit=${PAGE}&offset=${offset}`,
    REFRESH,
  );
  return (
    <section aria-label="Messages and updates">
      <h2>Messages and updates</h2>
      <ErrorMessage error={query.error} />
      {query.isPending ? (
        <p>Loading activity…</p>
      ) : !query.error && !query.data?.items.length ? (
        <p className="muted">No messages or status updates yet.</p>
      ) : null}
      {(query.data?.items ?? []).map((activity) => (
        <article
          className="line agent-activity"
          key={activity.kind + activity.id}
        >
          {activity.kind === "message" ? (
            <p>
              <strong>Message</strong> in{" "}
              <a href={"#discussions/" + activity.discussionId}>
                {activity.discussionTitle}
              </a>{" "}
              · {timestamp(activity.createdAt)}
              {activity.chats.map((chat) => (
                <a
                  className="person-badge"
                  key={chat.id}
                  href={"#chats/" + chat.id}
                >
                  {chat.title}
                </a>
              ))}
            </p>
          ) : (
            <p>
              <strong>
                {activity.updateKind === "blocker"
                  ? "Blocker"
                  : activity.updateKind === "handoff"
                    ? "Handoff"
                    : "Update"}
              </strong>{" "}
              · {activity.runName} · {timestamp(activity.createdAt)}
              {activity.item && (
                <>
                  {" "}
                  ·{" "}
                  <a href={"#work/" + activity.item.id}>
                    {activity.item.title ?? "Work item"}
                  </a>
                </>
              )}
              {activity.contexts.map((context, i) => (
                <a
                  className="person-badge"
                  key={i}
                  href={"#repositories/" + context.repositoryId}
                >
                  {context.repositoryName || context.repositoryId}
                </a>
              ))}
            </p>
          )}
          <p className="preserve-lines">
            {activity.text}
            {activity.truncated ? "…" : ""}
          </p>
        </article>
      ))}
      <Pager
        offset={offset}
        total={query.data?.total ?? 0}
        onChange={setOffset}
        label="activity"
      />
    </section>
  );
}
