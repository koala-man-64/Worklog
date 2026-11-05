import { useState } from "react";
import { request } from "./api";
import { commands } from "./commands";
import { useAction, useCommand, useData } from "./hooks";
import {
  ChangeHistory,
  DraftField,
  Empty,
  ErrorMessage,
  Markdown,
  STATUSES,
  StatusSelect,
} from "./components";
import { Pages } from "./Coordination";
import { Avatar } from "./Avatar";
import {
  RepositoryBadges,
  RepositoryFilter,
  RepositoryPicker,
} from "./RepositoryControls";
import { Icon } from "./icons";
import type { DiscussionParticipants } from "./discussionTypes";
import type { Item, List } from "./types";
import type {
  Ticket,
  TicketDetail,
  TicketKind,
  TicketPerson,
  TicketPriority,
} from "./ticketTypes";

const KINDS: TicketKind[] = ["bug", "incident", "task"];
const PRIORITIES: TicketPriority[] = ["urgent", "high", "normal", "low"];
const VIEWS = {
  active: "Open, doing or blocked",
  open: "Open",
  doing: "Doing",
  blocked: "Blocked",
  done: "Done",
  all: "All states",
};
type View = keyof typeof VIEWS;
const PAGE = 50;
const capital = (value: string) => value[0].toUpperCase() + value.slice(1);
const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
function timestamp(value: string) {
  return <time dateTime={value}>{new Date(value).toLocaleString()}</time>;
}

