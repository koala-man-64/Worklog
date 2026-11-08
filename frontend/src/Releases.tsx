import { useRef, useState } from "react";
import { commands } from "./commands";
import { Pages } from "./Coordination";
import { History } from "./sections/History";
import { RecoverRecords } from "./sections/RecoverRecords";
import { DraftField } from "./ui/DraftField";
import { Markdown } from "./ui/Markdown";
import { Empty, ErrorMessage } from "./ui/Messages";
import { ITEM_STATUSES, statusLabel, StatusSelect } from "./ui/Status";
import { useCommand, useData, useWrites } from "./hooks";
import { Icon } from "./icons";
import { LinkPicker } from "./LinkPicker";
import type {
  Answer,
  CheckKind,
  Item,
  Person,
  ReleaseCheck,
  ReleaseCounts,
  ReleaseDetail,
  ReleaseRow,
  ReleaseStatus,
  ReleaseTarget,
  Schema,
} from "./apiTypes";

const PAGE = 50;
const STATUSES: Record<ReleaseStatus, string> = {
  planned: "Planned",
  released: "Released",
  cancelled: "Cancelled",
};
const VIEWS = {
  planned: "Upcoming",
  released: "Released",
  cancelled: "Cancelled",
  all: "All releases",
};
type View = keyof typeof VIEWS;
/** A release's three lists of checks, in the order its page shows them. */
const CHECKS: {
  kind: CheckKind;
  heading: string;
  lede: string;
  label: string;
  placeholder: string;
  action: string;
  none: string;
}[] = [
  {
    kind: "notify",
    heading: "Who needs to know",
    lede: "People and groups to tell, and what to tell them.",
    label: "New person or group to tell",
    placeholder: "Who to tell, and what…",
    action: "Add who to tell",
    none: "No one to tell yet.",
  },
  {
    kind: "test",
    heading: "What to test",
    lede: "What has to be tested before it ships, and where.",
    label: "New test",
    placeholder: "What to test, and where…",
    action: "Add test",
    none: "Nothing to test yet.",
  },
  {
    kind: "prepare",
    heading: "Before release",
    lede: "Configuration and requests to handle ahead of time.",
    label: "New preparation",
    placeholder: "A setting to change or a request to make first…",
    action: "Add preparation",
    none: "Nothing to prepare yet.",
  },
];
/** A search result, as the roadmap, step and GitHub pickers list them. */
type Hit = { id: string; type: string; title: string; snippet: string };

