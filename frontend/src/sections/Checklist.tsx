import { useState } from "react";
import { getDataContext } from "../api";
import { commands } from "../commands";
import {
  exactDiscussionDate,
  formatDiscussionDate,
} from "../discussionFormatting";
import { useWrites } from "../hooks";
import { Icon } from "../icons";
import type { Task } from "../apiTypes";
import { DraftField } from "../ui/DraftField";
import { ErrorMessage } from "../ui/Messages";
// When a task was added and when its text or tick last changed, to the minute; the exact time
// shows on hover.
function TaskTimes({ task }: { task: Task }) {
  if (!task.createdAt || !task.updatedAt) return null;
  const stamp = (label: string, value: string) => (
    <span>
      {label}{" "}
      <time dateTime={value} title={exactDiscussionDate(value)}>
        {formatDiscussionDate(value)}
      </time>
    </span>
  );
  return (
    <small className="task-times">
      {stamp("Created", task.createdAt)}
      <span aria-hidden="true"> · </span>
      {stamp("Updated", task.updatedAt)}
    </small>
  );
}
// What a dragged task carries: the list it belongs to, so a drop on another list does nothing.
const TASK_DRAG = "application/x-worklog-task";
type TaskDrag = { taskId: string; itemId: string; dataId: string };
/**
 * A work item's tasks: tick them off, edit, archive or reorder them, and add more. Archived ones
 * are recovered with the item's other archived records.
 */
export function Checklist({
  itemId,
  tasks,
  heading = true,
}: {
  itemId: string;
  tasks: Task[];
  /** False where a tab already names the list. */
  heading?: boolean;
}) {
  const action = useWrites();
  const [text, setText] = useState("");
  // A tick shows at once and holds until the saved task comes back with a newer revision; a
  // failed save drops it. Until then the box is disabled, so a second click can't race the first.
  const [ticked, setTicked] = useState<
    Record<string, { done: boolean; from: number }>
  >({});
  const [dragged, setDragged] = useState<string | null>(null);
  const saving = (t: Task) => ticked[t.id]?.from === t.revision;
  const isDone = (t: Task) => (saving(t) ? ticked[t.id].done : t.done);
  const active = tasks.filter((t) => !t.archived);
  const done = active.filter(isDone).length;
  // A task moves to a place among the active tasks, 1 first; archived ones keep theirs. The move
  // is checked against the task's own revision and leaves every revision as it is.
  const move = (t: Task, position: number) =>
    action.mutate(() =>
      commands.moveTask(t.id, { revision: t.revision, position }),
    );
  return (
    <section className="checklist">
      {heading && (
        <h3>
          Tasks{" "}
          {active.length > 0 && (
            <span className="count">
              {done} of {active.length} done
            </span>
          )}
        </h3>
      )}
      {!active.length && <p className="muted">No tasks yet.</p>}
      {active.map((t, index) => (
        <div
          className={
            "line task" +
            (isDone(t) ? " done" : "") +
            (dragged === t.id ? " dragging" : "")
          }
          key={t.id}
          onDragOver={(e) => {
            if (!e.dataTransfer.types.includes(TASK_DRAG)) return;
            e.preventDefault();
            e.stopPropagation();
          }}
          onDrop={(e) => {
            if (!e.dataTransfer.types.includes(TASK_DRAG)) return;
            // A task's drop is this list's alone, never the day entry's around it.
            e.preventDefault();
            e.stopPropagation();
            setDragged(null);
            let drop: TaskDrag;
            try {
              drop = JSON.parse(e.dataTransfer.getData(TASK_DRAG)) as TaskDrag;
            } catch {
              return;
            }
            const moving = active.find((other) => other.id === drop.taskId);
            if (
              drop.itemId === itemId &&
              drop.dataId === getDataContext() &&
              moving &&
              moving.id !== t.id
            )
              move(moving, index + 1);
          }}
        >
          <span
            className="drag-handle quiet"
            role="img"
            draggable={!action.isPending}
            aria-disabled={action.isPending}
            aria-label={"Drag task " + t.text + " to reorder"}
            title="Drag to reorder; arrow buttons also work"
            onDragStart={(e) => {
              e.dataTransfer.effectAllowed = "move";
              e.dataTransfer.setData(
                TASK_DRAG,
                JSON.stringify({
                  taskId: t.id,
                  itemId,
                  dataId: getDataContext(),
                } satisfies TaskDrag),
              );
              setDragged(t.id);
            }}
            onDragEnd={() => setDragged(null)}
          >
            <Icon name="grip" />
          </span>
          <div className="task-main">
            <label>
              <input
                type="checkbox"
                checked={isDone(t)}
                disabled={saving(t)}
                onChange={(e) => {
                  const next = e.target.checked;
                  setTicked((was) => ({
                    ...was,
                    [t.id]: { done: next, from: t.revision },
                  }));
                  action.mutate(
                    () =>
                      commands.updateTask(t.id, {
                        revision: t.revision,
                        done: next,
                      }),
                    {
                      onError: () =>
                        setTicked((was) => {
                          const rest = { ...was };
                          delete rest[t.id];
                          return rest;
                        }),
                    },
                  );
                }}
              />
              {t.text}
            </label>
            <TaskTimes task={t} />
          </div>
          <div className="reorder">
            <button
              type="button"
              aria-label={"Move task " + t.text + " up"}
              disabled={index === 0 || action.isPending}
              onClick={() => move(t, index)}
            >
              <Icon name="up" />
            </button>
            <button
              type="button"
              aria-label={"Move task " + t.text + " down"}
              disabled={index === active.length - 1 || action.isPending}
              onClick={() => move(t, index + 2)}
            >
              <Icon name="down" />
            </button>
          </div>
          <button
            className="quiet"
            aria-label={"Archive task " + t.text}
            onClick={() =>
              action.mutate(() =>
                commands.setArchived("tasks", t.id, true, {
                  revision: t.revision,
                }),
              )
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
              save={(value, revision) =>
                commands.updateTask(t.id, { text: value, revision })
              }
            />
          </details>
        </div>
      ))}
      <form
        className="inline"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!text.trim()) return;
          await action
            .mutateAsync(() =>
              commands.createTask({ itemId, text, done: false }),
            )
            .then(() => setText(""))
            .catch(() => undefined);
        }}
      >
        <input
          aria-label="New task"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Small next action"
          required
        />
        <button disabled={action.isPending || !text.trim()}>Add task</button>
      </form>
      <ErrorMessage error={action.error} />
    </section>
  );
}
