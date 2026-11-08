import { useRef } from "react";
import { commands } from "../commands";
import { useWrites } from "../hooks";
import type { Note } from "../apiTypes";
import { DraftField } from "../ui/DraftField";
import { Markdown } from "../ui/Markdown";
import { ErrorMessage } from "../ui/Messages";
export function Notes({
  ownerType,
  ownerId,
  notes,
}: {
  ownerType: Note["ownerType"];
  ownerId: string;
  notes: Note[];
}) {
  const action = useWrites();
  return (
    <section>
      <div className="section-heading">
        <h3>Notes</h3>
        <button
          onClick={() =>
            action.mutate(() =>
              commands.createNote({
                ownerType,
                ownerId,
                title: "New note",
                text: "",
              }),
            )
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
                action.mutate(() =>
                  commands.setArchived("notes", n.id, true, {
                    revision: n.revision,
                  }),
                )
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