/** A day such as "Nov 20", with the year when it is not this one. */
export function releaseDay(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(year === new Date().getFullYear() ? {} : { year: "numeric" }),
  });
}
const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
/** A list row's summary: its work, then what is still open, such as "1 of 3 work items done · 2 to tell". */
export function releaseCounts(counts: ReleaseCounts) {
  return [
    counts.items
      ? `${counts.itemsDone} of ${plural(counts.items, "work item")} done`
      : "No work items",
    counts.notify && `${counts.notify} to tell`,
    counts.test && `${counts.test} to test`,
    counts.prepare && `${counts.prepare} to prepare`,
    counts.pastDue && `${counts.pastDue} past due`,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * Releases: what ships together and when, with the work, roadmaps and changes each holds,
 * and who needs to know, what to test and what to prepare first. `#releases/<id>` opens one.
 */
export function Releases({ selected }: { selected?: string }) {
  return selected ? (
    <ReleaseView key={selected} id={selected} />
  ) : (
    <ReleaseList />
  );
}

/** Adds a release by its name, and its date if it has one, then opens it. */
function NewRelease() {
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const create = useCommand(commands.createRelease);
  return (
    <form
      className="quick-add"
      onSubmit={(e) => {
        e.preventDefault();
        if (!title.trim()) return;
        create.mutate(
          { title: title.trim(), releaseDate: date || null },
          {
            onSuccess: (release) => {
              setTitle("");
              setDate("");
              location.hash = "#releases/" + release.id;
            },
          },
        );
      }}
    >
      <input
        aria-label="New release title"
        placeholder="Name a release, such as November platform release…"
        required
        maxLength={1000}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <label className="release-new-date">
        Release date
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
      </label>
      <button className="primary" disabled={!title.trim() || create.isPending}>
        Add release
      </button>
      <ErrorMessage error={create.error} />
    </form>
  );
}

function ReleaseRowView({ release }: { release: ReleaseRow }) {
  return (
    <article className="work-row release-row">
      <span
        className={"release-when" + (release.pastDue ? " past-due" : "")}
        title={release.releaseDate ?? undefined}
      >
        {release.releaseDate ? releaseDay(release.releaseDate) : "Not dated"}
      </span>
      <div>
        <a className="item-title" href={"#releases/" + release.id}>
          {release.title}
        </a>{" "}
        {release.status !== "planned" && (
          <span className="badge">{STATUSES[release.status]}</span>
        )}
        {release.pastDue && <span className="badge past-due">Past due</span>}
        <small>{releaseCounts(release.counts)}</small>
      </div>
    </article>
  );
}

function ReleaseList() {
  const [view, setView] = useState<View>("planned");
  const [q, setQ] = useState("");
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({
    status: view,
    archived: String(archived),
    limit: String(PAGE),
    offset: String(offset),
  });
  if (q.trim()) params.set("q", q.trim());
  const list = useData<Answer<"/api/v1/releases">>(
    "/releases?" + params.toString(),
  );
  const items = list.data?.items ?? [];
  // Upcoming releases come soonest first, then the ones not dated yet under their own heading.
  const grouped = view === "planned";
  const dated = grouped ? items.filter((r) => r.releaseDate) : items;
  const undated = grouped ? items.filter((r) => !r.releaseDate) : [];
  const filter =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setOffset(0);
    };
  return (
    <>
      <header>
        <h1>Releases</h1>
        <p className="lede">
          What ships together and when: its work, roadmaps and changes, who
          needs to know, what to test and what to handle first.
        </p>
      </header>
      {!archived && <NewRelease />}
      <div className="toolbar release-filters">
        <input
          type="search"
          aria-label="Find releases"
          placeholder="Find releases…"
          value={q}
          onChange={(e) => filter(setQ)(e.target.value)}
        />
        <select
          aria-label="Release status"
          value={view}
          onChange={(e) => filter(setView)(e.target.value as View)}
        >
          {Object.entries(VIEWS).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={archived}
            onChange={(e) => {
              // The archive holds releases of every status, so it opens on all of them.
              setView(e.target.checked ? "all" : "planned");
              filter(setArchived)(e.target.checked);
            }}
          />{" "}
          Show archive
        </label>
      </div>
      <ErrorMessage error={list.error} />
      {list.isPending ? (
        <p>Loading releases…</p>
      ) : !list.error && !items.length ? (
        <Empty>
          {archived
            ? "No archived releases match."
            : q.trim() || view !== "planned"
              ? "No releases match."
              : "No upcoming releases. Name one above, with its date if it has one."}
        </Empty>
      ) : null}
      <div className="work-list">
        {dated.map((release) => (
          <ReleaseRowView key={release.id} release={release} />
        ))}
      </div>
      {!!undated.length && (
        <>
          <h2 className="release-group">Not dated</h2>
          <div className="work-list">
            {undated.map((release) => (
              <ReleaseRowView key={release.id} release={release} />
            ))}
          </div>
        </>
      )}
      <Pages
        offset={offset}
        total={list.data?.total ?? 0}
        change={setOffset}
        label="releases"
      />
    </>
  );
}

/**
 * A date that saves when you leave the field or press Enter, never part way through typing
 * one; an emptied field clears it. Give it a key of the saved value, so a save resets it.
 */
function DayInput({
  label,
  value,
  disabled,
  onCommit,
}: {
  label: string;
  value: string | null;
  disabled: boolean;
  onCommit: (value: string | null) => void;
}) {
  const [draft, setDraft] = useState(value ?? "");
  const input = useRef<HTMLInputElement>(null);
  const commit = () => {
    // A date typed part way reads as empty while the browser marks it bad input, such as one whose
    // month was cleared; a year typed part way reads as a year before 1000. Neither is a date to
    // save, so the field goes back to the saved one instead of clearing or moving it.
    if (input.current?.validity.badInput || (draft && draft < "1000")) {
      if (input.current) input.current.value = value ?? "";
      setDraft(value ?? "");
      return;
    }
    const next = draft || null;
    if (next !== value) onCommit(next);
  };
  return (
    <label className="day-input">
      {label}
      <input
        ref={input}
        type="date"
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          e.preventDefault();
          commit();
        }}
      />
    </label>
  );
}

