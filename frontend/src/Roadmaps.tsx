import { useState } from "react";
import { localStore } from "./storage";
import { request, download, contextStorageKey } from "./api";
import { commands } from "./commands";
import { useAction, useCommand, useData } from "./hooks";
import {
  AgentChatsSection,
  Empty,
  ErrorMessage,
  Files,
  GithubRefsSection,
  History,
  LinksSection,
  Markdown,
  Notes,
  PeopleLinks,
  PersonBadges,
  RecoverRecords,
  StatusSelect,
} from "./components";
import type { Item, List, Person, Roadmap, Step } from "./types";
type Preview = {
  id: string;
  title: string;
  overview: string;
  steps: Step[];
  warnings: string[];
  sha256: string;
};
export function Roadmaps({ selected }: { selected?: string }) {
  const [archive, setArchive] = useState(false);
  const [personId, setPersonId] = useState("");
  const people = useData<List<Person>>("/people?archived=false");
  const roads = useData<List<Roadmap>>(
    `/roadmaps?archived=false${personId ? "&personId=" + encodeURIComponent(personId) : ""}`,
  );
  const old = useData<List<Roadmap>>(
    `/roadmaps?archived=true${personId ? "&personId=" + encodeURIComponent(personId) : ""}`,
  );
  const action = useAction();
  const [title, setTitle] = useState("");
  if (selected) return <RoadmapDetail id={selected} />;
  return (
    <>
      <header>
        <p className="eyebrow">See the shape of the work</p>
        <h1>Roadmaps</h1>
        <p className="lede">A living plan, with a history you can trust.</p>
      </header>
      <form
        className="quick-add"
        onSubmit={async (e) => {
          e.preventDefault();
          await action
            .mutateAsync({
              path: "/roadmaps",
              body: { title, overview: "", steps: [] },
            })
            .then(() => setTitle(""))
            .catch(() => undefined);
        }}
      >
        <input
          aria-label="Roadmap title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Name a new roadmap"
          required
        />
        <button className="primary">Create roadmap</button>
      </form>
      <ImportRoadmap />
      <div className="toolbar">
        <label>
          <input
            type="checkbox"
            checked={archive}
            onChange={(e) => setArchive(e.target.checked)}
          />{" "}
          Show archive
        </label>
        <select
          aria-label="Filter roadmaps by person"
          value={personId}
          onChange={(e) => setPersonId(e.target.value)}
        >
          <option value="">All people</option>
          {people.data?.items?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <ErrorMessage error={roads.error || old.error || action.error} />
      {!(archive ? old : roads).data?.items.length ? (
        <Empty>Your bigger picture starts here.</Empty>
      ) : (
        (archive ? old : roads).data?.items.map((r) => (
          <article className="roadmap-row" key={r.id}>
            <div>
              <a className="item-title" href={"#roadmaps/" + r.id}>
                {r.title}
              </a>
              <PersonBadges people={r.people} />
              <p>{r.overview?.slice(0, 160)}</p>
            </div>
            <button
              onClick={() =>
                action.mutate({
                  path: `/roadmaps/${r.id}/${archive ? "recover" : "archive"}`,
                  body: { revision: r.revision },
                })
              }
            >
              {archive ? "Recover" : "Archive"}
            </button>
          </article>
        ))
      )}
    </>
  );
}
function ImportRoadmap() {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const action = useAction();
  const roads = useData<List<Roadmap>>("/roadmaps?archived=false");
  const [referenceTarget, setReferenceTarget] = useState("");
  return (
    <section className="import-box">
      <h3>Bring in an HTML plan</h3>
      <p className="muted">
        Review the extracted structure before creating a roadmap. Original HTML
        is download only.
      </p>
      <input
        aria-label="Import HTML plan"
        type="file"
        accept=".html,.htm,text/html"
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (!file) return;
          if (file.size > 5000000) {
            setError("HTML imports must be 5 MB or smaller.");
            return;
          }
          try {
            const result = await request<Preview>(
              "/imports/preview?name=" + encodeURIComponent(file.name),
              "POST",
              file,
            );
            setPreview(result);
            setSelected(new Set(result.steps.map((s) => s.id)));
            setError("");
          } catch (error) {
            setError(String(error));
          }
        }}
      />
      <ErrorMessage error={error || action.error} />
      {preview && (
        <div>
          <label>
            Imported title
            <input
              value={preview.title}
              onChange={(e) =>
                setPreview({ ...preview, title: e.target.value })
              }
            />
          </label>
          <label>
            Overview
            <textarea
              value={preview.overview}
              onChange={(e) =>
                setPreview({ ...preview, overview: e.target.value })
              }
            />
          </label>
          {preview.warnings.map((w, i) => (
            <p key={i} className="warning">
              {w}
            </p>
          ))}
          {preview.steps.map((s) => (
            <div className="import-candidate" key={s.id}>
              <label className="line">
                <input
                  type="checkbox"
                  checked={selected.has(s.id)}
                  onChange={(e) => {
                    const next = new Set(selected);
                    if (e.target.checked) {
                      next.add(s.id);
                      let parent = preview.steps.find(
                        (p) => p.id === s.parentId,
                      );
                      while (parent) {
                        next.add(parent.id);
                        parent = preview.steps.find(
                          (p) => p.id === parent?.parentId,
                        );
                      }
                    } else {
                      next.delete(s.id);
                      let changed = true;
                      while (changed) {
                        changed = false;
                        preview.steps.forEach((p) => {
                          if (
                            p.parentId &&
                            !next.has(p.parentId) &&
                            next.delete(p.id)
                          )
                            changed = true;
                        });
                      }
                    }
                    setSelected(next);
                  }}
                />
                {s.title}
              </label>
              <details>
                <summary>Preview extracted content</summary>
                <Markdown text={s.description} />
              </details>
            </div>
          ))}
          <label>
            Or keep the original as a reference on an existing roadmap
            <select
              value={referenceTarget}
              onChange={(e) => setReferenceTarget(e.target.value)}
            >
              <option value="">Create a new roadmap from the selection</option>
              {roads.data?.items.map((r) => (
                <option value={r.id} key={r.id}>
                  {r.title}
                </option>
              ))}
            </select>
          </label>
          <div className="toolbar">
            <button
              className="primary"
              disabled={action.isPending}
              onClick={async () => {
                await action
                  .mutateAsync({
                    path: `/imports/${preview.id}/commit`,
                    body: {
                      title: preview.title,
                      overview: preview.overview,
                      steps: preview.steps.filter((s) => selected.has(s.id)),
                      ...(referenceTarget
                        ? {
                            roadmapId: referenceTarget,
                            revision: roads.data?.items.find(
                              (r) => r.id === referenceTarget,
                            )?.revision,
                          }
                        : {}),
                    },
                  })
                  .then(() => setPreview(null))
                  .catch(() => undefined);
              }}
            >
              {referenceTarget
                ? "Attach original as reference"
                : "Create from selected steps"}
            </button>
            <a href={download(`/imports/${preview.id}/download`)}>
              Download original
            </a>
            <button onClick={() => setPreview(null)}>Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}
export function RoadmapDetail({ id }: { id: string }) {
  const query = useData<Roadmap>("/roadmaps/" + id);
  const action = useAction();
  const createItem = useCommand(commands.createItem);
  const [editing, setEditing] = useState(false);
  const [snapshot, setSnapshot] = useState<unknown>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [newItemTitle, setNewItemTitle] = useState("");
  const [newItemStepId, setNewItemStepId] = useState("");
  const [workQuery, setWorkQuery] = useState("");
  const [pickItemId, setPickItemId] = useState("");
  const [pickStepId, setPickStepId] = useState("");
  const workList = useData<List<Item>>(
    `/work-items?view=all&archived=false&limit=100&q=${encodeURIComponent(workQuery)}`,
  );
  const road = query.data;
  if (!road)
    return (
      <>
        <ErrorMessage error={query.error} />
        <p>Loading roadmap…</p>
      </>
    );
  const active = road.steps.filter((s) => !s.retired);
  const leaves = active.filter(
    (s) => !active.some((child) => child.parentId === s.id),
  );
  const parents = active
    .filter((s) => active.some((child) => child.parentId === s.id))
    .map((s) => s.id);
  const completed = leaves.filter((s) => s.status === "done").length;
  const roadWorkItems = road.workItems ?? [];
  const unassignedWorkItems = roadWorkItems.filter(
    (w) => !w.stepId || !active.some((s) => s.id === w.stepId),
  );
  const renderWorkItem = (item: Item) => (
    <div className="line roadmap-work-item" key={item.id}>
      <span className={"status-dot " + item.status} />
      <a className="item-title" href={"#work/" + item.id}>
        {item.title}
      </a>
      <PersonBadges people={item.people} />
      <StatusSelect
        disabled={road.archived}
        label={"Status for " + item.title}
        value={item.status}
        onChange={(status) =>
          action.mutate({
            path: "/work-items/" + item.id,
            method: "PATCH",
            body: { revision: item.revision, status },
          })
        }
      />
      <select
        aria-label={"Step for " + item.title}
        disabled={road.archived}
        value={
          item.stepId && active.some((s) => s.id === item.stepId)
            ? item.stepId
            : ""
        }
        onChange={(e) =>
          action.mutate({
            path: "/work-items/" + item.id,
            method: "PATCH",
            body: {
              revision: item.revision,
              roadmapId: id,
              stepId: e.target.value || null,
            },
          })
        }
      >
        <option value="">Roadmap (no specific step)</option>
        {active.map((s) => (
          <option key={s.id} value={s.id}>
            Step: {s.title}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="quiet"
        disabled={road.archived}
        aria-label={"Unlink " + item.title}
        onClick={() =>
          action.mutate({
            path: "/work-items/" + item.id,
            method: "PATCH",
            body: {
              revision: item.revision,
              roadmapId: null,
              stepId: null,
            },
          })
        }
      >
        Unlink
      </button>
    </div>
  );
  const render = (parent: string | null, depth = 0): React.ReactNode =>
    active
      .filter((s) => s.parentId === parent)
      .sort((a, b) => a.position - b.position)
      .map((s) => {
        const kids = active.filter((c) => c.parentId === s.id).length;
        const closed = collapsed.has(s.id);
        const stepItems = roadWorkItems.filter((w) => w.stepId === s.id);
        return (
          <div
            key={s.id}
            className={"step status-" + s.status}
            style={{ marginLeft: depth ? 20 : 0 }}
          >
            <div className="section-heading">
              <div>
                {kids > 0 && (
                  <button
                    type="button"
                    className="tree-toggle"
                    aria-expanded={!closed}
                    aria-label={(closed ? "Expand " : "Collapse ") + s.title}
                    onClick={() => {
                      const next = new Set(collapsed);
                      if (closed) next.delete(s.id);
                      else next.add(s.id);
                      setCollapsed(next);
                    }}
                  >
                    {closed ? "▸" : "▾"}
                  </button>
                )}
                <span className={"status-dot " + s.status} aria-hidden="true" />
                <strong>{s.title}</strong>
                {closed && (
                  <small className="muted">
                    {" "}
                    · {kids} sub-step{kids === 1 ? "" : "s"} hidden
                  </small>
                )}
                {s.description && <Markdown text={s.description} />}
              </div>
              <StatusSelect
                disabled={
                  road.archived ||
                  active.some((child) => child.parentId === s.id)
                }
                label={"Progress for " + s.title}
                value={s.status}
                onChange={(status) =>
                  action.mutate({
                    path: "/steps/" + s.id + "/progress",
                    method: "PATCH",
                    body: { revision: s.revision, status },
                  })
                }
              />
            </div>
            {stepItems.length > 0 && (
              <div className="step-work-items">
                {stepItems.map(renderWorkItem)}
              </div>
            )}
            <button
              className="quiet"
              onClick={async () => {
                try {
                  setSnapshot(await request("/steps/" + s.id + "/history"));
                } catch (error) {
                  setSnapshot({ error: String(error) });
                }
              }}
            >
              Step history
            </button>
            <details>
              <summary>Step notes & files</summary>
              <Notes ownerType="step" ownerId={s.id} notes={s.notes ?? []} />
              <Files
                ownerType="step"
                ownerId={s.id}
                files={s.attachments ?? []}
              />
              <RecoverRecords
                groups={[
                  { label: "Notes", path: "notes", records: s.notes ?? [] },
                  {
                    label: "Files",
                    path: "attachments",
                    records: s.attachments ?? [],
                  },
                ]}
              />
            </details>
            {!closed && render(s.id, depth + 1)}
          </div>
        );
      });
  return (
    <>
      <a className="back" href="#roadmaps">
        ← All roadmaps
      </a>
      <header>
        <p className="eyebrow">Roadmap / revision {road.revision}</p>
        <h1>{road.title}</h1>
        <Markdown text={road.overview} />
        <div className="toolbar">
          <button
            className="primary"
            disabled={road.archived}
            onClick={() => setEditing(!editing)}
          >
            {editing ? "Close editor" : "Edit structure"}
          </button>
          <button
            onClick={() =>
              action.mutate({
                path: `/roadmaps/${id}/${road.archived ? "recover" : "archive"}`,
                body: { revision: road.revision },
              })
            }
          >
            {road.archived ? "Recover" : "Archive"}
          </button>
          <span>
            {completed} / {leaves.length} steps complete
          </span>
        </div>
        <progress
          max={leaves.length || 1}
          value={completed}
          aria-label="Roadmap completion"
        />
      </header>
      <ErrorMessage error={action.error || createItem.error || workList.error} />
      {editing && !road.archived && (
        <StructureEditor
          key={road.revision}
          roadmap={road}
          onDone={() => setEditing(false)}
        />
      )}
      <PeopleLinks
        targetType="roadmap"
        targetId={id}
        people={road.people}
        disabled={road.archived}
      />
      <section>
        <h3>
          Work items <span className="count">{roadWorkItems.length}</span>
        </h3>
        <form
          className="quick-add"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!newItemTitle.trim() || road.archived) return;
            await createItem
              .mutateAsync({
                title: newItemTitle,
                description: "",
                roadmapId: id,
                stepId: newItemStepId || null,
                worked: false,
              })
              .then(() => setNewItemTitle(""))
              .catch(() => undefined);
          }}
        >
          <input
            aria-label="New roadmap work item"
            placeholder="Add a work item to this roadmap…"
            value={newItemTitle}
            onChange={(e) => setNewItemTitle(e.target.value)}
            maxLength={500}
            disabled={road.archived}
          />
          <select
            aria-label="Step for new work item"
            value={newItemStepId}
            disabled={road.archived}
            onChange={(e) => setNewItemStepId(e.target.value)}
          >
            <option value="">Roadmap (no specific step)</option>
            {active.map((s) => (
              <option key={s.id} value={s.id}>
                Step: {s.title}
              </option>
            ))}
          </select>
          <button
            type="submit"
            className="primary"
            disabled={
              road.archived || createItem.isPending || !newItemTitle.trim()
            }
          >
            Add work item
          </button>
        </form>
        <form
          className="inline plan-picker"
          onSubmit={async (e) => {
            e.preventDefault();
            const chosen = workList.data?.items.find(
              (i) => i.id === pickItemId,
            );
            if (!chosen || road.archived) return;
            await action
              .mutateAsync({
                path: "/work-items/" + chosen.id,
                method: "PATCH",
                body: {
                  revision: chosen.revision,
                  roadmapId: id,
                  stepId: pickStepId || null,
                },
              })
              .then(() => setPickItemId(""))
              .catch(() => undefined);
          }}
        >
          <input
            aria-label="Find existing work"
            placeholder="Find existing work…"
            value={workQuery}
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            disabled={road.archived}
            onChange={(e) => {
              setWorkQuery(e.target.value);
              setPickItemId("");
            }}
          />
          <select
            aria-label="Choose existing work"
            value={pickItemId}
            disabled={road.archived}
            onChange={(e) => setPickItemId(e.target.value)}
          >
            <option value="">Attach existing work to this roadmap…</option>
            {workList.data?.items
              .filter(
                (i) =>
                  !roadWorkItems.some(
                    (w) =>
                      w.id === i.id &&
                      (w.stepId ?? "") === (pickStepId || ""),
                  ),
              )
              .map((i) => (
                <option value={i.id} key={i.id}>
                  {i.title}
                </option>
              ))}
          </select>
          <select
            aria-label="Step for existing work item"
            value={pickStepId}
            disabled={road.archived}
            onChange={(e) => setPickStepId(e.target.value)}
          >
            <option value="">Roadmap (no specific step)</option>
            {active.map((s) => (
              <option key={s.id} value={s.id}>
                Step: {s.title}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={road.archived || !pickItemId || action.isPending}
          >
            Attach work item
          </button>
        </form>
        {unassignedWorkItems.map(renderWorkItem)}
      </section>
      <div className="section-heading">
        <h2>Plan & progress</h2>
        {parents.length > 0 && (
          <span className="toolbar">
            <button
              className="quiet"
              disabled={parents.every((p) => collapsed.has(p))}
              onClick={() => setCollapsed(new Set(parents))}
            >
              Collapse all
            </button>
            <button
              className="quiet"
              disabled={!parents.some((p) => collapsed.has(p))}
              onClick={() => setCollapsed(new Set())}
            >
              Expand all
            </button>
          </span>
        )}
      </div>
      {!active.length ? (
        <Empty>Add the first step in the structure editor.</Empty>
      ) : (
        render(null)
      )}
      {road.steps.some((s) => s.retired) && (
        <details>
          <summary>Retired steps</summary>
          {road.steps
            .filter((s) => s.retired)
            .map((s) => (
              <p key={s.id}>
                {s.title} · {s.status}
              </p>
            ))}
        </details>
      )}
      <section>
        <h3>Immutable versions</h3>
        {road.versions.map((v) => (
          <div className="line" key={v.number}>
            <button
              onClick={async () => {
                try {
                  setSnapshot(
                    await request(`/roadmaps/${id}/revisions/${v.number}`),
                  );
                } catch (error) {
                  setSnapshot({ error: String(error) });
                }
              }}
            >
              View version {v.number}
            </button>
            <span>{v.note || "Structure saved"}</span>
            <small>{new Date(v.createdAt).toLocaleString()}</small>
            <button
              onClick={() =>
                action.mutate({
                  path: `/roadmaps/${id}/restore`,
                  body: { revision: road.revision, number: v.number },
                })
              }
            >
              Restore as new version
            </button>
          </div>
        ))}
      </section>
      {!!road.imports?.length && (
        <section>
          <h3>Original plan references</h3>
          {road.imports.map((file) => (
            <div className="line" key={file.id}>
              <a href={download(`/imports/${file.id}/download`)}>{file.name}</a>
              <small title={file.sha256}>
                SHA-256 {file.sha256.slice(0, 12)}…
              </small>
            </div>
          ))}
        </section>
      )}
      {snapshot !== null && (
        <section>
          <div className="section-heading">
            <h3>Historical record</h3>
            <button onClick={() => setSnapshot(null)}>Close</button>
          </div>
          <pre>{JSON.stringify(snapshot, null, 2)}</pre>
        </section>
      )}
      <Notes ownerType="roadmap" ownerId={id} notes={road.notes} />
      <LinksSection
        ownerType="roadmap"
        ownerId={id}
        links={road.links}
        disabled={road.archived}
      />
      <AgentChatsSection
        ownerType="roadmap"
        ownerId={id}
        chats={road.chats}
        disabled={road.archived}
      />
      <GithubRefsSection
        ownerType="roadmap"
        ownerId={id}
        github={road.github}
        chats={road.chats}
        disabled={road.archived}
      />
      <Files ownerType="roadmap" ownerId={id} files={road.attachments} />
      <RecoverRecords
        groups={[
          { label: "Notes", path: "notes", records: road.notes },
          { label: "Files", path: "attachments", records: road.attachments },
          { label: "Links", path: "links", records: road.links ?? [] },
          {
            label: "Agent chats",
            path: "chats",
            records: road.chats ?? [],
          },
          {
            label: "GitHub",
            path: "github",
            records: road.github ?? [],
          },
        ]}
      />
      <History items={road.history} />
    </>
  );
}
export function StructureEditor({
  roadmap,
  onDone,
}: {
  roadmap: Roadmap;
  onDone: () => void;
}) {
  const key = contextStorageKey("roadmap-structure", roadmap.id);
  const storedResult = localStore.read(key);
  const stored = storedResult.value;
  let draft: {
    title: string;
    overview: string;
    steps: Step[];
    revision: number;
    // Active step ids when the draft started; missing on drafts saved before it was recorded.
    baseStepIds?: string[];
  } | null = null;
  try {
    draft = stored ? JSON.parse(stored) : null;
  } catch {
    /* Preserve corrupt draft; start from canonical. */
  }
  // Editor state never shares objects with the query cache.
  const active = () => roadmap.steps.filter((s) => !s.retired);
  const [title, setTitle] = useState(draft?.title ?? roadmap.title);
  const [overview, setOverview] = useState(draft?.overview ?? roadmap.overview);
  const [steps, setSteps] = useState<Step[]>(
    () => draft?.steps ?? active().map((s) => ({ ...s })),
  );
  const [revision, setRevision] = useState(draft?.revision ?? roadmap.revision);
  const [baseStepIds, setBaseStepIds] = useState<string[] | null>(() =>
    draft ? (draft.baseStepIds ?? null) : active().map((s) => s.id),
  );
  const [recovered, setRecovered] = useState(draft !== null);
  const serverOnly = active().filter((s) => !steps.some((d) => d.id === s.id));
  const [keep, setKeep] = useState(() => new Set(serverOnly.map((s) => s.id)));
  const [note, setNote] = useState("");
  const [storageWarning, setStorageWarning] = useState(!storedResult.ok);
  const action = useAction();
  const stale = revision !== roadmap.revision;
  function persist(
    nextTitle: string,
    nextOverview: string,
    nextSteps: Step[],
    base = revision,
    baseIds = baseStepIds,
  ) {
    const persisted = localStore.write(
      key,
      JSON.stringify({
        title: nextTitle,
        overview: nextOverview,
        steps: nextSteps,
        revision: base,
        ...(baseIds ? { baseStepIds: baseIds } : {}),
      }),
    );
    if (!persisted) setStorageWarning(true);
  }
  function update(id: string, fields: Partial<Step>) {
    const next = steps.map((s) => (s.id === id ? { ...s, ...fields } : s));
    setSteps(next);
    persist(title, overview, next);
  }
  const move = (i: number, delta: number) => {
    const next = steps.map((s) => ({ ...s }));
    [next[i], next[i + delta]] = [next[i + delta], next[i]];
    next.forEach((s, index) => (s.position = index));
    setSteps(next);
    persist(title, overview, next);
  };
  function discard() {
    localStore.remove(key);
    setTitle(roadmap.title);
    setOverview(roadmap.overview);
    setSteps(active().map((s) => ({ ...s })));
    setRevision(roadmap.revision);
    setBaseStepIds(active().map((s) => s.id));
    setRecovered(false);
  }
  function rebase() {
    // Keep steps created elsewhere since the draft's base; steps the user removed stay retired.
    // Without a recorded base, the user chose which server-only steps to keep.
    const added = serverOnly
      .filter((s) =>
        baseStepIds ? !baseStepIds.includes(s.id) : keep.has(s.id),
      )
      .map((s) => ({ ...s }));
    const ids = new Set([...steps, ...added].map((s) => s.id));
    const next = [...steps, ...added].map((s, index) => ({
      ...s,
      parentId: s.parentId && ids.has(s.parentId) ? s.parentId : null,
      position: index,
    }));
    const baseIds = active().map((s) => s.id);
    setSteps(next);
    setRevision(roadmap.revision);
    setBaseStepIds(baseIds);
    setRecovered(false);
    persist(title, overview, next, roadmap.revision, baseIds);
  }
  return (
    <form
      className="structure-editor"
      onSubmit={async (e) => {
        e.preventDefault();
        await action
          .mutateAsync({
            path: `/roadmaps/${roadmap.id}/revisions`,
            body: {
              revision,
              title,
              overview,
              steps: steps.map((s, index) => ({
                id: s.id,
                title: s.title,
                description: s.description,
                parentId: s.parentId,
                position: index,
              })),
              note,
            },
          })
          .then(() => {
            localStore.remove(key);
            onDone();
          })
          .catch(() => undefined);
      }}
    >
      <h2>Edit roadmap structure</h2>
      {storageWarning && (
        <p className="warning">
          Browser draft storage unavailable; keep this tab open until saved.
        </p>
      )}
      <p className="muted">
        Saving creates an immutable version. Removed steps retire and keep their
        history.
      </p>
      {stale ? (
        <div className="warning" role="alert">
          <p>
            This draft is based on revision {revision}, but the roadmap is now
            at revision {roadmap.revision}, so saving it would be rejected.
            Rebase keeps your edits on the current revision; discard drops them.
          </p>
          {!baseStepIds && serverOnly.length > 0 && (
            <fieldset>
              <legend>
                Steps saved elsewhere that this draft does not include
              </legend>
              {serverOnly.map((s) => (
                <label key={s.id}>
                  <input
                    type="checkbox"
                    checked={keep.has(s.id)}
                    onChange={(e) => {
                      const next = new Set(keep);
                      if (e.target.checked) next.add(s.id);
                      else next.delete(s.id);
                      setKeep(next);
                    }}
                  />
                  Keep “{s.title}”
                </label>
              ))}
            </fieldset>
          )}
          <div className="toolbar">
            <button type="button" onClick={rebase}>
              Rebase onto current
            </button>
            <button type="button" onClick={discard}>
              Discard draft
            </button>
          </div>
        </div>
      ) : (
        recovered && (
          <p className="warning">
            Recovered a local draft. Its base revision is {revision}.
          </p>
        )
      )}
      <label>
        Title
        <input
          value={title}
          onChange={(e) => {
            setTitle(e.target.value);
            persist(e.target.value, overview, steps);
          }}
          required
        />
      </label>
      <label>
        Overview · Markdown
        <textarea
          value={overview}
          onChange={(e) => {
            setOverview(e.target.value);
            persist(title, e.target.value, steps);
          }}
        />
      </label>
      {steps.map((s, i) => (
        <fieldset key={s.id}>
          <legend>Step {i + 1}</legend>
          <input
            aria-label={"Step " + (i + 1) + " title"}
            value={s.title}
            onChange={(e) => update(s.id, { title: e.target.value })}
            required
          />
          <textarea
            aria-label={"Step " + (i + 1) + " description"}
            value={s.description}
            onChange={(e) => update(s.id, { description: e.target.value })}
          />
          <select
            aria-label={"Step " + (i + 1) + " parent"}
            value={s.parentId ?? ""}
            onChange={(e) => update(s.id, { parentId: e.target.value || null })}
          >
            <option value="">Top level</option>
            {steps
              .filter((p) => {
                let ancestor: Step | undefined = p;
                const visited = new Set<string>();
                while (ancestor && !visited.has(ancestor.id)) {
                  if (ancestor.id === s.id) return false;
                  visited.add(ancestor.id);
                  ancestor = steps.find((a) => a.id === ancestor?.parentId);
                }
                return true;
              })
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
          </select>
          <div className="toolbar">
            <button
              type="button"
              aria-label={"Move step " + (i + 1) + " up"}
              disabled={i === 0}
              onClick={() => move(i, -1)}
            >
              ↑
            </button>
            <button
              type="button"
              aria-label={"Move step " + (i + 1) + " down"}
              disabled={i === steps.length - 1}
              onClick={() => move(i, 1)}
            >
              ↓
            </button>
            <button
              type="button"
              onClick={() => {
                const next = steps
                  .filter((p) => p.id !== s.id)
                  .map((p) =>
                    p.parentId === s.id ? { ...p, parentId: s.parentId } : p,
                  );
                setSteps(next);
                persist(title, overview, next);
              }}
            >
              Retire step
            </button>
          </div>
        </fieldset>
      ))}
      <button
        type="button"
        onClick={() => {
          const next = [
            ...steps,
            {
              id: crypto.randomUUID(),
              title: "New step",
              description: "",
              parentId: null,
              position: steps.length,
              status: "open" as const,
              revision: 0,
            },
          ];
          setSteps(next);
          persist(title, overview, next);
        }}
      >
        Add step
      </button>
      <label>
        Version note
        <input value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <ErrorMessage error={action.error} />
      <button className="primary" disabled={action.isPending || stale}>
        Save new version
      </button>
    </form>
  );
}
