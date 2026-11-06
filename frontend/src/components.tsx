import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { slugger } from "./guideAnchors";
import { RepositoryBadges, RepositoryPicker } from "./RepositoryControls";
import type { TieType } from "./agentTieTypes";
import { download, getDataContext } from "./api";
import { commands } from "./commands";
import {
  exactDiscussionDate,
  formatDiscussionDate,
} from "./discussionFormatting";
import { useAction, useData, useDraft } from "./hooks";
import { Icon } from "./icons";
import type {
  AgentChat,
  Attachment,
  GithubRef,
  ItemStatus,
  List,
  Note,
  Person,
  PersonAssignment,
  Reference,
  Status,
  Step,
  Task,
  WebLink,
} from "./types";
const ALLOWED_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);
function resolveLink(url: string): URL | null {
  try {
    const resolved = new URL(url, window.location.origin);
    return ALLOWED_PROTOCOLS.has(resolved.protocol) ? resolved : null;
  } catch {
    return null;
  }
}
function isSameOrigin(url: string): boolean {
  return resolveLink(url)?.origin === window.location.origin;
}
/** The parts of a Markdown syntax tree node that `anchorHeadings` reads and sets. */
type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
  data?: { hProperties?: Record<string, unknown> };
};
function visit(node: MarkdownNode, each: (node: MarkdownNode) => void) {
  each(node);
  for (const child of node.children ?? []) visit(child, each);
}
/** A heading's text as the page shows it; raw HTML is left out, as the page leaves it out. */
function plain(node: MarkdownNode): string {
  return node.type === "html"
    ? ""
    : (node.value ?? (node.children ?? []).map(plain).join(""));
}
/**
 * A remark plugin that takes headings from the parsed text, so underlined headings count
 * and lines in any code block never do. Each heading gets the id `<anchors>-<slug>`, a
 * repeated one numbered as GitHub numbers it, and `#slug` links and link definitions
 * become routes to them (`#<anchors>/<slug>`), since the whole hash is the page route.
 */
function anchorHeadings(anchors: string) {
  return () => (tree: MarkdownNode) => {
    const next = slugger();
    const slugs = new Set<string>();
    visit(tree, (node) => {
      if (node.type !== "heading") return;
      const slug = next(plain(node));
      if (!slug) return;
      slugs.add(slug);
      node.data = {
        ...node.data,
        hProperties: { ...node.data?.hProperties, id: `${anchors}-${slug}` },
      };
    });
    // A target may be written percent-encoded, so a heading "Café" is also #caf%C3%A9.
    const own = (target: string) => {
      try {
        return slugs.has(decodeURIComponent(target));
      } catch {
        return false;
      }
    };
    visit(tree, (node) => {
      if (
        (node.type === "link" || node.type === "definition") &&
        node.url?.startsWith("#") &&
        own(node.url.slice(1))
      )
        node.url = `#${anchors}/${node.url.slice(1)}`;
    });
  };
}
/**
 * `untrusted` is for text written elsewhere, such as an agent transcript: its links
 * may lead off this site but never to a page or download of Worklog itself.
 *
 * `anchors` names the page the text fills, such as `guide`: its headings get ids
 * (`guide-<slug>`) and its `#slug` links become routes to them (`#guide/<slug>`).
 * Other `#` links, such as `#work/<id>`, already name a route and stay as written.
 */
export function Markdown({
  text,
  untrusted = false,
  anchors,
}: {
  text: string;
  untrusted?: boolean;
  anchors?: string;
}) {
  const plugins = useMemo(
    () => (anchors ? [remarkGfm, anchorHeadings(anchors)] : [remarkGfm]),
    [anchors],
  );
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={plugins}
        skipHtml
        components={{
          img: ({ alt }) => (
            <span className="image-reference">
              [Image: {alt || "image reference"}]
            </span>
          ),
          a: ({ href, title, children }) => {
            const external = href ? !isSameOrigin(href) : false;
            return (
              <a
                href={href || undefined}
                title={title}
                target={external ? "_blank" : undefined}
                rel={external ? "noopener noreferrer" : undefined}
              >
                {children}
              </a>
            );
          },
        }}
        urlTransform={(url) =>
          resolveLink(url) && !(untrusted && isSameOrigin(url)) ? url : ""
        }
      >
        {text || "Nothing written yet."}
      </ReactMarkdown>
    </div>
  );
}
export function ErrorMessage({ error }: { error: unknown }) {
  return error ? (
    <p className="error" role="alert">
      {error instanceof Error ? error.message : String(error)}
    </p>
  ) : null;
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
/**
 * A separator that sets the width of `panel` by pointer drag or arrow keys.
 * Every change starts from the panel's rendered width, so a width the layout
 * clamped never leaves the handle out of step with the pointer.
 */
export function ResizeHandle({
  label,
  panel,
  min,
  max,
  onResize,
  onReset,
}: {
  label: string;
  panel: RefObject<HTMLElement | null>;
  min: number;
  /** The widest the layout can show the panel right now. */
  max: () => number;
  /** `done` is false while a drag is still moving. */
  onResize: (width: number, done: boolean) => void;
  onReset: () => void;
}) {
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; from: number; max: number; to?: number }>(
    null,
  );
  const [dragging, setDragging] = useState(false);
  const [size, setSize] = useState({ now: 0, max: 0 });
  useEffect(() => {
    const target = panel.current;
    const container = handle.current?.parentElement;
    if (!target || !container || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() =>
      setSize({
        now: Math.round(target.getBoundingClientRect().width),
        max: Math.round(max()),
      }),
    );
    observer.observe(target);
    observer.observe(container);
    return () => observer.disconnect();
  }, [panel]);
  useEffect(
    () => () => document.documentElement.classList.remove("resizing"),
    [],
  );
  const fit = (width: number, limit: number) =>
    Math.round(Math.min(Math.max(width, min), Math.max(min, limit)));
  const current = () => panel.current?.getBoundingClientRect().width ?? min;
  const end = () => {
    const last = drag.current;
    drag.current = null;
    setDragging(false);
    document.documentElement.classList.remove("resizing");
    if (last?.to !== undefined) onResize(last.to, true);
  };
  return (
    <div
      ref={handle}
      className={dragging ? "resize-handle dragging" : "resize-handle"}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={size.now || undefined}
      aria-valuemin={min}
      aria-valuemax={size.max ? Math.max(min, size.max) : undefined}
      tabIndex={0}
      title="Drag to resize. Double-click to reset."
      onKeyDown={(e) => {
        const next =
          e.key === "ArrowLeft"
            ? current() - 16
            : e.key === "ArrowRight"
              ? current() + 16
              : e.key === "Home"
                ? min
                : e.key === "End"
                  ? max()
                  : null;
        if (next === null) return;
        e.preventDefault();
        onResize(fit(next, max()), true);
      }}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture?.(e.pointerId);
        drag.current = { x: e.clientX, from: current(), max: max() };
        setDragging(true);
        document.documentElement.classList.add("resizing");
      }}
      onPointerMove={(e) => {
        const active = drag.current;
        if (!active) return;
        active.to = fit(active.from + e.clientX - active.x, active.max);
        onResize(active.to, false);
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onDoubleClick={onReset}
    />
  );
}
/** A ticket's or a roadmap step's statuses, in the order menus list them. */
export const STATUSES: readonly Status[] = ["open", "doing", "blocked", "done"];
/** A work item's statuses: Waiting is work that waits on someone else. */
export const ITEM_STATUSES: readonly ItemStatus[] = [
  "open",
  "doing",
  "waiting",
  "blocked",
  "done",
];
export const statusLabel = (status: ItemStatus) =>
  status[0].toUpperCase() + status.slice(1);