function ReleaseView({ id }: { id: string }) {
  const query = useData<ReleaseDetail>("/releases/" + id);
  const people = useData<Answer<"/api/v1/people">>("/people");
  const action = useWrites();
  const release = query.data;
  if (!release)
    return (
      <>
        <ErrorMessage error={query.error} />
        {query.isPending && <p>Loading release…</p>}
      </>
    );
  const locked = release.archived || action.isPending;
  const patch = (fields: Omit<Schema["ReleaseUpdate"], "revision">) =>
    action.mutate(() =>
      commands.updateRelease(id, { revision: release.revision, ...fields }),
    );
  const hold = (targetType: ReleaseTarget, targetId: string) =>
    action.mutate(() =>
      commands.holdInRelease({ releaseId: id, targetType, targetId }),
    );
  const remove = (targetType: ReleaseTarget, targetId: string) =>
    action.mutate(() =>
      commands.removeFromRelease({ releaseId: id, targetType, targetId }),
    );
  const removeButton = (
    targetType: ReleaseTarget,
    targetId: string,
    name: string,
  ) =>
    !release.archived && (
      <button
        className="quiet"
        aria-label={`Remove ${name} from the release`}
        disabled={action.isPending}
        onClick={() => remove(targetType, targetId)}
      >
        Remove
      </button>
    );
  const heldItems = new Set(release.items.map((i) => i.id));
  const heldPlans = new Set([
    ...release.roadmaps.map((r) => r.id),
    ...release.steps.map((s) => s.id),
  ]);
  const heldChanges = new Set(
    release.changes.filter((c) => c.direct).map((c) => c.id),
  );
  return (
    <>
      <a className="back" href="#releases">
        <Icon name="back" />
        All releases
      </a>
      <header className="title-row">
        <h1>{release.title}</h1>
        <div className="toolbar">
          <select
            aria-label="Release status"
            value={release.status}
            disabled={locked}
            onChange={(e) => patch({ status: e.target.value as ReleaseStatus })}
          >
            {Object.entries(STATUSES).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
          <button
            disabled={action.isPending}
            onClick={() =>
              action.mutate(() =>
                commands.setReleaseArchived(id, !release.archived, {
                  revision: release.revision,
                }),
              )
            }
          >
            {release.archived ? "Recover" : "Archive"}
          </button>
        </div>
      </header>
      {release.archived && (
        <p className="warning">
          Archived: this release is out of the list and read-only. Recover it to
          change it.
        </p>
      )}
      <ErrorMessage error={action.error} />
      <DraftField
        draftKey={"release-title-" + id}
        label="Title"
        initial={release.title}
        revision={release.revision}
        multiline={false}
        maxLength={1000}
        disabled={release.archived}
        save={(title, revision) =>
          commands.updateRelease(id, { revision, title })
        }
      />
      <div className="toolbar release-date-field">
        <DayInput
          key={release.releaseDate ?? ""}
          label="Release date"
          value={release.releaseDate}
          disabled={locked}
          onCommit={(releaseDate) => patch({ releaseDate })}
        />
        {release.releaseDate ? (
          <button
            className="quiet"
            disabled={locked}
            onClick={() => patch({ releaseDate: null })}
          >
            Clear date
          </button>
        ) : (
          <span className="muted">Not dated</span>
        )}
        {release.pastDue && <span className="badge past-due">Past due</span>}
      </div>
      <DraftField
        draftKey={"release-description-" + id}
        label="Description (Markdown)"
        initial={release.description}
        revision={release.revision}
        maxLength={1000000}
        disabled={release.archived}
        placeholder="What ships, why it matters, and the window it goes out in"
        save={(description, revision) =>
          commands.updateRelease(id, { revision, description })
        }
      />
      <section>
        <h2>
          Work items <span className="count">{release.items.length}</span>
        </h2>
        {release.items.map((item) => (
          <div className="line release-line" key={item.id}>
            <span className={"status-dot " + item.status} />
            <a href={"#work/" + item.id}>{item.title}</a>
            <small>
              {statusLabel(item.status)}
              {item.archived ? " · archived" : ""}
            </small>
            {removeButton("item", item.id, item.title)}
          </div>
        ))}
        {!release.items.length && (
          <p className="muted">No work items in this release yet.</p>
        )}
        {!release.archived && (
          <div className="link-row">
            <LinkPicker<Item>
              action="Add work item"
              label="Work to add"
              placeholder="Find work…"
              path={(q) =>
                `/work-items?view=all&archived=false&limit=20&q=${encodeURIComponent(q)}`
              }
              linked={(item) => heldItems.has(item.id)}
              option={(item) => (
                <>
                  <span>{item.title}</span>
                  <small>{statusLabel(item.status)}</small>
                </>
              )}
              none="No work to add yet."
              onPick={(item) => hold("item", item.id)}
              disabled={action.isPending}
            />
          </div>
        )}
      </section>
      <section>
        <h2>
          Roadmaps and steps{" "}
          <span className="count">
            {release.roadmaps.length + release.steps.length}
          </span>
        </h2>
        {release.roadmaps.map((roadmap) => (
          <div className="line release-line" key={roadmap.id}>
            <a href={"#roadmaps/" + roadmap.id}>{roadmap.title}</a>
            <small>
              {roadmap.steps
                ? `${roadmap.stepsDone} of ${plural(roadmap.steps, "step")} done`
                : "No steps"}
              {roadmap.archived ? " · archived" : ""}
            </small>
            {removeButton("roadmap", roadmap.id, roadmap.title)}
          </div>
        ))}
        {release.steps.map((step) => (
          <div className="line release-line" key={step.id}>
            <span className={"status-dot " + step.status} />
            <a href={`#roadmaps/${step.roadmapId}/${step.id}`}>
              {step.roadmapTitle} / {step.title}
            </a>
            <small>
              {step.retired ? "Retired" : statusLabel(step.status)}
              {step.roadmapArchived ? " · archived roadmap" : ""}
            </small>
            {removeButton("step", step.id, step.title)}
          </div>
        ))}
        {!release.roadmaps.length && !release.steps.length && (
          <p className="muted">No roadmaps or steps in this release yet.</p>
        )}
        {!release.archived && (
          <div className="link-row">
            <LinkPicker<Hit>
              action="Add roadmap or step"
              label="Roadmap or step to add"
              placeholder="Find roadmaps and steps…"
              path={(q) =>
                `/search?archived=false&limit=20&type=roadmap&type=step&q=${encodeURIComponent(q)}`
              }
              linked={(hit) => heldPlans.has(hit.id)}
              option={(hit) => (
                <>
                  <span>{hit.title}</span>
                  <small>{hit.type === "step" ? "Step" : "Roadmap"}</small>
                </>
              )}
              none="Type a roadmap's or step's name."
              onPick={(hit) =>
                hold(hit.type === "step" ? "step" : "roadmap", hit.id)
              }
              disabled={action.isPending}
            />
          </div>
        )}
      </section>
      <section>
        <h2>
          Changes <span className="count">{release.changes.length}</span>
        </h2>
        <p className="muted">
          The PRs and commits on this release’s work items and roadmaps, and any
          you add here, with the agent chat that made each.
        </p>
        {release.changes.map((change) => (
          <div className="line release-line" key={change.id}>
            <span className="badge">
              {change.kind === "pr" ? "PR" : "Commit"}
            </span>
            <a href={change.url} target="_blank" rel="noreferrer">
              {change.reference || change.url}
              {change.title ? " " + change.title : ""} ↗
            </a>
            <small>
              From{" "}
              <a
                href={
                  (change.ownerType === "item" ? "#work/" : "#roadmaps/") +
                  change.ownerId
                }
              >
                {change.ownerTitle ?? "its record"}
              </a>
              {change.chatId && change.chatTitle && (
                <>
                  {" "}
                  · Made in{" "}
                  <a href={"#chats/" + change.chatId}>{change.chatTitle}</a>
                </>
              )}
              {change.archived ? " · archived" : ""}
            </small>
            {change.direct &&
              removeButton(
                "github",
                change.id,
                change.reference || change.title || change.url,
              )}
          </div>
        ))}
        {!release.changes.length && (
          <p className="muted">
            No changes yet: add work items with PRs or commits, or a single PR
            or commit.
          </p>
        )}
        {!release.archived && (
          <div className="link-row">
            <LinkPicker<Hit>
              action="Add PR or commit"
              label="PR or commit to add"
              placeholder="Find PRs and commits…"
              path={(q) =>
                `/search?archived=false&limit=20&type=github&q=${encodeURIComponent(q)}`
              }
              linked={(hit) => heldChanges.has(hit.id)}
              option={(hit) => (
                <>
                  <span>{hit.title}</span>
                  <small>{hit.snippet}</small>
                </>
              )}
              none="Type words from a PR's or commit's title or address."
              onPick={(hit) => hold("github", hit.id)}
              disabled={action.isPending}
            />
          </div>
        )}
      </section>
      {CHECKS.map((spec) => (
        <Checks
          key={spec.kind}
          spec={spec}
          release={release}
          people={people.data?.items ?? []}
        />
      ))}
      <RecoverRecords
        groups={[
          {
            label: "Release checks",
            path: "release-checks",
            records: release.archived ? [] : release.checks,
          },
        ]}
      />
      <History items={release.history} />
    </>
  );
}

/** One of a release's lists of checks, with a way to add one. */
function Checks({
  spec,
  release,
  people,
}: {
  spec: (typeof CHECKS)[number];
  release: ReleaseDetail;
  people: Person[];
}) {
  const [text, setText] = useState("");
  const create = useCommand(commands.createReleaseCheck);
  const checks = release.checks.filter(
    (check) => check.kind === spec.kind && !check.archived,
  );
  return (
    <section className="release-checks">
      <h2>
        {spec.heading} <span className="count">{checks.length}</span>
      </h2>
      <p className="muted">{spec.lede}</p>
      {checks.map((check) => (
        <CheckRow
          key={check.id}
          check={check}
          people={people}
          locked={release.archived}
        />
      ))}
      {!checks.length && <p className="muted">{spec.none}</p>}
      {!release.archived && (
        <form
          className="inline"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            create.mutate(
              { releaseId: release.id, kind: spec.kind, text: text.trim() },
              { onSuccess: () => setText("") },
            );
          }}
        >
          <input
            aria-label={spec.label}
            placeholder={spec.placeholder}
            maxLength={2000}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button disabled={!text.trim() || create.isPending}>
            {spec.action}
          </button>
        </form>
      )}
      <ErrorMessage error={create.error} />
    </section>
  );
}

