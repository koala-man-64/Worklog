import { useId, useState, type ChangeEvent } from "react";
import { download } from "../api";
import { commands } from "../commands";
import { useWrites } from "../hooks";
import type { Attachment, Reference } from "../apiTypes";
import { ErrorMessage } from "../ui/Messages";
import { formatBytes } from "../ui/format";
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
  const action = useWrites();
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
      await action.mutateAsync(() =>
        commands.uploadAttachment(ownerType, ownerId, file),
      );
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
                  .mutateAsync(() =>
                    commands.createReference({ itemId: ownerId, path }),
                  )
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
                  action.mutate(() =>
                    commands.setArchived("attachments", entry.record.id, true, {
                      revision: entry.record.revision,
                    }),
                  )
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
                  action.mutate(() =>
                    commands.setArchived("references", entry.record.id, true, {
                      revision: entry.record.revision,
                    }),
                  )
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
