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
import type { Day, Item, List } from "./types";
import { Icon } from "./icons";
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
  const work = useData<List<Item>>(
    `/work-items?view=all&archived=false&limit=100&q=${encodeURIComponent(workQuery)}`,
  );
  const action = useAction();
  const [pick, setPick] = useState("");
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [rescheduleError, setRescheduleError] = useState<unknown>(null);
  const [dragged, setDragged] = useState<string | null>(null);
  const data = day.data;
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
  return (
    <>
      <header className="day-header">
        <div>
          <h1>
            {date === today
              ? "Today"
              : new Date(date + "T12:00:00").toLocaleDateString(undefined, {
                  weekday: "long",
                })}
          </h1>
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
      <form
        className="inline plan-picker"
        onSubmit={(e) => {
          e.preventDefault();
          if (pick)
            action.mutate({
              path: `/days/${date}/entries/${pick}`,
              method: "PUT",
              body: { revision: 0, planned: true },
            });
        }}
      >
        <input
          aria-label="Find existing work"
          placeholder="Find existing work…"
          value={workQuery}
          onChange={(e) => {
            setWorkQuery(e.target.value);
            setPick("");
          }}
        />
        <select
          aria-label="Choose existing work"
          value={pick}
          onChange={(e) => setPick(e.target.value)}
        >
          <option value="">Bring existing work into this day…</option>
          {work.data?.items
            .filter((i) => !data?.entries.some((e) => e.itemId === i.id))
            .map((i) => (
              <option value={i.id} key={i.id}>
                {i.title}
              </option>
            ))}
        </select>
        <button disabled={!pick}>Plan work</button>
        {(work.data?.total ?? 0) > 100 && (
          <small>Showing the first 100 matches. Refine your search.</small>
        )}
      </form>
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
              No work planned yet. Add work above or bring in existing work.
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
              <form
                className="inline"
                onSubmit={async (e) => {
                  e.preventDefault();
                  const target = targets[entry.id];
                  if (!target || target === date) return;
                  try {
                    const other = await request<Day>("/days/" + target);
                    action.mutate({
                      path: `/days/${date}/reschedule`,
                      body: {
                        itemId: entry.itemId,
                        targetDate: target,
                        revision: entry.revision,
                        targetRevision:
                          other.entries.find((e) => e.itemId === entry.itemId)
                            ?.revision ?? 0,
                      },
                    });
                  } catch (error) {
                    setRescheduleError(error);
                  }
                }}
              >
                <input
                  aria-label={"Reschedule " + entry.item.title}
                  type="date"
                  disabled={entry.item.archived}
                  value={targets[entry.id] ?? ""}
                  onChange={(e) =>
                    setTargets({ ...targets, [entry.id]: e.target.value })
                  }
                  required
                />
                <button disabled={entry.item.archived}>Reschedule</button>
              </form>
            </article>
          ))}
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