/** A check's text, its person, due date and status, its details, and Archive. */
function CheckRow({
  check,
  people,
  locked,
}: {
  check: ReleaseCheck;
  people: Person[];
  locked: boolean;
}) {
  const action = useWrites();
  const disabled = locked || action.isPending;
  const patch = (fields: Omit<Schema["ReleaseCheckUpdate"], "revision">) =>
    action.mutate(() =>
      commands.updateReleaseCheck(check.id, {
        revision: check.revision,
        ...fields,
      }),
    );
  // The people one could name now, and whoever the check names already, archived or not.
  const named =
    check.personId && !people.some((person) => person.id === check.personId);
  return (
    <article
      className={"release-check" + (check.pastDue ? " past-due" : "")}
      aria-label={check.text}
    >
      <div className="release-check-main">
        <span className={"status-dot " + check.status} />
        <span className="release-check-text">{check.text}</span>
        {check.pastDue && <span className="badge past-due">Past due</span>}
      </div>
      <div className="toolbar release-check-controls">
        <label>
          Person
          <select
            value={check.personId ?? ""}
            disabled={disabled}
            onChange={(e) => patch({ personId: e.target.value || null })}
          >
            <option value="">No one</option>
            {people.map((person) => (
              <option key={person.id} value={person.id}>
                {person.name}
              </option>
            ))}
            {named && (
              <option value={check.personId ?? ""}>
                {(check.personName ?? "Someone") +
                  (check.personArchived ? " (archived)" : "")}
              </option>
            )}
          </select>
        </label>
        <DayInput
          key={check.dueDate ?? ""}
          label="Due"
          value={check.dueDate}
          disabled={disabled}
          onCommit={(dueDate) => patch({ dueDate })}
        />
        <StatusSelect
          value={check.status}
          options={ITEM_STATUSES}
          disabled={disabled}
          onChange={(status) => patch({ status })}
        />
        <button
          className="quiet"
          aria-label={"Archive " + check.text}
          disabled={disabled}
          onClick={() =>
            action.mutate(() =>
              commands.setReleaseCheckArchived(check.id, true, {
                revision: check.revision,
              }),
            )
          }
        >
          Archive
        </button>
      </div>
      {check.details.trim() && (
        <div className="release-check-details">
          <Markdown text={check.details} />
        </div>
      )}
      {!locked && (
        <details className="task-edit">
          <summary>Edit</summary>
          <DraftField
            draftKey={"release-check-text-" + check.id}
            label="Check text"
            initial={check.text}
            revision={check.revision}
            multiline={false}
            maxLength={2000}
            save={(text, revision) =>
              commands.updateReleaseCheck(check.id, { revision, text })
            }
          />
          <DraftField
            draftKey={"release-check-details-" + check.id}
            label="Details (Markdown)"
            initial={check.details}
            revision={check.revision}
            maxLength={100000}
            placeholder="Where, how, links to the request or ticket"
            save={(details, revision) =>
              commands.updateReleaseCheck(check.id, { revision, details })
            }
          />
        </details>
      )}
      <ErrorMessage error={action.error} />
    </article>
  );
}