export function Tickets({ selected }: { selected?: string }) {
  return selected ? (
    <TicketView key={selected} id={selected} />
  ) : (
    <TicketList />
  );
}
function Who({ person }: { person: TicketPerson }) {
  return (
    <span className="ticket-person">
      <Avatar name={person.avatar} kind={person.kind} size={20} />
      {person.label}
    </span>
  );
}
function TicketBadges({ ticket }: { ticket: Ticket }) {
  return (
    <span className="ticket-badges">
      <span className={"badge ticket-kind " + ticket.kind}>
        {capital(ticket.kind)}
      </span>
      {ticket.priority !== "normal" && (
        <span className={"badge ticket-priority " + ticket.priority}>
          {capital(ticket.priority)}
        </span>
      )}
      {ticket.labels.map((label) => (
        <span className="badge" key={label}>
          {label}
        </span>
      ))}
    </span>
  );
}
function TicketRow({ ticket }: { ticket: Ticket }) {
  return (
    <article className="work-row ticket-row">
      <span className={"status-dot " + ticket.status} />
      <div>
        <a className="item-title" href={"#tickets/" + ticket.id}>
          <span className="ticket-key">{ticket.key}</span> {ticket.title}
        </a>
        <TicketBadges ticket={ticket} />
        <RepositoryBadges ids={ticket.repositoryIds} />
        <small>
          {capital(ticket.status)}
          {ticket.assignee ? " · " + ticket.assignee.label : ""} ·{" "}
          {count(ticket.commentCount, "comment")} · last activity{" "}
          {timestamp(ticket.lastActivityAt)}
        </small>
      </div>
    </article>
  );
}
/** Files a ticket from its title; the rest is written on the ticket itself. */
function NewTicket({
  itemId,
  disabled = false,
}: {
  itemId?: string;
  disabled?: boolean;
}) {
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<TicketKind>("bug");
  const [priority, setPriority] = useState<TicketPriority>("normal");
  const create = useCommand(commands.createTicket);
  return (
    <form
      className="quick-add"
      onSubmit={(e) => {
        e.preventDefault();
        if (!title.trim()) return;
        create.mutate(
          {
            title: title.trim(),
            description: "",
            kind,
            priority,
            status: "open",
            resolution: "",
            itemId: itemId ?? null,
          },
          { onSuccess: () => setTitle("") },
        );
      }}
    >
      <input
        aria-label="New ticket title"
        placeholder={
          itemId ? "File a ticket for this work…" : "What went wrong?…"
        }
        required
        maxLength={1000}
        disabled={disabled}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <select
        aria-label="New ticket kind"
        value={kind}
        disabled={disabled}
        onChange={(e) => setKind(e.target.value as TicketKind)}
      >
        {KINDS.map((k) => (
          <option key={k} value={k}>
            {capital(k)}
          </option>
        ))}
      </select>
      <select
        aria-label="New ticket priority"
        value={priority}
        disabled={disabled}
        onChange={(e) => setPriority(e.target.value as TicketPriority)}
      >
        {PRIORITIES.map((p) => (
          <option key={p} value={p}>
            {capital(p)}
          </option>
        ))}
      </select>
      <button
        className="primary"
        disabled={disabled || !title.trim() || create.isPending}
      >
        File ticket
      </button>
      <ErrorMessage error={create.error} />
    </form>
  );
}
function TicketList() {
  const [q, setQ] = useState("");
  const [view, setView] = useState<View>("active");
  const [kind, setKind] = useState("");
  const [priority, setPriority] = useState("");
  const [label, setLabel] = useState("");
  const [repositoryId, setRepositoryId] = useState("");
  const [assigneeId, setAssigneeId] = useState("");
  const [sort, setSort] = useState("priority");
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({
    status: view,
    sort,
    archived: String(archived),
    limit: String(PAGE),
    offset: String(offset),
  });
  const filters = {
    q,
    kind,
    priority,
    label: label.trim(),
    repositoryId,
    assigneeId,
  };
  for (const [key, value] of Object.entries(filters))
    if (value) params.set(key, value);
  const list = useData<List<Ticket>>("/tickets?" + params.toString());
  const participants = useData<DiscussionParticipants>(
    "/discussions/participants",
  );
  // Any filter change starts again from the first page.
  const filter =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setOffset(0);
    };
  const filtered = view !== "active" || Object.values(filters).some(Boolean);
  return (
    <>
      <header>
        <h1>Tickets</h1>
        <p className="lede">
          Issues you and your agents file, work on and close, kept as history.
        </p>
      </header>
      <NewTicket />
      <div className="toolbar ticket-filters">
        <input
          aria-label="Find tickets"
          placeholder="Find tickets…"
          value={q}
          onChange={(e) => filter(setQ)(e.target.value)}
        />
        <select
          aria-label="Filter tickets by status"
          value={view}
          onChange={(e) => filter(setView)(e.target.value as View)}
        >
          {Object.entries(VIEWS).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter tickets by kind"
          value={kind}
          onChange={(e) => filter(setKind)(e.target.value)}
        >
          <option value="">All kinds</option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {capital(k)}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter tickets by priority"
          value={priority}
          onChange={(e) => filter(setPriority)(e.target.value)}
        >
          <option value="">Any priority</option>
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {capital(p)}
            </option>
          ))}
        </select>
        <input
          aria-label="Filter tickets by label"
          placeholder="Label…"
          value={label}
          onChange={(e) => filter(setLabel)(e.target.value)}
        />
        <select
          aria-label="Filter tickets by assignee"
          value={assigneeId}
          onChange={(e) => filter(setAssigneeId)(e.target.value)}
        >
          <option value="">Anyone</option>
          <option value="none">Unassigned</option>
          {(participants.data?.items ?? [])
            .filter((p) => p.active)
            .map((p) => (
              <option key={p.id} value={p.id}>
                {p.kind === "human" ? "Assigned to you" : p.label}
              </option>
            ))}
        </select>
        <select
          aria-label="Sort tickets"
          value={sort}
          onChange={(e) => filter(setSort)(e.target.value)}
        >
          <option value="priority">Priority first</option>
          <option value="recent">Recent activity first</option>
        </select>
        <label>
          <input
            type="checkbox"
            checked={archived}
            onChange={(e) => filter(setArchived)(e.target.checked)}
          />{" "}
          Show archive
        </label>
      </div>
      <RepositoryFilter
        value={repositoryId}
        onChange={filter(setRepositoryId)}
        label="Filter tickets by repository"
      />
      <ErrorMessage error={list.error} />
      {list.isPending ? (
        <p>Loading tickets…</p>
      ) : !list.error && !list.data?.items.length ? (
        <Empty>
          {archived
            ? "No archived tickets match."
            : filtered
              ? "No tickets match these filters."
              : "No open tickets. File one above when something goes wrong."}
        </Empty>
      ) : null}
      <div className="work-list">
        {(list.data?.items ?? []).map((ticket) => (
          <TicketRow key={ticket.id} ticket={ticket} />
        ))}
      </div>
      <Pages
        offset={offset}
        total={list.data?.total ?? 0}
        change={setOffset}
        label="tickets"
      />
    </>
  );
}
function TicketView({ id }: { id: string }) {
  const query = useData<TicketDetail>("/tickets/" + id);
  const participants = useData<DiscussionParticipants>(
    "/discussions/participants",
  );
  const action = useAction();
  const ticket = query.data;
  if (!ticket)
    return (
      <>
        <ErrorMessage error={query.error} />
        {query.isPending && <p>Loading ticket…</p>}
      </>
    );
  const locked = ticket.archived || action.isPending;
  const patch = (fields: Record<string, unknown>) =>
    action.mutate({
      path: "/tickets/" + ticket.id,
      method: "PATCH",
      body: { revision: ticket.revision, ...fields },
    });
  // New assignments go to someone who can still act; a revoked assignee stays visible.
  const assignable = (participants.data?.items ?? []).filter(
    (p) => p.active || p.id === ticket.assigneeId,
  );
  return (
    <>
      <a className="back" href="#tickets">
        <Icon name="back" />
        All tickets
      </a>
      <header className="title-row">
        <h1>
          <span className="ticket-key">{ticket.key}</span> {ticket.title}
        </h1>
        <div className="toolbar">
          <StatusSelect
            value={ticket.status}
            options={STATUSES}
            disabled={locked}
            onChange={(status) => patch({ status })}
          />
          <button
            disabled={action.isPending}
            onClick={() =>
              action.mutate({
                path: `/tickets/${ticket.id}/${ticket.archived ? "recover" : "archive"}`,
                body: { revision: ticket.revision },
              })
            }
          >
            {ticket.archived ? "Recover ticket" : "Archive ticket"}
          </button>
        </div>
      </header>
      {ticket.archived && (
        <p>Archived ticket. Recover it to change it or comment.</p>
      )}
      <p className="ticket-meta">
        Filed by <Who person={ticket.author} /> · {timestamp(ticket.createdAt)}
        {ticket.closedAt && <> · Done {timestamp(ticket.closedAt)}</>}
      </p>
      <ErrorMessage error={action.error} />
      <div className="toolbar ticket-fields">
        <label>
          Kind
          <select
            aria-label="Kind"
            value={ticket.kind}
            disabled={locked}
            onChange={(e) => patch({ kind: e.target.value })}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {capital(k)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Priority
          <select
            aria-label="Priority"
            value={ticket.priority}
            disabled={locked}
            onChange={(e) => patch({ priority: e.target.value })}
          >
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {capital(p)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Assignee
          <select
            aria-label="Assignee"
            value={ticket.assigneeId ?? ""}
            disabled={locked}
            onChange={(e) => patch({ assigneeId: e.target.value || null })}
          >
            <option value="">Unassigned</option>
            {assignable.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {p.active ? "" : " (revoked)"}
              </option>
            ))}
            {ticket.assignee &&
              !assignable.some((p) => p.id === ticket.assigneeId) && (
                <option value={ticket.assignee.id}>
                  {ticket.assignee.label}
                </option>
              )}
          </select>
        </label>
      </div>
      {(
        [
          ["title", "Title", false, 1000, undefined],
          [
            "description",
            "Description",
            true,
            100000,
            "What happened, how to reproduce it, and what you expected",
          ],
          [
            "resolution",
            "Resolution (needed to mark Done)",
            true,
            100000,
            "What fixed it, or why it was closed",
          ],
        ] as const
      ).map(([field, label, multiline, maxLength, placeholder]) => (
        <DraftField
          key={field}
          draftKey={`ticket-${field}-${ticket.id}`}
          label={label}
          initial={ticket[field]}
          revision={ticket.revision}
          multiline={multiline}
          maxLength={maxLength}
          placeholder={placeholder}
          disabled={ticket.archived}
          save={(value, revision) =>
            request<Ticket>("/tickets/" + ticket.id, "PATCH", {
              revision,
              [field]: value,
            })
          }
        />
      ))}
      <Labels
        labels={ticket.labels}
        disabled={locked}
        onChange={(labels) => patch({ labels })}
      />
      <RepositoryPicker
        heading="h2"
        value={ticket.repositoryIds}
        onChange={(repositoryIds) => patch({ repositoryIds })}
        disabled={locked}
      />
      <section>
        <h2>Work item</h2>
        {ticket.item ? (
          <p>
            <a href={"#work/" + ticket.item.id}>{ticket.item.title}</a> ·{" "}
            {ticket.item.status}
            {ticket.item.archived ? " · archived" : ""}{" "}
            <button
              className="quiet"
              disabled={locked}
              onClick={() => patch({ itemId: null })}
            >
              Unlink work item
            </button>
          </p>
        ) : (
          <WorkPicker
            disabled={locked}
            onPick={(itemId) => patch({ itemId })}
          />
        )}
      </section>
      <Comments ticket={ticket} />
      <ChangeHistory id={ticket.id} changes={ticket.history.length} />
    </>
  );
}
function Labels({
  labels,
  disabled,
  onChange,
}: {
  labels: string[];
  disabled: boolean;
  onChange: (labels: string[]) => void;
}) {
  const [value, setValue] = useState("");
  return (
    <section>
      <h2>Labels</h2>
      <p className="ticket-badges">
        {labels.map((label) => (
          <span className="badge" key={label}>
            {label}{" "}
            <button
              type="button"
              className="quiet"
              aria-label={"Remove label " + label}
              disabled={disabled}
              onClick={() => onChange(labels.filter((l) => l !== label))}
            >
              ×
            </button>
          </span>
        ))}
        {!labels.length && <small>No labels.</small>}
      </p>
      <form
        className="inline"
        onSubmit={(e) => {
          e.preventDefault();
          const label = value.trim().toLowerCase();
          if (label && !labels.includes(label)) onChange([...labels, label]);
          setValue("");
        }}
      >
        <input
          aria-label="New label"
          placeholder="Add a label, such as sqlite or ci…"
          maxLength={50}
          disabled={disabled}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button disabled={disabled || !value.trim()}>Add label</button>
      </form>
    </section>
  );
}
function WorkPicker({
  disabled,
  onPick,
}: {
  disabled: boolean;
  onPick: (itemId: string) => void;
}) {
  const [q, setQ] = useState("");
  const [pick, setPick] = useState("");
  const work = useData<List<Item>>(
    `/work-items?view=all&archived=false&limit=100&q=${encodeURIComponent(q)}`,
  );
  return (
    <form
      className="inline"
      onSubmit={(e) => {
        e.preventDefault();
        if (pick) onPick(pick);
      }}
    >
      <input
        aria-label="Find existing work"
        placeholder="Find existing work…"
        disabled={disabled}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setPick("");
        }}
      />
      <select
        aria-label="Choose existing work"
        disabled={disabled}
        value={pick}
        onChange={(e) => setPick(e.target.value)}
      >
        <option value="">Link this ticket to work…</option>
        {(work.data?.items ?? []).map((i) => (
          <option key={i.id} value={i.id}>
            {i.title}
          </option>
        ))}
      </select>
      <button disabled={disabled || !pick}>Link work item</button>
      <ErrorMessage error={work.error} />
    </form>
  );
}
function Comments({ ticket }: { ticket: TicketDetail }) {
  const [text, setText] = useState("");
  const post = useCommand((body: { text: string }) =>
    commands.commentOnTicket(ticket.id, body),
  );
  const { items, total } = ticket.comments;
  const field = "ticket-comment-" + ticket.id;
  return (
    <section className="ticket-comments">
      <h2>Comments ({total})</h2>
      {total > items.length && (
        <p className="muted">
          Showing the latest {items.length} of {total}.
        </p>
      )}
      {!total && <p className="muted">No comments yet.</p>}
      {items.map((comment) => (
        <article className="ticket-comment" key={comment.id}>
          <p className="ticket-comment-head">
            <Who person={comment.author} /> · {timestamp(comment.createdAt)}
          </p>
          <Markdown text={comment.text} untrusted />
        </article>
      ))}
      {!ticket.archived && (
        <form
          className="ticket-comment-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim())
              post.mutate(
                { text: text.trim() },
                { onSuccess: () => setText("") },
              );
          }}
        >
          <label htmlFor={field}>Add a comment</label>
          <textarea
            id={field}
            maxLength={100000}
            placeholder="What you tried, found or changed. Comments can't be edited."
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button className="primary" disabled={!text.trim() || post.isPending}>
            Add comment
          </button>
          <ErrorMessage error={post.error} />
        </form>
      )}
    </section>
  );
}
/** A work item's tickets, newest activity first, with a way to file one for it. */
export function TicketsSection({
  itemId,
  disabled = false,
}: {
  itemId: string;
  disabled?: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const list = useData<List<Ticket>>(
    `/tickets?itemId=${encodeURIComponent(itemId)}&status=all&sort=recent&limit=${PAGE}&offset=${offset}`,
  );
  return (
    <section className="ticket-section">
      <h3>Tickets</h3>
      <NewTicket itemId={itemId} disabled={disabled} />
      <ErrorMessage error={list.error} />
      {list.isPending ? (
        <p>Loading tickets…</p>
      ) : !list.error && !list.data?.items.length ? (
        <p className="muted">No tickets for this work item.</p>
      ) : null}
      <div className="work-list">
        {(list.data?.items ?? []).map((ticket) => (
          <TicketRow key={ticket.id} ticket={ticket} />
        ))}
      </div>
      <Pages
        offset={offset}
        total={list.data?.total ?? 0}
        change={setOffset}
        label="tickets"
      />
    </section>
  );
}
