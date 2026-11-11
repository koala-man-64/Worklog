import { useState, type ReactNode } from "react";
import { contextStorageKey, request, getDataContext } from "./api";
import { commands } from "./commands";
import { unsavedDraft, useCollapsed, useData, useWrites } from "./hooks";
import { Files } from "./sections/Files";
import { LinksSection } from "./sections/Links";
import { Scratchpad } from "./sections/Notes";
import { DraftField } from "./ui/DraftField";
import { Markdown } from "./ui/Markdown";
import { Empty, ErrorMessage } from "./ui/Messages";
import { ITEM_STATUSES, StatusSelect } from "./ui/Status";
import { Tabs } from "./ui/Tabs";
import { CreateItem } from "./Work";
import { DaySummary } from "./DaySummary";
import type { Answer, Day, Detail, Entry, ItemStatus } from "./apiTypes";
import { Icon } from "./icons";
import { RankBadge } from "./StackRank";
import { RepositoryBadges, RepositoryPicker } from "./RepositoryControls";
// The work Today offers to plan: anything not finished.
const OPEN_STATES: ItemStatus[] = ["open", "doing", "waiting", "blocked"];
// The key an entry's "Outcome / next action" field keeps its draft under.
const outcomeDraftKey = (entry: Entry) => "entry-" + entry.id;
// What the day records for an entry beyond its plan, counting an outcome typed here and not yet
// saved. Taking the entry off the day discards it, so the person confirms that first; an entry
// that only plans the work comes off at once.
function recordedWork(entry: Entry): string | null {
  const outcome = entry.outcome.trim() !== "";
  if (entry.worked && outcome)
    return "It is marked “Worked on” and has an outcome / next action.";
  if (entry.worked) return "It is marked “Worked on”.";
  if (outcome) return "It has an outcome / next action.";
  if (unsavedDraft(outcomeDraftKey(entry))?.trim())
    return "It has an outcome / next action that isn't saved yet.";
  return null;
}
// A tab showing part of the work item. Every such tab shares one read of the item, made when
// the first of them opens.
function ItemPart({
  itemId,
  children,
}: {
  itemId: string;
  children: (item: Detail) => ReactNode;
}) {
  const item = useData<Detail>("/work-items/" + itemId);
  if (!item.data)
    return item.error ? (
      <ErrorMessage error={item.error} />
    ) : (
      <p className="muted">Loading…</p>
    );
  return <>{children(item.data)}</>;
}
// A tab title with a short count after it, such as "1/2", and its name in words for assistive
// technology. Without a count the title is the name.
function counted(title: string, shown: string | false, spoken: string) {
  return shown === false
    ? { label: title }
    : {
        label: (
          <>
            {title}
            <span className="count">{shown}</span>
          </>
        ),
        name: `${title}, ${spoken}`,
      };
}
// The work item's repositories: linking or unlinking one replaces its set under the revision
// the item was read at, so a change made elsewhere is refused, not lost.
function ItemRepositories({ item }: { item: Detail }) {
  const action = useWrites();
  return (
    <>
      <RepositoryPicker
        labelHidden
        value={item.repositoryIds ?? []}
        onChange={(repositoryIds) =>
          action.mutate(() =>
            commands.updateItem(item.id, {
              revision: item.revision,
              repositoryIds,
            }),
          )
        }
        disabled={!!item.archived || action.isPending}
      />
      <ErrorMessage error={action.error} />
    </>
  );
}
// An entry's outcome for the day, and its work item's own scratchpad, links, files and
// repositories. The tabs say what they hold from the day's counts, before the item is read.
function EntryTabs({ entry, date }: { entry: Entry; date: string }) {
  const counts = entry.counts;
  const itemId = entry.itemId;
  // The day's record of the item already lists its repositories.
  const repositories = entry.item.repositoryIds?.length ?? 0;
  return (
    <Tabs
      className="entry-tabs"
      label={entry.item.title}
      tabs={[
        {
          id: "outcome",
          label: "Outcome / next action",
          panel: () => (
            <DraftField
              draftKey={outcomeDraftKey(entry)}
              label="Outcome / next action"
              labelHidden
              initial={entry.outcome}
              revision={entry.revision}
              save={(outcome, revision) =>
                commands.upsertEntry(date, itemId, { revision, outcome })
              }
            />
          ),
        },
        {
          id: "scratchpad",
          ...counted("Scratchpad", !!counts?.scratchpad && "•", "has text"),
          panel: () => (
            <ItemPart itemId={itemId}>
              {(item) => (
                <Scratchpad itemId={itemId} notes={item.notes} labelHidden />
              )}
            </ItemPart>
          ),
        },
        {
          id: "links",
          ...counted(
            "Links",
            !!counts?.links && String(counts.links),
            String(counts?.links),
          ),
          panel: () => (
            <ItemPart itemId={itemId}>
              {(item) => (
                <LinksSection
                  ownerType="item"
                  ownerId={itemId}
                  links={item.links}
                  heading={false}
                />
              )}
            </ItemPart>
          ),
        },
        {
          id: "files",
          ...counted(
            "Files",
            !!counts?.files && String(counts.files),
            String(counts?.files),
          ),
          panel: () => (
            <ItemPart itemId={itemId}>
              {(item) => (
                <Files
                  ownerType="item"
                  ownerId={itemId}
                  files={item.attachments}
                  references={item.references ?? []}
                  heading={false}
                />
              )}
            </ItemPart>
          ),
        },
        {
          id: "repositories",
          ...counted(
            "Repositories",
            !!repositories && String(repositories),
            String(repositories),
          ),
          panel: () => (
            <ItemPart itemId={itemId}>
              {(item) => <ItemRepositories item={item} />}
            </ItemPart>
          ),
        },
      ]}
    />
  );
}
export function Today({
  today,
  initialDate,
}: {
  today: string;
  initialDate?: string;
}) {
  const [date, setDate] = useState(initialDate ?? today);
  const day = useData<Day>("/days/" + date);
  const [workQuery, setWorkQuery] = useState("");
  const openWork = useData<Answer<"/api/v1/work-items">>(
    `/work-items?view=all&archived=false&limit=100${OPEN_STATES.map((s) => "&status=" + s).join("")}&q=${encodeURIComponent(workQuery)}`,
  );
  const action = useWrites();
  // One entry's reschedule picker is open at a time.
  const [rescheduling, setRescheduling] = useState<string | null>(null);
  const [target, setTarget] = useState("");
  const [rescheduleError, setRescheduleError] = useState<unknown>(null);
  // The entry whose removal waits for the person to confirm discarding what the day records on
  // it, and what that was when they asked; the question stays until they answer it.
  const [removing, setRemoving] = useState<{
    id: string;
    recorded: string;
  } | null>(null);
  const [dragged, setDragged] = useState<string | null>(null);
  // Entries folded to their heading row, remembered per work item in this browser,
  // so an item stays folded on any day it is planned.
  const { collapsed, toggle, setClosed } = useCollapsed(
    contextStorageKey("today-collapsed", "items"),
  );
  const data = day.data;
  const itemIds = data?.entries.map((e) => e.itemId) ?? [];
  const weekday = new Date(date + "T12:00:00").toLocaleDateString(undefined, {
    weekday: "long",
  });
  const planLabel = date === today ? "Work on today" : "Work on " + weekday;
  const planFor = (title: string) =>
    date === today
      ? `Work on ${title} today`
      : `Work on ${title} on ${weekday}`;
  const dayName = date === today ? "today" : weekday;
  const removeLabel =
    date === today ? "Remove from today" : "Remove from " + weekday;
  const removeFrom = (title: string) => `Remove ${title} from ${dayName}`;
  const candidates =
    openWork.data?.items.filter(
      (i) => !data?.entries.some((e) => e.itemId === i.id),
    ) ?? [];
  const move = (from: number, delta: number) => {
    if (!data) return;
    const ids = data.entries.map((e) => e.id);
    const to = from + delta;
    if (to < 0 || to >= ids.length) return;
    [ids[from], ids[to]] = [ids[to], ids[from]];
    action.mutate(() =>
      commands.reorderDay(date, { revision: data.revision, ids }),
    );
  };
  const closeReschedule = () => {
    setRescheduling(null);
    setTarget("");
  };
  // The item stays as it is; only this day's entry for it goes, and the item returns to Open work.
  const remove = (entry: Entry) =>
    action.mutate(
      () =>
        commands.removeEntry(date, entry.itemId, { revision: entry.revision }),
      { onSuccess: () => setRemoving(null) },
    );
  // Keeping the entry closes the question and returns to the button that asked it.
  const keep = (entry: Entry) => {
    setRemoving(null);
    document.getElementById("remove-" + entry.id)?.focus();
  };
  const reschedule = async (entry: Entry) => {
    if (!target || target === date) return;
    try {
      const other = await request<Day>("/days/" + target);
      await action.mutateAsync(() =>
        commands.rescheduleEntry(date, {
          itemId: entry.itemId,
          targetDate: target,
          revision: entry.revision,
          targetRevision:
            other.entries.find((e) => e.itemId === entry.itemId)?.revision ?? 0,
        }),
      );
      closeReschedule();
    } catch (error) {
      setRescheduleError(error);
    }
  };
  return (
    <>
      <header className="day-header">
        <div>
          <h1>{date === today ? "Today" : weekday}</h1>
          <p className="lede">Plan the day and record what you worked on.</p>
        </div>
        <label className="date-label">
          Working day
          <input
            aria-label="Working day"
            type="date"
            value={date}
            onChange={(e) => {
              if (e.target.value) {
                setDate(e.target.value);
                location.hash = "today/" + e.target.value;
              }
            }}
          />
        </label>
      </header>
      <CreateItem day={date} />
      <ErrorMessage error={day.error || action.error || rescheduleError} />
      {data && (
        <>
          <div className="section-heading day-heading">
            <h2>
              The day’s work{" "}
              <span className="count">{data.entries.length}</span>
            </h2>
            {data.entries.length > 0 && (
              <div className="toolbar">
                <button
                  type="button"
                  className="quiet"
                  disabled={!itemIds.some((id) => collapsed.has(id))}
                  onClick={() => setClosed(itemIds, false)}
                >
                  Expand all
                </button>
                <button
                  type="button"
                  className="quiet"
                  disabled={itemIds.every((id) => collapsed.has(id))}
                  onClick={() => setClosed(itemIds, true)}
                >
                  Collapse all
                </button>
              </div>
            )}
            <div className="day-summary">
              <div>
                <strong>{data.summary.worked.length}</strong>
                <span>worked on</span>
              </div>
              <div>
                <strong>{data.summary.completed.length}</strong>
                <span>completed</span>
              </div>
              <div>
                <strong>{data.summary.blocked.length}</strong>
                <span>blocked</span>
              </div>
              <p>
                {data.nextItemId ? (
                  <a href={"#work/" + data.nextItemId}>
                    Continue your next open item
                  </a>
                ) : (
                  "Nothing open to continue."
                )}
              </p>
            </div>
          </div>
          {!data.entries.length && (
            <Empty>
              No work planned yet. Add new work above or pick from open work
              below.
            </Empty>
          )}
          {data.entries.map((entry, index) => (
            <article
              className={
                "day-entry" +
                (dragged === entry.id ? " dragging" : "") +
                (collapsed.has(entry.itemId) ? " collapsed" : "")
              }
              key={entry.id}
              onDragOver={(e) => {
                if (
                  e.dataTransfer.types.includes(
                    "application/x-worklog-day-entry",
                  )
                )
                  e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                setDragged(null);
                try {
                  const transfer = JSON.parse(
                    e.dataTransfer.getData("application/x-worklog-day-entry"),
                  ) as {
                    entryId: string;
                    date: string;
                    dataId: string;
                    revision: number;
                  };
                  if (
                    transfer.date !== date ||
                    transfer.dataId !== getDataContext()
                  )
                    return;
                  if (transfer.revision !== data.revision)
                    throw new Error(
                      "The day changed while dragging. Refresh its order and try again.",
                    );
                  const ids = data.entries.map((item) => item.id);
                  const from = ids.indexOf(transfer.entryId);
                  const to = ids.indexOf(entry.id);
                  if (from < 0 || to < 0 || from === to) return;
                  ids.splice(from, 1);
                  ids.splice(to, 0, transfer.entryId);
                  action.mutate(() =>
                    commands.reorderDay(date, { revision: data.revision, ids }),
                  );
                } catch (error) {
                  setRescheduleError(error);
                }
              }}
            >
              <div className="section-heading entry-head">
                <div className="entry-title">
                  <span className="entry-mark">
                    <button
                      type="button"
                      className="tree-toggle"
                      aria-expanded={!collapsed.has(entry.itemId)}
                      aria-controls={"entry-body-" + entry.id}
                      aria-label={
                        (collapsed.has(entry.itemId)
                          ? "Expand "
                          : "Collapse ") + entry.item.title
                      }
                      onClick={() => toggle(entry.itemId)}
                    >
                      <Icon
                        name={
                          collapsed.has(entry.itemId) ? "expand" : "collapse"
                        }
                        size={14}
                      />
                    </button>
                    <span className="ordinal">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span
                      className="drag-handle quiet"
                      role="img"
                      draggable={!entry.item.archived && !action.isPending}
                      aria-disabled={entry.item.archived || action.isPending}
                      aria-label={"Drag " + entry.item.title + " to reorder"}
                      title="Drag to reorder; arrow buttons also work"
                      onDragStart={(e) => {
                        e.dataTransfer.effectAllowed = "move";
                        e.dataTransfer.setData(
                          "application/x-worklog-day-entry",
                          JSON.stringify({
                            entryId: entry.id,
                            date,
                            dataId: getDataContext(),
                            revision: data.revision,
                          }),
                        );
                        setDragged(entry.id);
                      }}
                      onDragEnd={() => setDragged(null)}
                    >
                      <Icon name="grip" />
                    </span>
                    <span
                      className={"status-dot " + entry.item.status}
                      aria-hidden="true"
                    />
                  </span>
                  <span className="entry-name">
                    <a className="item-title" href={"#work/" + entry.itemId}>
                      {entry.item.title}
                    </a>
                    <RankBadge rank={entry.item.rank} />
                    <RepositoryBadges ids={entry.item.repositoryIds} />
                    {entry.item.archived && (
                      <span className="badge">
                        Archived, recover in work details
                      </span>
                    )}
                    <button
                      className="quiet reschedule"
                      aria-label={"Reschedule " + entry.item.title}
                      aria-expanded={rescheduling === entry.id}
                      disabled={entry.item.archived}
                      onClick={() => {
                        if (rescheduling === entry.id) {
                          closeReschedule();
                        } else {
                          setRescheduleError(null);
                          setTarget("");
                          setRemoving(null);
                          setRescheduling(entry.id);
                        }
                      }}
                    >
                      Reschedule
                    </button>
                    <button
                      id={"remove-" + entry.id}
                      className="quiet remove-entry"
                      aria-label={removeFrom(entry.item.title)}
                      disabled={entry.item.archived || action.isPending}
                      onClick={() => {
                        const recorded = recordedWork(entry);
                        if (!recorded) {
                          remove(entry);
                        } else {
                          closeReschedule();
                          setRemoving({ id: entry.id, recorded });
                        }
                      }}
                    >
                      {removeLabel}
                    </button>
                  </span>
                </div>
                <div className="entry-controls">
                  <label>
                    <input
                      type="checkbox"
                      checked={entry.planned}
                      disabled={entry.item.archived}
                      onChange={(e) =>
                        action.mutate(() =>
                          commands.upsertEntry(date, entry.itemId, {
                            revision: entry.revision,
                            planned: e.target.checked,
                          }),
                        )
                      }
                    />{" "}
                    Planned
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={entry.worked}
                      disabled={entry.item.archived}
                      onChange={(e) =>
                        action.mutate(() =>
                          commands.upsertEntry(date, entry.itemId, {
                            revision: entry.revision,
                            worked: e.target.checked,
                          }),
                        )
                      }
                    />{" "}
                    Worked on
                  </label>
                  <div className="reorder">
                    <button
                      aria-label={"Move " + entry.item.title + " up"}
                      disabled={index === 0 || entry.item.archived}
                      onClick={() => move(index, -1)}
                    >
                      <Icon name="up" />
                    </button>
                    <button
                      aria-label={"Move " + entry.item.title + " down"}
                      disabled={
                        index === data.entries.length - 1 || entry.item.archived
                      }
                      onClick={() => move(index, 1)}
                    >
                      <Icon name="down" />
                    </button>
                  </div>
                  <StatusSelect
                    disabled={entry.item.archived}
                    value={entry.item.status}
                    options={ITEM_STATUSES}
                    onChange={(status) =>
                      action.mutate(() =>
                        commands.updateItem(entry.itemId, {
                          revision: entry.item.revision,
                          status,
                        }),
                      )
                    }
                  />
                </div>
              </div>
              {rescheduling === entry.id && (
                <form
                  className="inline reschedule-picker"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void reschedule(entry);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") closeReschedule();
                  }}
                >
                  <input
                    aria-label={"New date for " + entry.item.title}
                    type="date"
                    value={target}
                    onChange={(e) => setTarget(e.target.value)}
                    autoFocus
                    required
                  />
                  <button
                    disabled={!target || target === date || action.isPending}
                  >
                    Move
                  </button>
                  <button
                    type="button"
                    className="quiet"
                    onClick={closeReschedule}
                  >
                    Cancel
                  </button>
                </form>
              )}
              {removing?.id === entry.id && (
                <div
                  className="inline remove-confirm"
                  role="group"
                  aria-labelledby={"remove-question-" + entry.id}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") keep(entry);
                  }}
                >
                  <span id={"remove-question-" + entry.id}>
                    {removing.recorded} Remove it from {dayName} anyway?
                  </span>
                  <button
                    disabled={action.isPending}
                    onClick={() => remove(entry)}
                  >
                    Confirm remove
                  </button>
                  {/* Focus lands on the choice that keeps the record. */}
                  <button
                    type="button"
                    className="quiet"
                    onClick={() => keep(entry)}
                    autoFocus
                  >
                    Cancel
                  </button>
                </div>
              )}
              {/* Hidden rather than unmounted when collapsed, so a draft keeps
                  saving and opened tabs keep their place. */}
              <div
                className="entry-body"
                id={"entry-body-" + entry.id}
                hidden={collapsed.has(entry.itemId)}
              >
                {entry.item.archived ? (
                  <section className="draft">
                    <label>Outcome / next action</label>
                    <Markdown text={entry.outcome} />
                  </section>
                ) : (
                  <EntryTabs entry={entry} date={date} />
                )}
              </div>
            </article>
          ))}
          <DaySummary day={data} />
          <section className="open-work">
            <div className="section-heading day-heading">
              <h2>
                Open work <span className="count">{candidates.length}</span>
              </h2>
              <input
                aria-label="Find open work"
                placeholder="Find open work…"
                value={workQuery}
                onChange={(e) => setWorkQuery(e.target.value)}
              />
            </div>
            <ErrorMessage error={openWork.error} />
            {openWork.data && !candidates.length && (
              <p className="muted">
                {workQuery ? "No open work matches." : "No other open work."}
              </p>
            )}
            {candidates.length > 0 && (
              <div className="open-list">
                {candidates.map((item) => (
                  <div className="line" key={item.id}>
                    <span
                      className={"status-dot " + item.status}
                      aria-hidden="true"
                    />
                    <span className="line-title">
                      <a className="item-title" href={"#work/" + item.id}>
                        {item.title}
                      </a>
                      <RankBadge rank={item.rank} />
                      <RepositoryBadges ids={item.repositoryIds} />
                    </span>
                    <button
                      aria-label={planFor(item.title)}
                      disabled={action.isPending}
                      onClick={() =>
                        action.mutate(() =>
                          commands.upsertEntry(date, item.id, {
                            revision: 0,
                            planned: true,
                          }),
                        )
                      }
                    >
                      {planLabel}
                    </button>
                  </div>
                ))}
              </div>
            )}
            {(openWork.data?.total ?? 0) > 100 && (
              <small>Showing the first 100 matches. Refine your search.</small>
            )}
          </section>
        </>
      )}
    </>
  );
}