/**
 * The releases that hold a work item, roadmap or step, with a way to add it to an upcoming one.
 * Holding changes neither the record nor the release, so no draft here can conflict.
 */
export function ReleasesSection({
  targetType,
  targetId,
  disabled = false,
}: {
  targetType: "item" | "roadmap" | "step";
  targetId: string;
  disabled?: boolean;
}) {
  const held = useData<Answer<"/api/v1/releases">>(
    `/releases?status=all&targetType=${targetType}&targetId=${encodeURIComponent(targetId)}&limit=200`,
  );
  const upcoming = useData<Answer<"/api/v1/releases">>(
    "/releases?status=planned&limit=200",
  );
  const action = useWrites();
  const [pick, setPick] = useState("");
  const holding = held.data?.items ?? [];
  const ids = new Set(holding.map((release) => release.id));
  const offered = (upcoming.data?.items ?? []).filter(
    (release) => !ids.has(release.id),
  );
  return (
    <section className="release-section">
      <h3>
        Releases <span className="count">{holding.length}</span>
      </h3>
      <ErrorMessage error={held.error ?? action.error} />
      {holding.map((release) => (
        <div className="line release-line" key={release.id}>
          <a href={"#releases/" + release.id}>{release.title}</a>
          <small>
            {release.releaseDate
              ? releaseDay(release.releaseDate)
              : "Not dated"}
            {release.status !== "planned"
              ? " · " + STATUSES[release.status]
              : ""}
            {release.pastDue ? " · Past due" : ""}
          </small>
          {!release.archived && (
            <button
              className="quiet"
              aria-label={`Remove from ${release.title}`}
              disabled={disabled || action.isPending}
              onClick={() =>
                action.mutate(() =>
                  commands.removeFromRelease({
                    releaseId: release.id,
                    targetType,
                    targetId,
                  }),
                )
              }
            >
              Remove
            </button>
          )}
        </div>
      ))}
      {held.isSuccess && !holding.length && (
        <p className="muted">In no release yet.</p>
      )}
      {!disabled && (
        <form
          className="inline"
          onSubmit={(e) => {
            e.preventDefault();
            if (!pick) return;
            action.mutate(
              () =>
                commands.holdInRelease({
                  releaseId: pick,
                  targetType,
                  targetId,
                }),
              { onSuccess: () => setPick("") },
            );
          }}
        >
          <select
            aria-label="Release to add to"
            value={pick}
            onChange={(e) => setPick(e.target.value)}
          >
            <option value="">Choose an upcoming release…</option>
            {offered.map((release) => (
              <option key={release.id} value={release.id}>
                {release.title}
                {release.releaseDate
                  ? " · " + releaseDay(release.releaseDate)
                  : " · Not dated"}
              </option>
            ))}
          </select>
          <button disabled={!pick || action.isPending}>Add to release</button>
        </form>
      )}
    </section>
  );
}
