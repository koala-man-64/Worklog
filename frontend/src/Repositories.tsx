import { useState } from "react";
import { useData, useWrites } from "./hooks";
import { commands } from "./commands";
import { Files } from "./sections/Files";
import { PeopleLinks } from "./sections/PeopleLinks";
import { RecoverRecords } from "./sections/RecoverRecords";
import { DraftField } from "./ui/DraftField";
import { Empty, ErrorMessage } from "./ui/Messages";
import { statusLabel } from "./ui/Status";
import { Pager } from "./ui/Pager";
import { Coordination, OpenRuns } from "./Coordination";
import { LinkPicker } from "./LinkPicker";
import { RepositoryName, RepositoryPicker } from "./RepositoryControls";
import {
  RepositorySlotsSection,
  SlotCountControl,
  SlotSummaryPills,
} from "./RepositorySlots";
import { workClassLabel } from "./RoadmapExecution";
import type {
  Answer,
  Item,
  Repository,
  RepositoryDetail,
  RepositoryQueue,
  RepositoryRegistration,
} from "./apiTypes";

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
  const action = useWrites();
  const list = useData<Answer<"/api/v1/repositories">>(
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
            () =>
              commands.registerRepository({
                name: name.trim(),
                url: url.trim(),
                description: "",
              }),
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
      <div className="toolbar">
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
      </div>
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
              {!repo.mergedInto && (
                <>
                  <SlotSummaryPills summary={repo.slotSummary} />
                  {(repo.tier != null || repo.wipCap != null) && (
                    <span className="exec-pills">
                      {repo.tier != null && (
                        <span className="exec-pill">Tier {repo.tier}</span>
                      )}
                      {repo.wipCap != null && (
                        <span className="exec-pill">WIP cap {repo.wipCap}</span>
                      )}
                    </span>
                  )}
                </>
              )}
              <small>{repo.url || "No remote URL"}</small>
              {repo.mergedInto ? (
                <MergedInto id={repo.mergedInto} />
              ) : (
                repo.duplicateOf && <SameRemote id={repo.duplicateOf} />
              )}
              <Registrant repo={repo} edit />
              <p>{repo.description}</p>
            </div>
            {!repo.mergedInto && !repo.archived && repo.slotSummary && (
              <SlotCountControl
                key={`${repo.id}:${repo.slotSummary.revision}`}
                repo={repo}
                summary={repo.slotSummary}
              />
            )}
            {!repo.mergedInto && (
              <button
                disabled={action.isPending}
                onClick={() =>
                  action.mutate(() =>
                    commands.setRepositoryArchived(repo.id, !repo.archived, {
                      revision: repo.revision,
                    }),
                  )
                }
              >
                {repo.archived ? "Recover" : "Archive"}
              </button>
            )}
          </article>
        ))}
      </div>
      <Pager
        offset={offset}
        total={list.data?.total ?? 0}
        onChange={setOffset}
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
// What an agent registered for itself waits for the person, who confirms it here or edits it.
function Registrant({ repo, edit }: { repo: Repository; edit?: boolean }) {
  const action = useWrites();
  const by = repo.registeredBy;
  if (by?.kind !== "agent") return null;
  const agent = by.agentId ? (
    <a href={"#agents/" + by.agentId}>{by.label}</a>
  ) : (
    by.label
  );
  if (repo.reviewed || repo.mergedInto)
    return <small>Registered by {agent}</small>;
  return (
    <div className="warning">
      Registered by {agent} · unreviewed{" "}
      <button
        disabled={action.isPending}
        onClick={() =>
          action.mutate(() =>
            commands.confirmRepository(repo.id, { revision: repo.revision }),
          )
        }
      >
        Confirm
      </button>
      {edit && (
        <a className="button" href={"#repositories/" + repo.id}>
          Edit
        </a>
      )}
      <ErrorMessage error={action.error} />
    </div>
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
  const choices = useData<Answer<"/api/v1/repositories">>(
    `/repositories?limit=50&q=${encodeURIComponent(q)}`,
  );
  const options = (choices.data?.items ?? []).filter(
    (repo) => repo.id !== source.id,
  );
  return (
    <details open={!!source.duplicateOf}>
      <summary>Merge into another repository</summary>
      <p className="muted">
        Merging moves this repository’s work and chat links, people, tickets,
        files, reported activity and updates to the other record, archives this
        one as merged, and makes its remote answer with the other. It cannot be
        undone.
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
  const target = useData<RepositoryDetail>("/repositories/" + targetId);
  const action = useWrites();
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
              .mutateAsync(() =>
                commands.mergeRepository(source.id, {
                  revision: source.revision,
                  intoId: into.id,
                  intoRevision: into.revision,
                }),
              )
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
// A link lives on the work item, so linking from here replaces the item's own set under
// the revision it was read at: a change made elsewhere meanwhile is refused, not lost.
function LinkedWork({ repo }: { repo: Repository }) {
  const [offset, setOffset] = useState(0);
  const work = useData<Answer<"/api/v1/work-items">>(
    `/work-items?repositoryId=${encodeURIComponent(repo.id)}&limit=50&offset=${offset}`,
  );
  const action = useWrites();
  const relink = (item: Item, repositoryIds: string[]) =>
    action.mutate(() =>
      commands.updateItem(item.id, { revision: item.revision, repositoryIds }),
    );
  return (
    <section>
      <h2>Linked work</h2>
      <ErrorMessage error={work.error || action.error} />
      {work.isPending ? (
        <p>Loading linked work…</p>
      ) : !work.error && !work.data?.items.length ? (
        <Empty>No work linked to this repository.</Empty>
      ) : null}
      {(work.data?.items ?? []).map((item) => (
        <p className="line" key={item.id}>
          <span>
            <a href={"#work/" + item.id}>{item.title}</a> · {item.status}
          </span>
          <button
            className="quiet"
            aria-label={"Unlink " + item.title}
            disabled={action.isPending}
            onClick={() =>
              relink(
                item,
                (item.repositoryIds ?? []).filter((other) => other !== repo.id),
              )
            }
          >
            Unlink
          </button>
        </p>
      ))}
      <Pager
        offset={offset}
        total={work.data?.total ?? 0}
        onChange={setOffset}
        label="linked work"
      />
      {/* Only an active record takes new links; what it already has can still be unlinked. */}
      {!repo.archived && (
        <LinkPicker<Item>
          action="Link work item"
          label="Work to link"
          placeholder="Find work…"
          path={(q) =>
            `/work-items?view=all&archived=false&limit=20&q=${encodeURIComponent(q)}`
          }
          linked={(item) => !!item.repositoryIds?.includes(repo.id)}
          option={(item) => (
            <>
              <span>{item.title}</span>
              <small>{statusLabel(item.status)}</small>
            </>
          )}
          none="No work to link yet."
          onPick={(item) =>
            relink(item, [...(item.repositoryIds ?? []), repo.id])
          }
          disabled={action.isPending}
        />
      )}
    </section>
  );
}
// Documents kept with the repository, such as designs and runbooks. As with links, only an
// active record takes new ones; an archived record's files can still be archived and recovered.
function RepositoryFiles({ repo }: { repo: Repository }) {
  const owner = `/attachments?ownerType=repository&ownerId=${encodeURIComponent(repo.id)}`;
  const active = useData<Answer<"/api/v1/attachments">>(owner);
  const archived = useData<Answer<"/api/v1/attachments">>(
    owner + "&archived=true",
  );
  return (
    <section>
      <h2>Files</h2>
      <ErrorMessage error={active.error || archived.error} />
      {active.isPending ? (
        <p>Loading files…</p>
      ) : !active.error && !active.data?.items.length ? (
        <Empty>No files attached to this repository.</Empty>
      ) : null}
      <Files
        ownerType="repository"
        ownerId={repo.id}
        files={active.data?.items ?? []}
        attach={!repo.archived}
        heading={false}
      />
      <RecoverRecords
        groups={[
          {
            label: "Files",
            path: "attachments",
            records: archived.data?.items ?? [],
          },
        ]}
      />
    </section>
  );
}
function RepositoryDetail({ id }: { id: string }) {
  const query = useData<RepositoryDetail>("/repositories/" + id);
  const action = useWrites();
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
      <header className="title-row">
        <h1>{repo.name}</h1>
        {!repo.mergedInto && (
          <button
            disabled={action.isPending}
            onClick={() =>
              action.mutate(() =>
                commands.setRepositoryArchived(id, !repo.archived, {
                  revision: repo.revision,
                }),
              )
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
              Archived repository. Existing links, files and reports remain
              visible.
            </p>
          )}
          {repo.duplicateOf && <SameRemote id={repo.duplicateOf} />}
          <Registrant repo={repo} />
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
                commands.updateRepository(id, { revision, [field]: value })
              }
            />
          ))}
          <MergeInto source={repo} />
        </>
      )}
      <LinkedWork repo={repo} />
      {!repo.mergedInto && <RepositorySlotsSection repo={repo} />}
      {!repo.mergedInto && <RepositoryExecutionPolicy repo={repo} />}
      {!repo.mergedInto && <RepositoryQueueSection repo={repo} />}
      {/* A merge moves every tie and file to the record that answers for this one. An archived
          record keeps its ties, and their roles, but takes no new one until it is recovered. */}
      {!repo.mergedInto && (
        <PeopleLinks
          targetType="repository"
          targetId={repo.id}
          people={repo.people}
          canTie={!repo.archived}
          headingLevel={2}
        />
      )}
      {!repo.mergedInto && <RepositoryFiles repo={repo} />}
      <Coordination repositoryId={id} />
    </>
  );
}