/** A status menu over `options`: ITEM_STATUSES for work, STATUSES for tickets and steps. */
export function StatusSelect<S extends ItemStatus>({
  value,
  options,
  onChange,
  label = "Status",
  disabled = false,
}: {
  value: S;
  options: readonly S[];
  onChange: (value: S) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <select
      className={"status-select " + value}
      data-status={value}
      disabled={disabled}
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as S)}
    >
      {options.map((s) => (
        <option key={s} value={s}>
          {statusLabel(s)}
        </option>
      ))}
    </select>
  );
}
export function DraftField({
  draftKey,
  label,
  initial,
  revision,
  save,
  multiline = true,
  placeholder,
  maxLength,
  disabled = false,
  labelHidden = false,
  preview: previewable = multiline,
}: {
  draftKey: string;
  label: string;
  initial: string;
  revision: number;
  save: (text: string, revision: number) => Promise<{ revision: number }>;
  multiline?: boolean;
  placeholder?: string;
  maxLength?: number;
  disabled?: boolean;
  /** For a field a tab already names: the label still names it for assistive technology. */
  labelHidden?: boolean;
  /** False for plain text that is never shown as Markdown, such as a person's description. */
  preview?: boolean;
}) {
  const draft = useDraft(draftKey, initial, revision, save);
  const [preview, setPreview] = useState(false);
  return (
    <section className="draft">
      {/* The label, the save state and the field's own buttons share one row above it. */}
      <div className="field-heading">
        <label
          htmlFor={draftKey}
          className={labelHidden ? "visually-hidden" : undefined}
        >
          {label}
        </label>
        <span role="status">{draft.state}</span>
        {multiline && previewable && (
          <button className="quiet" onClick={() => setPreview(!preview)}>
            {preview ? "Edit" : "Preview"}
          </button>
        )}
        <button className="quiet" onClick={draft.flush} disabled={disabled}>
          Save now
        </button>
      </div>
      {preview ? (
        <Markdown text={draft.value} />
      ) : multiline ? (
        <textarea
          id={draftKey}
          maxLength={maxLength}
          disabled={disabled}
          value={draft.value}
          placeholder={placeholder}
          onChange={(e) => draft.set(e.target.value)}
          onBlur={draft.flush}
        />
      ) : (
        <input
          id={draftKey}
          maxLength={maxLength}
          disabled={disabled}
          value={draft.value}
          placeholder={placeholder}
          onChange={(e) => draft.set(e.target.value)}
          onBlur={draft.flush}
        />
      )}
      {draft.storageWarning && (
        <p className="warning">
          Browser draft storage unavailable; keep this tab open until saved.
        </p>
      )}
      {draft.state.startsWith("Conflict") && (
        <div className="warning">
          <strong>The current saved value is:</strong>
          <pre>{initial || "(empty)"}</pre>
          <p>
            Your draft is still in the editor above. Compare the values before
            choosing.
          </p>
          <div className="toolbar">
            <button onClick={draft.discard}>Use saved value</button>
            <button onClick={draft.rebase}>
              Replace saved value with my draft
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
const FILE_MODES = [
  ["copy", "Attach a copy"],
  ["path", "Reference a local file"],
] as const;
/**
 * An attached copy is stored in Worklog. Work items also pass `references`, which
 * offers the other choice: saving only the path of a file that stays where it is
 * on this machine. Both kinds then share one list, oldest first.
 */
export function Files({
  ownerType,
  ownerId,
  files,
  references,
  readOnly = false,
  heading = true,
  attach = true,
}: {
  ownerType: string;
  ownerId: string;
  files: Attachment[];
  references?: Reference[];
  readOnly?: boolean;
  /** False where a tab already names the section. */
  heading?: boolean;
  /** False where the owner takes no new files but can still archive its own. */
  attach?: boolean;
}) {
  const action = useAction();
  const [error, setError] = useState("");
  const [mode, setMode] = useState<(typeof FILE_MODES)[number][0]>("copy");
  const [path, setPath] = useState("");
  const hint = useId();
  async function upload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 25000000) {
      setError("Files must be 25 MB or smaller.");
      return;
    }
    try {
      await action.mutateAsync({
        path: `/attachments?ownerType=${ownerType}&ownerId=${ownerId}&name=${encodeURIComponent(file.name)}`,
        body: file,
      });
      setError("");
    } catch (err) {
      setError(String(err));
    }
    e.target.value = "";
  }
  const entries = [
    ...(files ?? []).map((record) => ({ kind: "copy" as const, record })),
    ...(references ?? []).map((record) => ({ kind: "path" as const, record })),
  ]
    .filter((entry) => !entry.record.archived)
    .sort((a, b) =>
      (a.record.createdAt ?? "").localeCompare(b.record.createdAt ?? ""),
    );
  return (
    <section className="files">
      {heading && <h3>Files</h3>}
      {!readOnly && attach && !references && (
        <label className="file-upload">
          Attach a file
          <input type="file" onChange={upload} />
        </label>
      )}
      {!readOnly && attach && references && (
        <div className="file-add">
          <div
            className="thread-filter"
            role="group"
            aria-label="How to add a file"
          >
            {FILE_MODES.map(([value, label]) => (
              <button
                type="button"
                key={value}
                aria-pressed={mode === value}
                onClick={() => {
                  setMode(value);
                  setError("");
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="muted" id={hint}>
            {mode === "copy"
              ? "Worklog keeps its own copy, up to 25 MB. Later changes to the original don't reach it."
              : "Only the path is saved. The file stays where it is on this machine; copy the path to open it yourself."}
          </p>
          {mode === "copy" ? (
            <input
              type="file"
              aria-label="Attach a file"
              aria-describedby={hint}
              onChange={upload}
            />
          ) : (
            <form
              className="inline"
              onSubmit={async (e) => {
                e.preventDefault();
                await action
                  .mutateAsync({
                    path: "/references",
                    body: { itemId: ownerId, path },
                  })
                  .then(() => setPath(""))
                  .catch(() => undefined);
              }}
            >
              <input
                aria-label="Local path"
                aria-describedby={hint}
                value={path}
                onChange={(e) => setPath(e.target.value)}
                required
                placeholder="C:\…"
              />
              <button>Add path</button>
            </form>
          )}
        </div>
      )}
      <ErrorMessage error={error} />
      {entries.map((entry) =>
        entry.kind === "copy" ? (
          <div className="line" key={"copy-" + entry.record.id}>
            <a href={download(`/attachments/${entry.record.id}?download=1`)}>
              {entry.record.name}
            </a>
            <small>
              {references && "Attached copy · "}
              {formatBytes(entry.record.size)}
            </small>
            {!readOnly && (
              <button
                className="quiet"
                onClick={() =>
                  action.mutate({
                    path: `/attachments/${entry.record.id}/archive`,
                    body: { revision: entry.record.revision },
                  })
                }
              >
                Archive
              </button>
            )}
          </div>
        ) : (
          <div className="line" key={"path-" + entry.record.id}>
            <code>{entry.record.path}</code>
            <small>Local path</small>
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(entry.record.path)}
            >
              Copy path
            </button>
            {!readOnly && (
              <button
                className="quiet"
                onClick={() =>
                  action.mutate({
                    path: `/references/${entry.record.id}/archive`,
                    body: { revision: entry.record.revision },
                  })
                }
              >
                Archive
              </button>
            )}
          </div>
        ),
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
export function Notes({
  ownerType,
  ownerId,
  notes,
}: {
  ownerType: string;
  ownerId: string;
  notes: Note[];
}) {
  const action = useAction();
  return (
    <section>
      <div className="section-heading">
        <h3>Notes</h3>
        <button
          onClick={() =>
            action.mutate({
              path: "/notes",
              body: { ownerType, ownerId, title: "New note", text: "" },
            })
          }
        >
          Add note
        </button>
      </div>
      {notes
        ?.filter((n) => !n.archived)
        .map((n) => (
          <article className="note" key={n.id}>
            <DraftField
              draftKey={"note-title-" + n.id}
              label="Note title"
              initial={n.title}
              revision={n.revision}
              multiline={false}
              save={(title, revision) =>
                commands.updateNote(n.id, { revision, title })
              }
            />
            <DraftField
              draftKey={"note-" + n.id}
              label="Markdown note"
              initial={n.text}
              revision={n.revision}
              save={(text, revision) =>
                commands.updateNote(n.id, { revision, text })
              }
            />
            <button
              className="quiet"
              onClick={() =>
                action.mutate({
                  path: `/notes/${n.id}/archive`,
                  body: { revision: n.revision },
                })
              }
            >
              Archive note
            </button>
          </article>
        ))}
      <ErrorMessage error={action.error} />
    </section>
  );
}
/**
 * A work item's one note: free Markdown, created by its first save. Notes it replaced, and
 * any archived before, stay readable below it.
 */
export function Scratchpad({
  itemId,
  notes,
  labelHidden = false,
}: {
  itemId: string;
  notes: Note[];
  /** Passed to its field, where a tab already names it. */
  labelHidden?: boolean;
}) {
  const pad = notes.find((n) => !n.archived);
  const archived = notes.filter((n) => n.archived);
  // The note the first save created, until the item's next load lists it.
  const created = useRef<string | null>(null);
  const save = async (text: string, revision: number) => {
    const id = pad?.id ?? created.current;
    if (id) return commands.updateNote(id, { revision, text });
    const note = await commands.createNote({
      ownerType: "item",
      ownerId: itemId,
      title: "Scratchpad",
      text,
    });
    created.current = note.id;
    return note;
  };
  return (
    <section className="scratchpad">
      <DraftField
        draftKey={"scratchpad-" + itemId}
        label="Scratchpad (Markdown)"
        labelHidden={labelHidden}
        initial={pad?.text ?? ""}
        revision={pad?.revision ?? 0}
        save={save}
      />
      {archived.length > 0 && (
        <details className="archived-notes">
          <summary>Archived notes ({archived.length})</summary>
          {archived.map((n) => (
            <article className="note" key={n.id}>
              {n.title && <h4>{n.title}</h4>}
              <Markdown text={n.text} />
            </article>
          ))}
        </details>
      )}
    </section>
  );
}
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
  const action = useAction();
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
    action.mutate({
      path: `/tasks/${t.id}/move`,
      body: { revision: t.revision, position },
    });
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
                    {
                      path: "/tasks/" + t.id,
                      method: "PATCH",
                      body: { revision: t.revision, done: next },
                    },
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
            .mutateAsync({
              path: "/tasks",
              body: { itemId, text, done: false },
            })
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
export type TabSpec = {
  id: string;
  label: ReactNode;
  /** The tab's name in words, where its label shows something shorter, such as "1/2". */
  name?: string;
  /** Drawn when the tab first opens, then kept, hidden while another tab shows. */
  panel: () => ReactNode;
};
/**
 * Tabs in the WAI-ARIA pattern: the arrow keys, Home and End move between them and open the
 * one reached. A panel is drawn the first time its tab opens and kept after, so its drafts
 * and half-filled forms survive a switch, and a panel that reads data reads it only once opened.
 */
export function Tabs({
  label,
  tabs,
  className,
}: {
  label: string;
  tabs: TabSpec[];
  className?: string;
}) {
  const base = useId();
  const [selected, setSelected] = useState(tabs[0]?.id);
  const [opened, setOpened] = useState(() => new Set([tabs[0]?.id]));
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const open = (id: string) => {
    setSelected(id);
    setOpened((was) => (was.has(id) ? was : new Set(was).add(id)));
  };
  const step = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = tabs.length - 1;
    const next =
      e.key === "ArrowRight"
        ? index === last
          ? 0
          : index + 1
        : e.key === "ArrowLeft"
          ? index === 0
            ? last
            : index - 1
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? last
              : null;
    if (next === null) return;
    e.preventDefault();
    open(tabs[next].id);
    buttons.current.get(tabs[next].id)?.focus();
  };
  return (
    <div className={"tabs" + (className ? " " + className : "")}>
      <div className="tab-list" role="tablist" aria-label={label}>
        {tabs.map((tab, index) => (
          <button
            key={tab.id}
            ref={(node) => {
              if (node) buttons.current.set(tab.id, node);
              else buttons.current.delete(tab.id);
            }}
            type="button"
            role="tab"
            id={`${base}-tab-${tab.id}`}
            aria-label={tab.name}
            aria-selected={tab.id === selected}
            aria-controls={`${base}-panel-${tab.id}`}
            tabIndex={tab.id === selected ? 0 : -1}
            onClick={() => open(tab.id)}
            onKeyDown={(e) => step(e, index)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          className="tab-panel"
          role="tabpanel"
          id={`${base}-panel-${tab.id}`}
          aria-labelledby={`${base}-tab-${tab.id}`}
          hidden={tab.id !== selected}
          tabIndex={0}
        >
          {opened.has(tab.id) && tab.panel()}
        </div>
      ))}
    </div>
  );
}
export function formatBytes(value: number) {
  return value < 1024
    ? `${value} B`
    : value < 1048576
      ? `${(value / 1024).toFixed(1)} KB`
      : `${(value / 1048576).toFixed(1)} MB`;
}
export function History({ items }: { items: unknown[] }) {
  return (
    <details>
      <summary>Activity history ({items?.length ?? 0})</summary>
      <pre>{JSON.stringify(items, null, 2)}</pre>
    </details>
  );
}
/**
 * Each change's full before and after records repeat the whole record, so the history
 * of a long one that changes often is large: it loads only when opened.
 */
export function ChangeHistory({
  id,
  changes,
}: {
  id: string;
  changes: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Activity history ({changes})</summary>
      {open && <Changes id={id} />}
    </details>
  );
}
function Changes({ id }: { id: string }) {
  const history = useData<{ items: unknown[] }>(
    "/history?entityId=" + encodeURIComponent(id),
  );
  return (
    <>
      <ErrorMessage error={history.error} />
      {history.isPending ? (
        <p>Loading history…</p>
      ) : (
        <pre>{JSON.stringify(history.data?.items ?? [], null, 2)}</pre>
      )}
    </>
  );
}
export function RecoverRecords({
  groups,
}: {
  groups: {
    label: string;
    path: string;
    records: (
      | Attachment
      | Note
      | WebLink
      | AgentChat
      | GithubRef
      | {
          id: string;
          revision: number;
          archived?: boolean;
          text?: string;
          path?: string;
          url?: string;
          title?: string;
          reference?: string;
        }
    )[];
  }[];
}) {
  const action = useAction();
  const groupsWithRecords = groups
    .map((g) => ({ ...g, records: g.records?.filter((r) => r.archived) ?? [] }))
    .filter((g) => g.records.length);
  if (!groupsWithRecords.length) return null;
  return (
    <details>
      <summary>
        Archived supporting records (
        {groupsWithRecords.reduce((n, g) => n + g.records.length, 0)})
      </summary>
      {groupsWithRecords.map((g) => (
        <section key={g.path}>
          <h3>{g.label}</h3>
          {g.records.map((r) => (
            <div className="line" key={r.id}>
              <span>
                {"name" in r
                  ? r.name
                  : "title" in r && r.title
                    ? r.title
                    : "reference" in r && r.reference
                      ? r.reference
                      : "url" in r && r.url
                        ? r.url
                        : "text" in r && r.text
                          ? r.text
                          : "path" in r && r.path
                            ? r.path
                            : r.id}
              </span>
              <button
                disabled={action.isPending}
                onClick={() =>
                  action.mutate({
                    path: `/${g.path}/${r.id}/recover`,
                    body: { revision: r.revision },
                  })
                }
              >
                Recover
              </button>
            </div>
          ))}
        </section>
      ))}
      <ErrorMessage error={action.error} />
    </details>
  );
}
/** A person's title and team on one line, such as "Staff Engineer · Platform"; empty when they have neither. */
export function personLine(title: string, team: string) {
  return [title, team].filter(Boolean).join(" · ");
}
/**
 * A tie's role, saved when its field loses focus, and the tie's Unlink. Pressing Unlink
 * right after editing the role saves the role first: the press is not lost to a button
 * disabled mid-click, and the save cannot land after the unlink and tie the person again.
 * A person's tie goes to /person-links; an agent's, with `path`, to /agent-links.
 */
export function TieRole({
  tie,
  path = "/person-links",
  name,
  unlinkName,
  relationship,
  disabled = false,
  unlinkDisabled = false,
  placeholder,
}: {
  tie:
    | {
        personId: string;
        targetType: "item" | "roadmap" | "repository";
        targetId: string;
      }
    | { agentId: string; targetType: TieType; targetId: string };
  path?: "/person-links" | "/agent-links";
  /** The role field's accessible name, such as "Role on Define SLOs". */
  name: string;
  unlinkName: string;
  relationship: string;
  disabled?: boolean;
  unlinkDisabled?: boolean;
  placeholder: string;
}) {
  const action = useAction();
  const saving = useRef<Promise<unknown>>(Promise.resolve());
  const [unlinking, setUnlinking] = useState(false);
  return (
    <>
      <input
        aria-label={name}
        defaultValue={relationship}
        key={relationship}
        disabled={disabled}
        placeholder={placeholder}
        onBlur={(e) => {
          const next = e.target.value.trim();
          if (next !== relationship)
            saving.current = action
              .mutateAsync({ path, body: { ...tie, relationship: next } })
              .catch(() => undefined);
        }}
      />
      <button
        type="button"
        className="quiet"
        aria-label={unlinkName}
        disabled={unlinkDisabled || unlinking}
        onClick={() => {
          setUnlinking(true);
          void saving.current
            .then(() =>
              action.mutateAsync({ path: path + "/unlink", body: tie }),
            )
            .catch(() => undefined)
            .finally(() => setUnlinking(false));
        }}
      >
        Unlink
      </button>
      <ErrorMessage error={action.error} />
    </>
  );
}
export function PersonBadges({ people }: { people?: PersonAssignment[] }) {
  if (!people?.length) return null;
  return (
    <span className="person-badges">
      {people.map((p) => (
        <a
          key={p.id}
          className="person-badge"
          href={"#people/" + p.personId}
          title={personLine(p.personTitle, p.personTeam) || undefined}
        >
          {p.name}
          {p.relationship ? ` (${p.relationship})` : ""}
        </a>
      ))}
    </span>
  );
}
export function PeopleLinks({
  targetType,
  targetId,
  people = [],
  disabled = false,
  canTie = !disabled,
  headingLevel = 3,
}: {
  targetType: "item" | "roadmap" | "repository";
  targetId: string;
  people?: PersonAssignment[];
  disabled?: boolean;
  /** False keeps the ties' roles editable and their Unlink, but takes no new tie, as on an archived repository. */
  canTie?: boolean;
  /** 2 where the page's other sections are h2, as on a repository's page. */
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const directory = useData<List<Person>>("/people?archived=false");
  const action = useAction();
  const [personId, setPersonId] = useState("");
  const [newName, setNewName] = useState("");
  const [relationship, setRelationship] = useState("");
  const [error, setError] = useState("");
  return (
    <section>
      <Heading>
        People <span className="count">{people.length}</span>
      </Heading>
      {people.map((p) => (
        <div className="line person-assignment" key={p.id}>
          <a href={"#people/" + p.personId}>{p.name}</a>
          {personLine(p.personTitle, p.personTeam) && (
            <small>{personLine(p.personTitle, p.personTeam)}</small>
          )}
          <TieRole
            tie={{ personId: p.personId, targetType, targetId }}
            name={"Role for " + p.name}
            unlinkName={"Unlink " + p.name}
            relationship={p.relationship}
            disabled={disabled}
            unlinkDisabled={disabled}
            placeholder="Role (e.g. Owner, Stakeholder)…"
          />
        </div>
      ))}
      {canTie && (
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            if (disabled || !personId) return;
            setError("");
            try {
              let resolvedPersonId = personId;
              if (personId === "__new__") {
                if (!newName.trim()) return;
                const created = await commands.createPerson({
                  name: newName.trim(),
                });
                resolvedPersonId = created.id;
              }
              await action.mutateAsync({
                path: "/person-links",
                body: {
                  personId: resolvedPersonId,
                  targetType,
                  targetId,
                  relationship: relationship.trim(),
                },
              });
              setPersonId("");
              setNewName("");
              setRelationship("");
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
            }
          }}
        >
          <select
            aria-label="Choose person"
            value={personId}
            disabled={disabled}
            onChange={(e) => setPersonId(e.target.value)}
          >
            <option value="">Select person to tie…</option>
            {/* Someone already tied changes their role in their own row instead. */}
            {directory.data?.items
              ?.filter(
                (person) => !people.some((p) => p.personId === person.id),
              )
              .map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name}
                  {personLine(person.title, person.team)
                    ? " (" + personLine(person.title, person.team) + ")"
                    : ""}
                </option>
              ))}
            <option value="__new__">+ New person…</option>
          </select>
          {personId === "__new__" && (
            <input
              aria-label="New person name"
              placeholder="New person name…"
              value={newName}
              disabled={disabled}
              onChange={(e) => setNewName(e.target.value)}
              required
            />
          )}
          <input
            aria-label="Relationship or role"
            placeholder="Role (e.g. Owner, Stakeholder, Reviewer)…"
            value={relationship}
            disabled={disabled}
            onChange={(e) => setRelationship(e.target.value)}
          />
          <button
            type="submit"
            disabled={
              disabled ||
              action.isPending ||
              !personId ||
              (personId === "__new__" && !newName.trim())
            }
          >
            Tie person
          </button>
        </form>
      )}
      <ErrorMessage error={error || action.error} />
    </section>
  );
}
export function LinksSection({
  ownerType,
  ownerId,
  links = [],
  disabled = false,
  heading = true,
}: {
  ownerType: "item" | "roadmap";
  ownerId: string;
  links?: WebLink[];
  disabled?: boolean;
  /** False where a tab already names the section. */
  heading?: boolean;
}) {
  const action = useAction();
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const active = links.filter((l) => !l.archived);
  return (
    <section className="links">
      {heading && (
        <h3>
          Links <span className="count">{active.length}</span>
        </h3>
      )}
      {active.map((l) => (
        <div className="line" key={l.id}>
          <div>
            <a href={l.url} target="_blank" rel="noopener noreferrer">
              {l.title || l.url} ↗
            </a>
            {l.title && (
              <>
                {" "}
                <small>{l.url}</small>
              </>
            )}
            {l.description && (
              <div>
                <small>{l.description}</small>
              </div>
            )}
          </div>
          {!disabled && (
            <button
              type="button"
              className="quiet"
              aria-label={"Archive link " + (l.title || l.url)}
              onClick={() =>
                action.mutate({
                  path: `/links/${l.id}/archive`,
                  body: { revision: l.revision },
                })
              }
            >
              Archive
            </button>
          )}
        </div>
      ))}
      {!disabled && (
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!url.trim()) return;
            await action
              .mutateAsync({
                path: "/links",
                body: {
                  ownerType,
                  ownerId,
                  url: url.trim(),
                  title: title.trim(),
                  description: description.trim(),
                },
              })
              .then(() => {
                setTitle("");
                setUrl("");
                setDescription("");
              })
              .catch(() => undefined);
          }}
        >
          <input
            aria-label="Link title"
            placeholder="Link title (optional)…"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <input
            aria-label="Link URL"
            placeholder="https://…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
          <input
            aria-label="Link description"
            placeholder="Description…"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <button type="submit" disabled={action.isPending || !url.trim()}>
            Add link
          </button>
        </form>
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
// Common providers seed the suggestions; providers and models already used join them.
const PROVIDERS = ["Anthropic", "OpenAI", "Google"];
type ChatFields = {
  repositoryIds?: string[];
  title: string;
  chatId: string;
  provider: string;
  model: string;
  repos: string[];
  createdDate?: string;
};
/** Repositories are typed comma separated. */
function splitRepos(value: string) {
  return [
    ...new Set(
      value
        .split(",")
        .map((r) => r.trim())
        .filter(Boolean),
    ),
  ];
}
function agentName(chat: AgentChat) {
  return [chat.provider, chat.model].filter(Boolean).join(" · ");
}
function ChatForm({
  label,
  submit,
  chats,
  initial,
  disabled = false,
  onSave,
  onCancel,
}: {
  label: string;
  submit: string;
  chats: AgentChat[];
  initial?: AgentChat;
  disabled?: boolean;
  onSave: (fields: ChatFields) => Promise<unknown>;
  onCancel?: () => void;
}) {
  const id = useId();
  const [repositoryIds, setRepositoryIds] = useState<string[]>(
    initial?.repositoryIds ?? [],
  );
  const blank = {
    title: "",
    chatId: "",
    provider: "",
    model: "",
    repos: "",
    createdDate: "",
  };
  const [values, setValues] = useState(() =>
    initial
      ? {
          title: initial.title,
          chatId: initial.chatId,
          provider: initial.provider,
          model: initial.model,
          repos: initial.repos.join(", "),
          createdDate: initial.createdDate,
        }
      : blank,
  );
  const field = (name: keyof typeof blank) => ({
    value: values[name],
    disabled,
    onChange: (e: { target: { value: string } }) =>
      setValues({ ...values, [name]: e.target.value }),
  });
  const providers = [
    ...new Set([...PROVIDERS, ...chats.map((c) => c.provider)]),
  ].filter(Boolean);
  const models = [...new Set(chats.map((c) => c.model))].filter(Boolean);
  const ready = !!values.title.trim() && !!values.chatId.trim();
  return (
    <form
      className="inline chat-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!ready || disabled) return;
        await onSave({
          title: values.title.trim(),
          chatId: values.chatId.trim(),
          provider: values.provider.trim(),
          model: values.model.trim(),
          repos: splitRepos(values.repos),
          ...(repositoryIds.length || initial?.repositoryIds?.length
            ? { repositoryIds }
            : {}),
          ...(values.createdDate ? { createdDate: values.createdDate } : {}),
        })
          .then(() => {
            if (!initial) {
              setValues(blank);
              setRepositoryIds([]);
            }
          })
          .catch(() => undefined);
      }}
    >
      <input
        aria-label={label + " title"}
        placeholder="Chat title…"
        required
        {...field("title")}
      />
      <input
        aria-label={label + " ID"}
        placeholder="Chat ID…"
        required
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        {...field("chatId")}
      />
      <input
        aria-label={label + " provider"}
        placeholder="AI provider…"
        list={id + "-providers"}
        {...field("provider")}
      />
      <input
        aria-label={label + " model"}
        placeholder="Model…"
        list={id + "-models"}
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        {...field("model")}
      />
      <input
        aria-label={label + " repositories"}
        placeholder="Repos, comma separated…"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        {...field("repos")}
      />
      <RepositoryPicker
        label={label + " registered repositories"}
        folded
        value={repositoryIds}
        onChange={setRepositoryIds}
        disabled={disabled}
      />
      <input
        aria-label={label + " date created"}
        type="date"
        {...field("createdDate")}
      />
      <datalist id={id + "-providers"}>
        {providers.map((p) => (
          <option key={p} value={p} />
        ))}
      </datalist>
      <datalist id={id + "-models"}>
        {models.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <button type="submit" disabled={disabled || !ready}>
        {submit}
      </button>
      {onCancel && (
        <button type="button" className="quiet" onClick={onCancel}>
          Cancel
        </button>
      )}
    </form>
  );
}
/** Who a chat is: its title, provider and model, chat ID, repositories, and the steps it works on, with a link to its messages. */
function ChatSummary({
  chat,
  steps = [],
  roadmapId,
}: {
  chat: AgentChat;
  steps?: Step[];
  roadmapId?: string;
}) {
  const agent = agentName(chat);
  const working = steps.filter((s) => chat.stepIds.includes(s.id));
  return (
    <div className="chat-summary">
      <div className="chat-row">
        <strong>{chat.title}</strong>
        {agent && <span className="person-badge">{agent}</span>}
        <a href={"#chats/" + chat.id} aria-label={"Messages of " + chat.title}>
          Messages
        </a>
      </div>
      <div className="chat-row">
        <code>{chat.chatId}</code>
        <RepositoryBadges ids={chat.repositoryIds} />
        {chat.repos.map((r) => (
          <code className="repo" key={r}>
            <span>{r}</span> · Unregistered
          </code>
        ))}
        <small>Created {chat.createdDate}</small>
      </div>
      {working.length > 0 && (
        <div className="chat-row">
          <small>Working on</small>
          {working.map((s) => (
            <a key={s.id} href={`#roadmaps/${roadmapId}/${s.id}`}>
              {s.title}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
function CopyChatId({ chat }: { chat: AgentChat }) {
  return (
    <button
      type="button"
      aria-label={"Copy ID of " + chat.title}
      onClick={() => navigator.clipboard.writeText(chat.chatId)}
    >
      Copy ID
    </button>
  );
}
export function AgentChatsSection({
  ownerType,
  ownerId,
  chats = [],
  steps,
  disabled = false,
}: {
  ownerType: "item" | "roadmap";
  ownerId: string;
  chats?: AgentChat[];
  // A roadmap's active steps, to show what each chat is working on.
  steps?: Step[];
  disabled?: boolean;
}) {
  const action = useAction();
  const [editing, setEditing] = useState<string | null>(null);
  const active = chats.filter((c) => !c.archived);
  return (
    <section>
      <h3>
        Agent chats <span className="count">{active.length}</span>
      </h3>
      {active.map((c) =>
        editing === c.id && !disabled ? (
          <div className="line" key={c.id}>
            <ChatForm
              label="Edited chat"
              submit="Save chat"
              chats={active}
              initial={c}
              onSave={(fields) =>
                action
                  .mutateAsync({
                    path: "/chats/" + c.id,
                    method: "PATCH",
                    body: { revision: c.revision, ...fields },
                  })
                  .then(() => setEditing(null))
              }
              onCancel={() => setEditing(null)}
            />
          </div>
        ) : (
          <div className="line chat-line" key={c.id}>
            <ChatSummary chat={c} steps={steps} roadmapId={ownerId} />
            <CopyChatId chat={c} />
            {!disabled && (
              <>
                <button
                  type="button"
                  className="quiet"
                  aria-label={"Edit chat " + c.title}
                  onClick={() => setEditing(c.id)}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className="quiet"
                  aria-label={"Archive chat " + c.title}
                  onClick={() =>
                    action.mutate({
                      path: `/chats/${c.id}/archive`,
                      body: { revision: c.revision },
                    })
                  }
                >
                  Archive
                </button>
              </>
            )}
          </div>
        ),
      )}
      {!disabled && (
        <ChatForm
          label="Agent chat"
          submit="Add chat"
          chats={active}
          disabled={action.isPending}
          onSave={(fields) =>
            action.mutateAsync({
              path: "/chats",
              body: { ownerType, ownerId, ...fields },
            })
          }
        />
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
/** The chats working on one roadmap step: assign the roadmap's chats or track a new one here. */
export function StepAgentChats({
  roadmapId,
  step,
  steps,
  chats = [],
  disabled = false,
}: {
  roadmapId: string;
  step: Step;
  // The roadmap's active steps, to find chats working on a parent of this one.
  steps: Step[];
  chats?: AgentChat[];
  disabled?: boolean;
}) {
  const action = useAction();
  const [choice, setChoice] = useState("");
  const active = chats.filter((c) => !c.archived);
  const assigned = active.filter((c) => c.stepIds.includes(step.id));
  const available = active.filter((c) => !c.stepIds.includes(step.id));
  const ancestors: Step[] = [];
  let parent = steps.find((s) => s.id === step.parentId);
  while (parent && !ancestors.includes(parent)) {
    ancestors.push(parent);
    parent = steps.find((s) => s.id === parent?.parentId);
  }
  const inherited = available.flatMap((c) => {
    const through = ancestors.find((a) => c.stepIds.includes(a.id));
    return through ? [{ chat: c, through }] : [];
  });
  const assign = (chat: AgentChat, stepIds: string[]) =>
    action.mutateAsync({
      path: "/chats/" + chat.id,
      method: "PATCH",
      body: { revision: chat.revision, stepIds },
    });
  return (
    <section>
      <h3>
        Agent chats <span className="count">{assigned.length}</span>
      </h3>
      {assigned.map((c) => (
        <div className="line chat-line" key={c.id}>
          <ChatSummary chat={c} />
          <CopyChatId chat={c} />
          {!disabled && (
            <button
              type="button"
              className="quiet"
              aria-label={"Unassign " + c.title}
              disabled={action.isPending}
              onClick={() =>
                assign(
                  c,
                  c.stepIds.filter((s) => s !== step.id),
                ).catch(() => undefined)
              }
            >
              Unassign
            </button>
          )}
        </div>
      ))}
      {!assigned.length && (
        <p className="muted">No agent chat is working on this step.</p>
      )}
      {inherited.map(({ chat, through }) => (
        <p className="muted" key={chat.id}>
          {chat.title}
          {agentName(chat) && ` (${agentName(chat)})`} is working on the parent
          step{" "}
          <a href={`#roadmaps/${roadmapId}/${through.id}`}>{through.title}</a>.
        </p>
      ))}
      {!disabled && available.length > 0 && (
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            const chat = available.find((c) => c.id === choice);
            if (!chat) return;
            await assign(chat, [...chat.stepIds, step.id])
              .then(() => setChoice(""))
              .catch(() => undefined);
          }}
        >
          <select
            aria-label="Agent chat to assign"
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
          >
            <option value="">Assign one of this roadmap’s chats…</option>
            {available.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
                {agentName(c) && ` (${agentName(c)})`}
              </option>
            ))}
          </select>
          <button type="submit" disabled={!choice || action.isPending}>
            Assign chat
          </button>
        </form>
      )}
      {!disabled && (
        <ChatForm
          label="Agent chat"
          submit="Add chat to step"
          chats={active}
          disabled={action.isPending}
          onSave={(fields) =>
            action.mutateAsync({
              path: "/chats",
              body: {
                ownerType: "roadmap",
                ownerId: roadmapId,
                ...fields,
                stepIds: [step.id],
              },
            })
          }
        />
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
export function GithubRefsSection({
  ownerType,
  ownerId,
  github = [],
  chats = [],
  disabled = false,
}: {
  ownerType: "item" | "roadmap";
  ownerId: string;
  github?: GithubRef[];
  chats?: AgentChat[];
  disabled?: boolean;
}) {
  const action = useAction();
  const [kind, setKind] = useState<"pr" | "commit">("pr");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [reference, setReference] = useState("");
  const [description, setDescription] = useState("");
  const [chatId, setChatId] = useState("");
  const active = github.filter((g) => !g.archived);
  const activeChats = chats.filter((c) => !c.archived);
  return (
    <section>
      <h3>
        GitHub commits & PRs <span className="count">{active.length}</span>
      </h3>
      {active.map((g) => (
        <div className="line" key={g.id}>
          <span className="person-badge">
            {g.kind === "pr" ? "PR" : "Commit"}
          </span>
          <a href={g.url} target="_blank" rel="noopener noreferrer">
            {g.title || g.reference || g.url} ↗
          </a>
          {g.reference && <code>{g.reference}</code>}
          {g.chatTitle && <small>Chat: {g.chatTitle}</small>}
          {g.description && <small>{g.description}</small>}
          {!disabled && (
            <button
              type="button"
              className="quiet"
              aria-label={"Archive GitHub " + (g.title || g.reference || g.url)}
              onClick={() =>
                action.mutate({
                  path: `/github/${g.id}/archive`,
                  body: { revision: g.revision },
                })
              }
            >
              Archive
            </button>
          )}
        </div>
      ))}
      {!disabled && (
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!url.trim()) return;
            await action
              .mutateAsync({
                path: "/github",
                body: {
                  ownerType,
                  ownerId,
                  kind,
                  url: url.trim(),
                  title: title.trim(),
                  reference: reference.trim(),
                  description: description.trim(),
                  chatId: chatId || null,
                },
              })
              .then(() => {
                setUrl("");
                setTitle("");
                setReference("");
                setDescription("");
                setChatId("");
              })
              .catch(() => undefined);
          }}
        >
          <select
            aria-label="GitHub reference type"
            value={kind}
            onChange={(e) => setKind(e.target.value as "pr" | "commit")}
          >
            <option value="pr">Pull request</option>
            <option value="commit">Commit</option>
          </select>
          <input
            aria-label="GitHub URL"
            placeholder="https://github.com/org/repo/pull/123 or /commit/sha…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
          <input
            aria-label="GitHub title"
            placeholder="Title or summary…"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <input
            aria-label="GitHub SHA or PR number"
            placeholder="SHA or PR # (auto if blank)…"
            value={reference}
            onChange={(e) => setReference(e.target.value)}
          />
          <input
            aria-label="GitHub description"
            placeholder="Description (optional)…"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <select
            aria-label="Associated agent chat"
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
          >
            <option value="">No linked agent chat</option>
            {activeChats.map((c) => (
              <option key={c.id} value={c.id}>
                Chat: {c.title} ({c.chatId})
              </option>
            ))}
          </select>
          <button type="submit" disabled={action.isPending || !url.trim()}>
            Add GitHub ref
          </button>
        </form>
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
