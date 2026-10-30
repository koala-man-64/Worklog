import { useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
  Status,
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
export function Markdown({ text }: { text: string }) {
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
        urlTransform={(url) => (resolveLink(url) ? url : "")}
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
      {["open", "doing", "blocked", "done"].map((s) => (
        <option key={s} value={s}>
          {s[0].toUpperCase() + s.slice(1)}
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
}: {
  draftKey: string;
  label: string;
  initial: string;
  revision: number;
  save: (text: string, revision: number) => Promise<{ revision: number }>;
  multiline?: boolean;
  placeholder?: string;
}) {
  const draft = useDraft(draftKey, initial, revision, save);
  const [preview, setPreview] = useState(false);
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
      </div>
      {preview ? (
        <Markdown text={draft.value} />
      ) : multiline ? (
        <textarea
          id={draftKey}
          value={draft.value}
          placeholder={placeholder}
          onChange={(e) => draft.set(e.target.value)}
          onBlur={draft.flush}
        />
      ) : (
        <input
          id={draftKey}
          value={draft.value}
          placeholder={placeholder}
          onChange={(e) => draft.set(e.target.value)}
          onBlur={draft.flush}
        />
      )}
      <button className="quiet" onClick={draft.flush}>
        Save now
      </button>
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
export function Files({
  ownerType,
  ownerId,
  files,
  readOnly = false,
}: {
  ownerType: string;
  ownerId: string;
  files: Attachment[];
  readOnly?: boolean;
}) {
  const action = useAction();
  const [error, setError] = useState("");
  return (
    <section>
      <h3>Files</h3>
      {!readOnly && (
        <label className="file-upload">
          Attach a file
          <input
            type="file"
            onChange={async (e) => {
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
            }}
          />
        </label>
      )}
      <ErrorMessage error={error} />
      {files
        ?.filter((f) => !f.archived)
        .map((f) => (
          <div className="line" key={f.id}>
            <a href={download(`/attachments/${f.id}?download=1`)}>{f.name}</a>
            <small>{formatBytes(f.size)}</small>
            {!readOnly && (
              <button
                className="quiet"
                onClick={() =>
                  action.mutate({
                    path: `/attachments/${f.id}/archive`,
                    body: { revision: f.revision },
                  })
                }
              >
                Archive
              </button>
            )}
          </div>
        ))}
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
export function AgentChatsSection({
  ownerType,
  ownerId,
  chats = [],
  disabled = false,
}: {
  ownerType: "item" | "roadmap";
  ownerId: string;
  chats?: AgentChat[];
  disabled?: boolean;
}) {
  const action = useAction();
  const [title, setTitle] = useState("");
  const [chatId, setChatId] = useState("");
  const [createdDate, setCreatedDate] = useState("");
  const active = chats.filter((c) => !c.archived);
  return (
    <section>
      <h3>
        Agent chats <span className="count">{active.length}</span>
      </h3>
      {active.map((c) => (
        <div className="line" key={c.id}>
          <strong>{c.title}</strong>
          <code>{c.chatId}</code>
          <small>Created {c.createdDate}</small>
          <button
            type="button"
            onClick={() => navigator.clipboard.writeText(c.chatId)}
          >
            Copy ID
          </button>
          {!disabled && (
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
          )}
        </div>
      ))}
      {!disabled && (
        <form
          className="inline"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!title.trim() || !chatId.trim()) return;
            await action
              .mutateAsync({
                path: "/chats",
                body: {
                  ownerType,
                  ownerId,
                  title: title.trim(),
                  chatId: chatId.trim(),
                  ...(createdDate ? { createdDate } : {}),
                },
              })
              .then(() => {
                setTitle("");
                setChatId("");
                setCreatedDate("");
              })
              .catch(() => undefined);
          }}
        >
          <input
            aria-label="Agent chat title"
            placeholder="Chat title…"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
          <input
            aria-label="Agent chat ID"
            placeholder="Chat ID…"
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            required
          />
          <input
            aria-label="Agent chat date created"
            type="date"
            value={createdDate}
            onChange={(e) => setCreatedDate(e.target.value)}
          />
          <button
            type="submit"
            disabled={action.isPending || !title.trim() || !chatId.trim()}
          >
            Add chat
          </button>
        </form>
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
              aria-label={
                "Archive GitHub " + (g.title || g.reference || g.url)
              }
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
