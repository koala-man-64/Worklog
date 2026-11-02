import { useState } from "react";
import { useData } from "./hooks";
import type { List } from "./types";
import type { Repository } from "./repositoryTypes";

export function RepositoryBadges({
  ids = [],
  legacy = [],
}: {
  ids?: string[];
  legacy?: string[];
}) {
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

export function RepositoryPicker({
  value,
  onChange,
  disabled = false,
  label = "Linked repositories",
}: {
  value: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  label?: string;
}) {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const query = useData<List<Repository>>(
    `/repositories?limit=50&offset=${offset}&q=${encodeURIComponent(q)}`,
  );
  const unique = query.data?.items ?? [];
  return (
    <fieldset className="repository-picker" disabled={disabled}>
      <legend>{label}</legend>
      <input
        aria-label={label + " search"}
        placeholder="Find repositories…"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOffset(0);
        }}
      />
      {query.error ? (
        <p role="alert">
          Could not load repositories.{" "}
          <button type="button" onClick={() => void query.refetch()}>
            Retry repositories
          </button>
        </p>
      ) : null}
      {query.isPending ? (
        <p>Loading repositories…</p>
      ) : !query.error && !unique.length ? (
        <p>
          {q
            ? "No repositories match this search."
            : "No repositories registered."}{" "}
          <a href="#repositories">Add a repository</a>.
        </p>
      ) : null}
      {unique.map((repo) => (
        <label key={repo.id} title={repo.url || repo.id}>
          <input
            type="checkbox"
            checked={value.includes(repo.id)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...value, repo.id]
                  : value.filter((id) => id !== repo.id),
              )
            }
          />
          {repo.name}
          {repo.archived ? " (archived)" : ""}
          {repo.url && <small>{repo.url}</small>}
        </label>
      ))}
      {value
        .filter((id) => !unique.some((r) => r.id === id))
        .map((id) => (
          <label key={id}>
            <input
              type="checkbox"
              checked
              onChange={() => onChange(value.filter((v) => v !== id))}
            />
            <RepositoryName id={id} /> (selected)
          </label>
        ))}
      {(offset > 0 || (query.data?.total ?? 0) > 50) && (
        <span>
          <button
            type="button"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous repository choices
          </button>
          <button
            type="button"
            disabled={offset + 50 >= (query.data?.total ?? 0)}
            onClick={() => setOffset(offset + 50)}
          >
            Next repository choices
          </button>
        </span>
      )}
    </fieldset>
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
  const query = useData<List<Repository>>(
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
