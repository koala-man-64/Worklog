import { useState } from "react";
import { useDraft } from "../hooks";
import { Markdown } from "./Markdown";
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
