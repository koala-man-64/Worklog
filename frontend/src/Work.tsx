import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request, getDataContext } from "./api";
import { commands } from "./commands";
import { useAction, useData, useCommand } from "./hooks";
import {
  DraftField,
  Empty,
  ErrorMessage,
  Files,
  History,
  Notes,
  RecoverRecords,
  StatusSelect,
} from "./components";
import type { Detail, Item, List, Roadmap } from "./types";
export function CreateItem({ day }: { day?: string }) {
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState("plan");
  const action = useCommand(commands.createItem);
  return (
    <form
      className="quick-add"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!title.trim()) return;
        await action
          .mutateAsync({ title, description: "", day, worked: mode === "log" })
          .then(() => setTitle(""))
          .catch(() => undefined);
      }}
    >
      <input
        aria-label="New work item"
        placeholder={day ? "What matters today?" : "Capture a work item…"}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        maxLength={500}
      />
      {day && (
        <select
          aria-label="Capture mode"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="plan">Plan work</option>
          <option value="log">Log work already performed</option>
        </select>
      )}
      <button className="primary" disabled={action.isPending || !title.trim()}>
        Add work
      </button>
      <ErrorMessage error={action.error} />
    </form>
  );
}
export function Work({ selected }: { selected?: string }) {
  return selected ? <ItemDetail id={selected} /> : <WorkList />;
}
function WorkList() {
  const [archived, setArchived] = useState(false);
  const [status, setStatus] = useState("");
  const [view, setView] = useState("all");
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const list = useData<List<Item>>(
    `/work-items?view=${view}&archived=${archived}&limit=100&offset=${offset}&q=${encodeURIComponent(q)}${status ? "&status=" + status : ""}`,
  );
  const action = useAction();
  const move = (id: string, delta: number) => {
    action.mutate({
      path: `/work-items/${id}/move`,
      body: {
        direction: delta < 0 ? "up" : "down",
        revision: list.data?.collectionRevision,
      },
    });
  };
  return (
    <>
      <header>
        <p className="eyebrow">Your working inventory</p>
        <h1>Work</h1>
        <p className="lede">Capture it once. Keep the next move clear.</p>
      </header>
      <CreateItem />
      <div className="toolbar">
        <input
          aria-label="Filter work by text"
          placeholder="Find in work…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOffset(0);
          }}
        />
        <select
          aria-label="Work view"
          value={view}
          onChange={(e) => {
            setView(e.target.value);
            setOffset(0);
          }}
        >
          <option value="all">All work</option>
          <option value="backlog">Unscheduled backlog</option>
        </select>
        <label>
          <input
            type="checkbox"
            checked={archived}
            onChange={(e) => {
              setArchived(e.target.checked);
              setOffset(0);
            }}
          />{" "}
          Show archive
        </label>
        <select
          aria-label="Filter status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All states</option>
          {["open", "doing", "blocked", "done"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <ErrorMessage error={list.error || action.error} />
      {list.isPending ? (
        <p>Loading work…</p>
      ) : !list.data?.items.length ? (
        <Empty>A little room to think. Add your first work item above.</Empty>
      ) : (
        <div className="work-list">
          {list.data.items.map((item) => (
            <article className="work-row" key={item.id}>
              <span className={"status-dot " + item.status} />
              <div>
                <a className="item-title" href={"#work/" + item.id}>
                  {item.title}
                </a>
                <small>
                  {item.description?.slice(0, 130) || "Ready for a next step"}
                </small>
              </div>
              <StatusSelect
                value={item.status}
                onChange={(status) =>
                  action.mutate({
                    path: `/work-items/${item.id}`,
                    method: "PATCH",
                    body: { revision: item.revision, status },
                  })
                }
              />
              <div className="reorder">
                <button
                  aria-label={"Move " + item.title + " up"}
                  onClick={() => move(item.id, -1)}
                  disabled={archived || !item.isBacklog || action.isPending}
                >
                  ↑
                </button>
                <button
                  aria-label={"Move " + item.title + " down"}
                  onClick={() => move(item.id, 1)}
                  disabled={archived || !item.isBacklog || action.isPending}
                >
                  ↓
                </button>
              </div>
              <button
                className="quiet"
                onClick={() =>
                  action.mutate({
                    path: `/work-items/${item.id}/${archived ? "recover" : "archive"}`,
                    body: { revision: item.revision },
                  })
                }
              >
                {archived ? "Recover" : "Archive"}
              </button>
            </article>
          ))}
        </div>
      )}
      {(list.data?.total ?? 0) > 100 && (
        <div className="toolbar">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 100))}
          >
            Previous work
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 100, list.data?.total ?? 0)} of{" "}
            {list.data?.total}
          </span>
          <button
            disabled={offset + 100 >= (list.data?.total ?? 0)}
            onClick={() => setOffset(offset + 100)}
          >
            Next work
          </button>
        </div>
      )}
    </>
  );
}
export function ItemDetail({ id }: { id: string }) {
  const query = useData<Detail>("/work-items/" + id);
  const roads = useData<List<Roadmap>>("/roadmaps?archived=false");
  const [selectedRoad, setSelectedRoad] = useState("");
  const selectedRoadmap = useQuery({
    queryKey: [getDataContext(), "/roadmaps/" + selectedRoad],
    queryFn: () => request<Roadmap>("/roadmaps/" + selectedRoad),
    enabled: !!selectedRoad,
  });
  const action = useAction();
  const [task, setTask] = useState("");
  const [path, setPath] = useState("");
  const [date, setDate] = useState("");
  const item = query.data;
  if (!item)
    return (
      <>
        <ErrorMessage error={query.error} />
        <p>Loading work item…</p>
      </>
    );
  const patch = (fields: Record<string, unknown>) =>
    action.mutate({
      path: "/work-items/" + id,
      method: "PATCH",
      body: { revision: item.revision, ...fields },
    });
  return (
    <>
      <a className="back" href="#work">
        ← All work
      </a>
      <header>
        <p className="eyebrow">Work item</p>
        <h1>{item.title}</h1>
        <div className="toolbar">
          <StatusSelect
            value={item.status}
            onChange={(status) => patch({ status })}
          />
          <button
            onClick={() =>
              action.mutate({
                path: `/work-items/${id}/${item.archived ? "recover" : "archive"}`,
                body: { revision: item.revision },
              })
            }
          >
            {item.archived ? "Recover" : "Archive"}
          </button>
        </div>
      </header>
      <ErrorMessage error={action.error} />
      <DraftField
        draftKey={"item-title-" + id}
        label="Title"
        initial={item.title}
        revision={item.revision}
        multiline={false}
        save={(title, revision) => commands.updateItem(id, { revision, title })}
      />
      <DraftField
        draftKey={"item-description-" + id}
        label="Description · Markdown"
        initial={item.description}
        revision={item.revision}
        save={(description, revision) =>
          commands.updateItem(id, { revision, description })
        }
      />
      <div className="two-column">
        <section>
          <h3>Context</h3>
          <label>
            Azure DevOps link
            <input
              type="url"
              defaultValue={item.adoUrl ?? ""}
              onBlur={(e) => {
                if (e.target.value !== (item.adoUrl ?? ""))
                  patch({ adoUrl: e.target.value || null });
              }}
              placeholder="https://dev.azure.com/…"
            />
          </label>
          {item.adoUrl && /^https?:\/\//i.test(item.adoUrl) && (
            <a href={item.adoUrl} target="_blank" rel="noreferrer">
              Open work item ↗
            </a>
          )}
          <label>
            Roadmap
            <select
              value={selectedRoad}
              onChange={(e) => setSelectedRoad(e.target.value)}
            >
              <option value="">Choose a roadmap to link a step…</option>
              {roads.data?.items.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.title}
                </option>
              ))}
            </select>
          </label>
          {item.linkedStep && (
            <p>
              <a href={"#roadmaps/" + item.linkedStep.roadmapId}>
                {item.linkedStep.roadmapTitle} / {item.linkedStep.title}
              </a>
            </p>
          )}
          <label>
            Roadmap step
            <select
              value={item.stepId ?? ""}
              onChange={(e) => patch({ stepId: e.target.value || null })}
            >
              <option value="">No linked step</option>
              {item.stepId &&
                !selectedRoadmap.data?.steps.some(
                  (s) => s.id === item.stepId,
                ) && (
                  <option value={item.stepId}>
                    {item.linkedStep?.title ?? "Current linked step"}
                  </option>
                )}
              {selectedRoadmap.data?.steps
                ?.filter((s) => !s.retired)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
            </select>
          </label>
          <form
            className="inline"
            onSubmit={(e) => {
              e.preventDefault();
              action.mutate({
                path: `/days/${date}/entries/${id}`,
                method: "PUT",
                body: { revision: 0, planned: true },
              });
            }}
          >
            <input
              aria-label="Schedule date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
            />
            <button>Plan for day</button>
          </form>
        </section>
        <section>
          <h3>Checklist</h3>
          {item.tasks
            ?.filter((t) => !t.archived)
            .map((t) => (
              <div className="line" key={t.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={t.done}
                    onChange={(e) =>
                      action.mutate({
                        path: "/tasks/" + t.id,
                        method: "PATCH",
                        body: { revision: t.revision, done: e.target.checked },
                      })
                    }
                  />
                  {t.text}
                </label>
                <button
                  className="quiet"
                  onClick={() =>
                    action.mutate({
                      path: `/tasks/${t.id}/archive`,
                      body: { revision: t.revision },
                    })
                  }
                >
                  Archive
                </button>
                <details className="task-edit">
                  <summary>Edit task</summary>
                  <DraftField
                    draftKey={"task-" + t.id}
                    label="Task text"
                    initial={t.text}
                    revision={t.revision}
                    multiline={false}
                    save={(text, revision) =>
                      request("/tasks/" + t.id, "PATCH", { text, revision })
                    }
                  />
                </details>
              </div>
            ))}
          <form
            className="inline"
            onSubmit={async (e) => {
              e.preventDefault();
              await action
                .mutateAsync({
                  path: "/tasks",
                  body: { itemId: id, text: task, done: false },
                })
                .then(() => setTask(""))
                .catch(() => undefined);
            }}
          >
            <input
              aria-label="New checklist task"
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Small next action"
              required
            />
            <button>Add</button>
          </form>
        </section>
      </div>
      <Notes ownerType="item" ownerId={id} notes={item.notes} />
      {!!item.dailyEntries?.length && (
        <section>
          <h3>Daily record</h3>
          {item.dailyEntries.map((entry) => (
            <div className="line" key={entry.id}>
              <a href={"#today/" + entry.date}>{entry.date}</a>
              <small>
                {entry.planned ? "Planned · " : ""}
                {entry.worked ? "Worked" : ""}
              </small>
              <span>{entry.outcome || "No outcome recorded"}</span>
            </div>
          ))}
        </section>
      )}
      <div className="two-column">
        <Files ownerType="item" ownerId={id} files={item.attachments} />
        <section>
          <h3>Local paths</h3>
          <p className="muted">
            References stay on this machine. Copy a path to open it yourself.
          </p>
          {item.references
            ?.filter((r) => !r.archived)
            .map((r) => (
              <div className="line" key={r.id}>
                <code>{r.path}</code>
                <button onClick={() => navigator.clipboard.writeText(r.path)}>
                  Copy
                </button>
                <button
                  className="quiet"
                  onClick={() =>
                    action.mutate({
                      path: `/references/${r.id}/archive`,
                      body: { revision: r.revision },
                    })
                  }
                >
                  Archive
                </button>
              </div>
            ))}
          <form
            className="inline"
            onSubmit={async (e) => {
              e.preventDefault();
              await action
                .mutateAsync({
                  path: "/references",
                  body: { itemId: id, path },
                })
                .then(() => setPath(""))
                .catch(() => undefined);
            }}
          >
            <input
              aria-label="Local path"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              required
              placeholder="C:\…"
            />
            <button>Add path</button>
          </form>
        </section>
      </div>
      <RecoverRecords
        groups={[
          { label: "Notes", path: "notes", records: item.notes },
          { label: "Tasks", path: "tasks", records: item.tasks },
          { label: "Files", path: "attachments", records: item.attachments },
          { label: "Paths", path: "references", records: item.references },
        ]}
      />
      <History items={item.history} />
    </>
  );
}