function RepositoryExecutionPolicy({ repo }: { repo: RepositoryDetail }) {
  const action = useWrites();
  const [tier, setTier] = useState(repo.tier != null ? String(repo.tier) : "");
  const [wipCap, setWipCap] = useState(
    repo.wipCap != null ? String(repo.wipCap) : "",
  );
  const initialUpstreams = repo.upstreamIds ?? [];
  const [upstreamIds, setUpstreamIds] = useState<string[]>(initialUpstreams);
  const downstreams = repo.downstreamIds ?? [];
  const parsedTier = tier === "" ? null : Number.parseInt(tier, 10);
  const parsedCap = wipCap === "" ? null : Number.parseInt(wipCap, 10);
  const upstreamsChanged =
    upstreamIds.length !== initialUpstreams.length ||
    upstreamIds.some((uid, idx) => uid !== initialUpstreams[idx]);
  const changed =
    parsedTier !== (repo.tier ?? null) ||
    parsedCap !== (repo.wipCap ?? null) ||
    upstreamsChanged;

  return (
    <section aria-label="Execution policy">
      <h2>Execution policy</h2>
      <form
        className="roadmap-record-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!changed || action.isPending || repo.archived) return;
          action.mutate(() =>
            commands.updateRepository(repo.id, {
              revision: repo.revision,
              tier: parsedTier,
              wipCap: parsedCap,
              upstreamIds: upstreamIds.filter((uid) => uid !== repo.id),
            }),
          );
        }}
      >
        <div className="exec-inline-fields">
          <label>
            Dependency tier
            <select
              aria-label="Dependency tier"
              disabled={repo.archived || action.isPending}
              value={tier}
              onChange={(e) => setTier(e.target.value)}
            >
              <option value="">Unset</option>
              <option value="1">Tier 1 (Foundation / Contracts)</option>
              <option value="2">Tier 2 (Services)</option>
              <option value="3">Tier 3 (Edge / UI)</option>
              <option value="4">Tier 4</option>
              <option value="5">Tier 5</option>
              <option value="6">Tier 6</option>
              <option value="7">Tier 7</option>
              <option value="8">Tier 8</option>
              <option value="9">Tier 9</option>
            </select>
          </label>
          <label>
            WIP cap (blank inherits slot cap)
            <input
              aria-label="WIP cap"
              type="number"
              min={1}
              max={99}
              placeholder="Inherit slot cap"
              disabled={repo.archived || action.isPending}
              value={wipCap}
              onChange={(e) => setWipCap(e.target.value)}
            />
          </label>
        </div>
        <RepositoryPicker
          label="Upstream repositories"
          value={upstreamIds}
          onChange={(ids) => setUpstreamIds(ids.filter((uid) => uid !== repo.id))}
          disabled={repo.archived || action.isPending}
        />
        {downstreams.length > 0 && (
          <p className="muted">
            Downstream consumers:{" "}
            {downstreams.map((cid, idx) => (
              <span key={cid}>
                {idx > 0 && ", "}
                <a href={"#repositories/" + cid}>
                  <RepositoryName id={cid} />
                </a>
              </span>
            ))}
          </p>
        )}
        <ErrorMessage error={action.error} />
        {!repo.archived && (
          <div className="toolbar">
            <button
              type="submit"
              className="primary"
              disabled={!changed || action.isPending}
            >
              Save execution policy
            </button>
          </div>
        )}
      </form>
    </section>
  );
}

