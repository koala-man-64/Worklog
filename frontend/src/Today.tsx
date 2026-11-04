import { useState } from "react";
import { request, getDataContext } from "./api";
import { commands } from "./commands";
import { useAction, useData } from "./hooks";
import {
  DraftField,
  Empty,
  ErrorMessage,
  StatusSelect,
  Markdown,
} from "./components";
import { CreateItem } from "./Work";
import type { Day, Entry, Item, List, Status } from "./types";
import { Icon } from "./icons";
// The work Today offers to plan: anything not finished.
const OPEN_STATES: Status[] = ["open", "doing", "blocked"];
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
  const openWork = useData<List<Item>>(
    `/work-items?view=all&archived=false&limit=100${OPEN_STATES.map((s) => "&status=" + s).join("")}&q=${encodeURIComponent(workQuery)}`,
  );
  const action = useAction();
  // One entry's reschedule picker is open at a time.
  const [rescheduling, setRescheduling] = useState<string | null>(null);
  const [target, setTarget] = useState("");
  const [rescheduleError, setRescheduleError] = useState<unknown>(null);
  const [dragged, setDragged] = useState<string | null>(null);
  const data = day.data;
  const weekday = new Date(date + "T12:00:00").toLocaleDateString(undefined, {
    weekday: "long",
  });
  const planLabel = date === today ? "Work on today" : "Work on " + weekday;
  const planFor = (title: string) =>
    date === today
      ? `Work on ${title} today`
      : `Work on ${title} on ${weekday}`;
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
    action.mutate({
      path: `/days/${date}/reorder`,
      body: { revision: data.revision, ids },
    });
  };
  const closeReschedule = () => {
    setRescheduling(null);
    setTarget("");
  };
  const reschedule = async (entry: Entry) => {
    if (!target || target === date) return;
    try {
      const other = await request<Day>("/days/" + target);
      await action.mutateAsync({
        path: `/days/${date}/reschedule`,
        body: {
          itemId: entry.itemId,
          targetDate: target,
          revision: entry.revision,
          targetRevision:
            other.entries.find((e) => e.itemId === entry.itemId)?.revision ?? 0,
        },
      });
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
          <h2>
            The day’s work <span className="count">{data.entries.length}</span>
          </h2>
          {!data.entries.length && (
            <Empty>
              No work planned yet. Add new work above or pick from open work
              below.
            </Empty>
          )}
          {data.entries.map((entry, index) => (
            <article
              className={
                "day-entry" + (dragged === entry.id ? " dragging" : "")
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
                  action.mutate({
                    path: `/days/${date}/reorder`,
                    body: { revision: data.revision, ids },
                  });
                } catch (error) {
                  setRescheduleError(error);
                }
              }}
            >
              <div className="section-heading">
                <div>
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
                  <a className="item-title" href={"#work/" + entry.itemId}>
                    {entry.item.title}
                  </a>
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
                        setRescheduling(entry.id);
                      }
                    }}
                  >
                    Reschedule
                  </button>
                </div>
                <StatusSelect
                  disabled={entry.item.archived}
                  value={entry.item.status}
                  onChange={(status) =>
                    action.mutate({
                      path: "/work-items/" + entry.itemId,
                      method: "PATCH",
                      body: { revision: entry.item.revision, status },
                    })
                  }
                />
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
              <div className="toolbar">
                <label>
                  <input
                    type="checkbox"
                    checked={entry.planned}
                    disabled={entry.item.archived}
                    onChange={(e) =>
                      action.mutate({
                        path: `/days/${date}/entries/${entry.itemId}`,
                        method: "PUT",
                        body: {
                          revision: entry.revision,
                          planned: e.target.checked,
                        },
                      })
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
                      action.mutate({
                        path: `/days/${date}/entries/${entry.itemId}`,
                        method: "PUT",
                        body: {
                          revision: entry.revision,
                          worked: e.target.checked,
                        },
                      })
                    }
                  />{" "}
                  Worked on
                </label>
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
              {entry.item.archived ? (
                <section className="draft">
                  <label>Outcome / next action</label>
                  <Markdown text={entry.outcome} />
                </section>
              ) : (
                <DraftField
                  draftKey={"entry-" + entry.id}
                  label="Outcome / next action"
                  initial={entry.outcome}
                  revision={entry.revision}
                  save={(outcome, revision) =>
                    commands.upsertEntry(date, entry.itemId, {
                      revision,
                      outcome,
                    })
                  }
                />
              )}
            </article>
          ))}
          <section className="open-work">
            <div className="section-heading">
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
            {candidates.map((item) => (
              <div className="line" key={item.id}>
                <span
                  className={"status-dot " + item.status}
                  aria-hidden="true"
                />
                <a className="item-title" href={"#work/" + item.id}>
                  {item.title}
                </a>
                <button
                  aria-label={planFor(item.title)}
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate({
                      path: `/days/${date}/entries/${item.id}`,
                      method: "PUT",
                      body: { revision: 0, planned: true },
                    })
                  }
                >
                  {planLabel}
                </button>
              </div>
            ))}
            {(openWork.data?.total ?? 0) > 100 && (
              <small>Showing the first 100 matches. Refine your search.</small>
            )}
          </section>
          <DraftField
            key={date}
            draftKey={"reflection-" + date}
            label="End-of-day reflection (Markdown)"
            initial={data.reflection}
            revision={data.revision}
            save={(text, revision) =>
              request(`/days/${date}/reflection`, "PUT", { revision, text })
            }
          />
        </>
      )}
    </>
  );
}
