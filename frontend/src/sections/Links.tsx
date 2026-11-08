import { useState } from "react";
import { commands } from "../commands";
import { useWrites } from "../hooks";
import type { WebLink } from "../apiTypes";
import { ErrorMessage } from "../ui/Messages";
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
  const action = useWrites();
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
                action.mutate(() =>
                  commands.setArchived("links", l.id, true, {
                    revision: l.revision,
                  }),
                )
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
              .mutateAsync(() =>
                commands.createLink({
                  ownerType,
                  ownerId,
                  url: url.trim(),
                  title: title.trim(),
                  description: description.trim(),
                }),
              )
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
