import { useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { download } from "./api";
import { commands } from "./commands";
import { useAction, useDraft } from "./hooks";
import type { Attachment, Note, Status } from "./types";
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
      disabled={disabled}
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as Status)}
    >
      {["open", "doing", "blocked", "done"].map((s) => (
        <option key={s}>{s}</option>
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
      <summary>Activity history · {items?.length ?? 0}</summary>
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
      | {
          id: string;
          revision: number;
          archived?: boolean;
          text?: string;
          path?: string;
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
        Archived supporting records ·{" "}
        {groupsWithRecords.reduce((n, g) => n + g.records.length, 0)}
      </summary>
      {groupsWithRecords.map((g) => (
        <section key={g.path}>
          <h3>{g.label}</h3>
          {g.records.map((r) => (
            <div className="line" key={r.id}>
              <span>
                {"name" in r
                  ? r.name
                  : "title" in r
                    ? r.title || r.text
                    : "text" in r
                      ? r.text
                      : "path" in r
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
