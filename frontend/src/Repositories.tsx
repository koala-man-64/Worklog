import { useState } from "react";
import { request } from "./api";
import { useAction, useData } from "./hooks";
import { DraftField, Empty, ErrorMessage } from "./components";
import { Coordination, OpenRuns, Pages } from "./Coordination";
import { RepositoryName } from "./RepositoryControls";
import type { Repository, RepositoryRegistration } from "./repositoryTypes";
import type { Item, List } from "./types";

export function Repositories({ selected }: { selected?: string }) {
  return selected ? (
    <RepositoryDetail key={selected} id={selected} />
  ) : (
    <RepositoryList />
  );
}
function RepositoryList() {
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const [showRuns, setShowRuns] = useState(false);
  const [showActivity, setShowActivity] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [existing, setExisting] = useState<Repository | null>(null);
  const action = useAction();
  const list = useData<List<Repository>>(
    `/repositories?archived=${archived}&limit=50&offset=${offset}&q=${encodeURIComponent(q)}`,
  );
  return (
    <>
      <header>
        <h1>Repositories</h1>
        <p className="lede">
          Shared repositories, linked work and agent-reported activity.
        </p>
      </header>
      <form
        className="quick-add"
        onSubmit={(e) => {
          e.preventDefault();
          setExisting(null);
          action.mutate(
            {
              path: "/repositories",
              body: { name: name.trim(), url: url.trim(), description: "" },
            },
            {
              onSuccess: (result) => {
                const registration = result as RepositoryRegistration;
                setExisting(
                  registration.created === false ? registration : null,
                );
                setName("");
                setUrl("");
              },
            },
          );
        }}
      >
        <input
          aria-label="Repository name"
          placeholder="Repository name…"
          required
          maxLength={200}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          aria-label="Repository remote URL"
          placeholder="Remote URL (optional)…"
          maxLength={2000}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button className="primary" disabled={!name.trim() || action.isPending}>
          Add repository
        </button>
      </form>
      {existing && (
        <p className="warning" role="status">
          That remote is already registered as{" "}
          <a href={"#repositories/" + existing.id}>{existing.name}</a>.
        </p>
      )}
      <ErrorMessage error={action.error || list.error} />
      <input
        aria-label="Find repositories"
        placeholder="Find repositories…"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOffset(0);
        }}
      />
      <label>
        <input
          type="checkbox"
          checked={archived}
          onChange={(e) => {
            setArchived(e.target.checked);
            setOffset(0);
          }}
        />{" "}
        Show archive
      </label>
      {list.isPending ? (
        <p>Loading repositories…</p>
      ) : !list.error && !list.data?.items.length ? (
        <Empty>
          {archived
            ? "No archived repositories."
            : "No repositories registered. Add one to connect work and reported activity."}
        </Empty>
      ) : null}
      <div className="work-list">
        {(list.data?.items ?? []).map((repo) => (
          <article className="work-row" key={repo.id}>
            <div>
              <a className="item-title" href={"#repositories/" + repo.id}>
                {repo.name}
              </a>
              <small>{repo.url || "No remote URL"}</small>
              {repo.mergedInto ? (
                <MergedInto id={repo.mergedInto} />
              ) : (
                repo.duplicateOf && <SameRemote id={repo.duplicateOf} />
              )}
              <p>{repo.description}</p>
            </div>
            {!repo.mergedInto && (
              <button
                disabled={action.isPending}
                onClick={() =>
                  action.mutate({
                    path: `/repositories/${repo.id}/${repo.archived ? "recover" : "archive"}`,
                    body: { revision: repo.revision },
                  })
                }
              >
                {repo.archived ? "Recover" : "Archive"}
              </button>
            )}
          </article>
        ))}
      </div>
      <Pages
        offset={offset}
        total={list.data?.total ?? 0}
        change={setOffset}
        label="repositories"
      />
      <details onToggle={(e) => setShowRuns(e.currentTarget.open)}>
        <summary>Open agent runs</summary>
        {showRuns && <OpenRuns />}
      </details>
      <details onToggle={(e) => setShowActivity(e.currentTarget.open)}>
        <summary>Reported activity across repositories</summary>
        {showActivity && <Coordination />}
      </details>
    </>
  );
}
// Only an upgrade leaves two records on one remote.
function SameRemote({ id }: { id: string }) {
  return (
    <p className="warning">
      Same remote as{" "}
      <a href={"#repositories/" + id}>
        <RepositoryName id={id} />
      </a>
      : two records for one repository.
    </p>
  );
}
function MergedInto({ id }: { id: string }) {
  return (
    <p className="muted">
      Merged into{" "}
      <a href={"#repositories/" + id}>
        <RepositoryName id={id} />
      </a>
      , which now answers for its remote.
    </p>
  );
}
// Folding one record into another is the person's call; it cannot be undone.
function MergeInto({ source }: { source: Repository }) {
  const [q, setQ] = useState("");
  const [target, setTarget] = useState(source.duplicateOf ?? "");
  const choices = useData<List<Repository>>(
    `/repositories?limit=50&q=${encodeURIComponent(q)}`,
  );
  const options = (choices.data?.items ?? []).filter(
    (repo) => repo.id !== source.id,
  );
  return (
    <details open={!!source.duplicateOf}>
      <summary>Merge into another repository</summary>
      <p className="muted">
        Merging moves this repository’s work and chat links, tickets, reported
        activity and updates to the other record, archives this one as merged,
        and makes its remote answer with the other. It cannot be undone.
      </p>
      <input
        aria-label="Find a repository to merge into"
        placeholder="Find repositories…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <select
        aria-label="Merge into"
        value={target}
        onChange={(e) => setTarget(e.target.value)}
      >
        <option value="">Choose a repository</option>
        {target && !options.some((repo) => repo.id === target) && (
          <option value={target}>Selected repository</option>
        )}
        {options.map((repo) => (
          <option key={repo.id} value={repo.id}>
            {repo.name}
            {repo.url ? ` · ${repo.url}` : ""}
          </option>
        ))}
      </select>
      <ErrorMessage error={choices.error} />
      {target && (
        <MergeConfirm key={target} source={source} targetId={target} />
      )}
    </details>
  );
}
function MergeConfirm({
  source,
  targetId,
}: {
  source: Repository;
  targetId: string;
}) {
  const target = useData<Repository>("/repositories/" + targetId);
  const action = useAction();
  const [confirm, setConfirm] = useState(false);
  const into = target.data;
  if (!into) return <ErrorMessage error={target.error} />;
  return confirm ? (
    <div className="decision">
      <strong>
        Merge {source.name} into {into.name}?
      </strong>
      <div className="toolbar">
        <button
          className="danger"
          disabled={action.isPending}
          onClick={() =>
            // The refetch shows this record merged and unmounts this control before a
            // mutate-level onSuccess would run, so follow the settled promise instead.
            action
              .mutateAsync({
                path: `/repositories/${source.id}/merge`,
                body: {
                  revision: source.revision,
                  intoId: into.id,
                  intoRevision: into.revision,
                },
              })
              .then(() => {
                location.hash = "repositories/" + into.id;
              })
              .catch(() => undefined)
          }
        >
          Confirm merge
        </button>
        <button onClick={() => setConfirm(false)}>Cancel</button>
      </div>
      <ErrorMessage error={action.error} />
    </div>
  ) : (
    <button onClick={() => setConfirm(true)}>Merge into {into.name}…</button>
  );
}
function RepositoryDetail({ id }: { id: string }) {
  const query = useData<Repository>("/repositories/" + id);
  const [offset, setOffset] = useState(0);
  const work = useData<List<Item>>(
    `/work-items?repositoryId=${encodeURIComponent(id)}&limit=50&offset=${offset}`,
  );
  const action = useAction();
  const repo = query.data;
  if (!repo)
    return (
      <>
        <ErrorMessage error={query.error} />
        {query.isPending && <p>Loading repository…</p>}
      </>
    );
  return (
    <>
      <a className="back" href="#repositories">
        All repositories
      </a>
      <header>
        <h1>{repo.name}</h1>
        {!repo.mergedInto && (
          <button
            disabled={action.isPending}
            onClick={() =>
              action.mutate({
                path: `/repositories/${id}/${repo.archived ? "recover" : "archive"}`,
                body: { revision: repo.revision },
              })
            }
          >
            {repo.archived ? "Recover repository" : "Archive repository"}
          </button>
        )}
      </header>
      {repo.mergedInto ? (
        <MergedInto id={repo.mergedInto} />
      ) : (
        <>
          {repo.archived && (
            <p>
              Archived repository. Existing links and reports remain visible.
            </p>
          )}
          {repo.duplicateOf && <SameRemote id={repo.duplicateOf} />}
          <ErrorMessage error={action.error} />
          {(
            [
              ["name", "Repository name", false],
              ["url", "Remote URL", false],
              ["description", "Description", true],
            ] as const
          ).map(([field, label, multiline]) => (
            <DraftField
              key={field}
              draftKey={`repository-${field}-${id}`}
              label={label}
              initial={repo[field]}
              revision={repo.revision}
              multiline={multiline}
              maxLength={
                field === "name" ? 200 : field === "url" ? 2000 : 10000
              }
              save={(value, revision) =>
                request<Repository>("/repositories/" + id, "PATCH", {
                  revision,
                  [field]: value,
                })
              }
            />
          ))}
          <MergeInto source={repo} />
        </>
      )}
      <section>
        <h2>Linked work</h2>
        <ErrorMessage error={work.error} />
        {work.isPending ? (
          <p>Loading linked work…</p>
        ) : !work.error && !work.data?.items.length ? (
          <Empty>No work linked to this repository.</Empty>
        ) : null}
        {(work.data?.items ?? []).map((item) => (
          <p key={item.id}>
            <a href={"#work/" + item.id}>{item.title}</a> · {item.status}
          </p>
        ))}
        <Pages
          offset={offset}
          total={work.data?.total ?? 0}
          change={setOffset}
          label="linked work"
        />
      </section>
      <Coordination repositoryId={id} />
    </>
  );
}