function RepositoryQueueSection({ repo }: { repo: RepositoryDetail }) {
  const queueQuery = useData<RepositoryQueue>(
    `/repositories/${repo.id}/queue`,
    30_000,
  );
  const action = useWrites();
  const queue = queueQuery.data;
  if (!queue || !Array.isArray(queue.doing)) return null;

  const reorder = (index: number, delta: number) => {
    const ids = queue.queued.map((s) => s.id);
    const swap = index + delta;
    if (swap < 0 || swap >= ids.length) return;
    [ids[index], ids[swap]] = [ids[swap], ids[index]];
    action.mutate(() =>
      commands.reorderRepositoryQueue(repo.id, { stepIds: ids }),
    );
  };

  return (
    <section aria-label="Execution queue">
      <h2>Execution queue</h2>
      <div className="exec-pills">
        <span className={"exec-pill" + (queue.overWipCap ? " is-warning" : "")}>
          {queue.effectiveWipCap !== null
            ? `WIP ${queue.doingCount}/${queue.effectiveWipCap} doing · ${queue.activeWriters}/${queue.effectiveWipCap} writers`
            : `${queue.doingCount} doing · ${queue.activeWriters} writers`}
        </span>
        {queue.freezes
          .filter((f) => f.freezeStatus !== "open")
          .map((f) => (
            <span
              key={f.waveId}
              className={`exec-pill freeze-${f.freezeStatus}`}
            >
              {f.waveName}: {f.freezeStatus}
              {f.frozenRef ? ` @ ${f.frozenRef}` : ""}
            </span>
          ))}
      </div>
      <ErrorMessage error={action.error} />
      {queue.doing.length === 0 &&
      queue.queued.length === 0 &&
      queue.blocked.length === 0 ? (
        <Empty>No active roadmap steps are linked to this repository.</Empty>
      ) : (
        <>
          {queue.doing.length > 0 && (
            <>
              <h3>
                Doing <span className="count">{queue.doing.length}</span>
              </h3>
              <ul className="exec-step-list">
                {queue.doing.map((s) => (
                  <li className="exec-step-item" key={s.id}>
                    <div className="exec-step-main">
                      <span className="status-dot doing" aria-hidden="true" />
                      <div className="exec-step-text">
                        <a href={`#roadmaps/${s.roadmapId}/${s.id}`}>
                          {s.title}
                        </a>
                        <small className="muted">
                          {s.roadmapTitle}
                          {s.workClass ? ` · ${workClassLabel(s.workClass)}` : ""}
                          {s.waveName ? ` · ${s.waveName}` : ""}
                        </small>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
          {queue.queued.length > 0 && (
            <>
              <h3>
                Queued <span className="count">{queue.queued.length}</span>
              </h3>
              <ul className="exec-step-list">
                {queue.queued.map((s, idx) => (
                  <li className="exec-step-item" key={s.id}>
                    <div className="exec-step-main">
                      <span className="status-dot open" aria-hidden="true" />
                      <div className="exec-step-text">
                        <a href={`#roadmaps/${s.roadmapId}/${s.id}`}>
                          {s.title}
                        </a>
                        <small className="muted">
                          {s.roadmapTitle}
                          {s.workClass ? ` · ${workClassLabel(s.workClass)}` : ""}
                          {s.waveName ? ` · ${s.waveName}` : ""}
                        </small>
                      </div>
                    </div>
                    {!repo.archived && (
                      <div className="toolbar">
                        <button
                          type="button"
                          className="quiet"
                          aria-label={`Move ${s.title} up`}
                          disabled={idx === 0 || action.isPending}
                          onClick={() => reorder(idx, -1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className="quiet"
                          aria-label={`Move ${s.title} down`}
                          disabled={
                            idx === queue.queued.length - 1 || action.isPending
                          }
                          onClick={() => reorder(idx, 1)}
                        >
                          ↓
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
          {queue.blocked.length > 0 && (
            <>
              <h3>
                Blocked <span className="count">{queue.blocked.length}</span>
              </h3>
              <ul className="exec-step-list">
                {queue.blocked.map((s) => (
                  <li className="exec-step-item" key={s.id}>
                    <div className="exec-step-main">
                      <span className="status-dot blocked" aria-hidden="true" />
                      <div className="exec-step-text">
                        <a href={`#roadmaps/${s.roadmapId}/${s.id}`}>
                          {s.title}
                        </a>
                        <small className="muted">{s.roadmapTitle}</small>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}
