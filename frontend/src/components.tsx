import {
  useEffect,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type ReactNode,
  type RefObject,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { RepositoryBadges, RepositoryPicker } from "./RepositoryControls";
import { download } from "./api";
import { commands } from "./commands";
import { useAction, useData, useDraft } from "./hooks";
import type {
  AgentChat,
  Attachment,
  GithubRef,
  List,
  Note,
  Person,
  PersonAssignment,
  Reference,
  Status,
  Step,
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
/**
 * `untrusted` is for text written elsewhere, such as an agent transcript: its links
 * may lead off this site but never to a page or download of Worklog itself.
 */
export function Markdown({
  text,
  untrusted = false,
}: {
  text: string;
  untrusted?: boolean;
}) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
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
export const STATUSES: Status[] = ["open", "doing", "blocked", "done"];
export const statusLabel = (status: Status) =>
  status[0].toUpperCase() + status.slice(1);
export function StatusSelect({
  value,
  onChange,
  label = "Status",
  disabled = false,
}: {
  value: Status;
  onChange: (value: Status) => void;
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
      onChange={(e) => onChange(e.target.value as Status)}
    >
      {STATUSES.map((s) => (
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
  compact = false,
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
  /** Puts Save now in the heading row, so a field in a list takes one row less. */
  compact?: boolean;
}) {
  const draft = useDraft(draftKey, initial, revision, save);
  const [preview, setPreview] = useState(false);
  const saveNow = (
    <button className="quiet" onClick={draft.flush} disabled={disabled}>
      Save now
    </button>
  );
  return (
    <section className="draft">
      <div className="field-heading">
        <label htmlFor={draftKey}>{label}</label>
        <span role="status">{draft.state}</span>
        {multiline && (
          <button className="quiet" onClick={() => setPreview(!preview)}>
            {preview ? "Edit" : "Preview"}
          </button>
        )}
        {compact && saveNow}
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
      {!compact && saveNow}
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
}: {
  ownerType: string;
  ownerId: string;
  files: Attachment[];
  references?: Reference[];
  readOnly?: boolean;
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
    <section>
      <h3>Files</h3>
      {!readOnly && !references && (
        <label className="file-upload">
          Attach a file
          <input type="file" onChange={upload} />
        </label>
      )}
      {!readOnly && references && (
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
  compact = false,
}: {
  itemId: string;
  notes: Note[];
  /** Passed to its field: Save now in the heading row. */
  compact?: boolean;
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
        initial={pad?.text ?? ""}
        revision={pad?.revision ?? 0}
        save={save}
        compact={compact}
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
export function PersonBadges({ people }: { people?: PersonAssignment[] }) {
  if (!people?.length) return null;
  return (
    <span className="person-badges">
      {people.map((p) => (
        <a
          key={p.id}
          className="person-badge"
          href={"#people/" + p.personId}
          title={p.personRole || undefined}
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
}: {
  targetType: "item" | "roadmap";
  targetId: string;
  people?: PersonAssignment[];
  disabled?: boolean;
}) {
  const directory = useData<List<Person>>("/people?archived=false");
  const action = useAction();
  const [personId, setPersonId] = useState("");
  const [newName, setNewName] = useState("");
  const [relationship, setRelationship] = useState("");
  const [error, setError] = useState("");
  return (
    <section>
      <h3>
        People <span className="count">{people.length}</span>
      </h3>
      {people.map((p) => (
        <div className="line person-assignment" key={p.id}>
          <a href={"#people/" + p.personId}>{p.name}</a>
          {p.personRole && <small>{p.personRole}</small>}
          <input
            aria-label={"Role for " + p.name}
            defaultValue={p.relationship}
            key={p.id + ":" + p.relationship}
            disabled={disabled}
            placeholder="Role (e.g. Owner, Stakeholder)…"
            onBlur={(e) => {
              const next = e.target.value.trim();
              if (next !== p.relationship) {
                action.mutate({
                  path: "/person-links",
                  body: {
                    personId: p.personId,
                    targetType,
                    targetId,
                    relationship: next,
                  },
                });
              }
            }}
          />
          <button
            type="button"
            className="quiet"
            disabled={disabled}
            aria-label={"Unlink " + p.name}
            onClick={() =>
              action.mutate({
                path: "/person-links/unlink",
                body: { personId: p.personId, targetType, targetId },
              })
            }
          >
            Unlink
          </button>
        </div>
      ))}
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
                role: "",
                notes: "",
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
          {directory.data?.items?.map((person) => (
            <option key={person.id} value={person.id}>
              {person.name}
              {person.role ? " (" + person.role + ")" : ""}
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
      <ErrorMessage error={error || action.error} />
    </section>
  );
}
export function LinksSection({
  ownerType,
  ownerId,
  links = [],
  disabled = false,
}: {
  ownerType: "item" | "roadmap";
  ownerId: string;
  links?: WebLink[];
  disabled?: boolean;
}) {
  const action = useAction();
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const active = links.filter((l) => !l.archived);
  return (
    <section>
      <h3>
        Links <span className="count">{active.length}</span>
      </h3>
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
