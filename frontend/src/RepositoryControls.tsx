import { useId, useState } from "react";
import { useData } from "./hooks";
import { LinkPicker } from "./LinkPicker";
import type { Answer, Repository } from "./apiTypes";

export function RepositoryBadges({
  ids = [],
  legacy = [],
}: {
  ids?: string[];
  legacy?: string[];
}) {
  if (!ids.length && !legacy.length) return null;
  return (
    <span className="repository-badges">
      {ids.map((id) => (
        <a className="person-badge" key={id} href={"#repositories/" + id}>
          <RepositoryName id={id} />
        </a>
      ))}
      {legacy.map((name) => (
        <span className="person-badge" key={name}>
          {name} · Unregistered
        </span>
      ))}
    </span>
  );
}
export function RepositoryName({ id }: { id: string }) {
  const query = useData<Repository>("/repositories/" + encodeURIComponent(id));
  return (
    <>
      {query.data?.name ?? `Repository (${id})`}
      {query.data?.archived ? " (archived)" : ""}
    </>
  );
}

/**
 * What something links to: each repository a chip that unlinks it, and "Link repository"
 * to search for another. Linking is optional. `folded` keeps it to one line inside a
 * form: the label is read out but not shown, and the search floats over what follows.
 */
export function RepositoryPicker({
  value,
  onChange,
  disabled = false,
  label = "Repositories",
  heading,
  folded = false,
  labelHidden = false,
}: {
  value: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  label?: string;
  /** Shows the label as a section heading, for pages made of sections. */
  heading?: "h2" | "h3";
  folded?: boolean;
  /** For a picker a tab already names: the label still names its group for assistive technology. */
  labelHidden?: boolean;
}) {
  const labelId = useId();
  const Root = heading ? "section" : "div";
  const Label = heading ?? "span";
  return (
    <Root
      className={"repository-picker" + (folded ? " folded" : "")}
      role="group"
      aria-labelledby={labelId}
    >
      <Label
        id={labelId}
        className={
          folded || labelHidden
            ? "visually-hidden"
            : heading
              ? undefined
              : "picker-label"
        }
      >
        {label}
      </Label>
      {value.map((id) => (
        <RepositoryChip
          key={id}
          id={id}
          disabled={disabled}
          onUnlink={() => onChange(value.filter((other) => other !== id))}
        />
      ))}
      <LinkPicker<Repository>
        action="Link repository"
        label={label}
        placeholder="Find repositories…"
        path={(q) => `/repositories?limit=20&q=${encodeURIComponent(q)}`}
        linked={(repo) => value.includes(repo.id)}
        option={(repo) => (
          <>
            <strong>{repo.name}</strong>
            {repo.url && <small>{repo.url}</small>}
          </>
        )}
        none={
          <>
            No repositories registered.{" "}
            <a href="#repositories">Add a repository</a>.
          </>
        }
        onPick={(repo) => onChange([...value, repo.id])}
        disabled={disabled}
        floating={folded}
      />
    </Root>
  );
}
function RepositoryChip({
  id,
  disabled,
  onUnlink,
}: {
  id: string;
  disabled: boolean;
  onUnlink: () => void;
}) {
  const query = useData<Repository>("/repositories/" + encodeURIComponent(id));
  const name = query.data?.name ?? `Repository (${id})`;
  return (
    <span className="repository-chip">
      <a href={"#repositories/" + id}>
        {name}
        {query.data?.archived ? " (archived)" : ""}
      </a>
      <button
        type="button"
        className="quiet"
        aria-label={"Unlink " + name}
        title={"Unlink " + name}
        disabled={disabled}
        onClick={onUnlink}
      >
        ×
      </button>
    </span>
  );
}

export function RepositoryFilter({
  value,
  onChange,
  label = "Filter work by repository",
}: {
  value: string;
  onChange: (id: string) => void;
  label?: string;
}) {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const query = useData<Answer<"/api/v1/repositories">>(
    `/repositories?limit=50&offset=${offset}&q=${encodeURIComponent(q)}`,
  );
  return (
    <div>
      <input
        aria-label="Find repository filter"
        placeholder="Find repository…"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOffset(0);
        }}
      />
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">All repositories</option>
        {value && !query.data?.items.some((r) => r.id === value) && (
          <option value={value}>Selected repository</option>
        )}
        {(query.data?.items ?? []).map((repo) => (
          <option key={repo.id} value={repo.id}>
            {repo.name} {repo.url}
          </option>
        ))}
      </select>
      {query.error ? (
        <p role="alert">Could not load repository choices.</p>
      ) : null}
      {(offset > 0 || (query.data?.total ?? 0) > 50) && (
        <span>
          <button
            type="button"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous filter choices
          </button>
          <button
            type="button"
            disabled={offset + 50 >= (query.data?.total ?? 0)}
            onClick={() => setOffset(offset + 50)}
          >
            Next filter choices
          </button>
        </span>
      )}
    </div>
  );
}
