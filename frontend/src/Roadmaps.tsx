import {
  Fragment,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { localStore } from "./storage";
import { roadmapChildren } from "./RoadmapNav";
import { request, download, contextStorageKey } from "./api";
import { commands } from "./commands";
import { useAction, useCommand, useData, useStoredWidth } from "./hooks";
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
  ResizeHandle,
  StatusSelect,
} from "./components";
import type { Item, List, Person, Roadmap, Step } from "./types";
import { Icon } from "./icons";
// Matches the outline column's clamp in styles.css: the outline never narrower
// than 300px, and the detail pane keeps 300px beside the 24px handle.
const OUTLINE_MIN = 300;
const DETAIL_MIN = 300;
const HANDLE_WIDTH = 24;
type Preview = {
  id: string;
  title: string;
  overview: string;
  steps: Step[];
  warnings: string[];
  sha256: string;
};
export function Roadmaps({
  selected,
  step,
}: {
  selected?: string;
  step?: string;
}) {
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
  if (selected) return <RoadmapDetail id={selected} step={step} />;
  const shown = (archive ? old : roads).data?.items ?? [];
  const tree = roadmapChildren(shown);
  const rows = (parent: string | null): React.ReactNode =>
    tree.get(parent)?.map((r) => (
      <li key={r.id}>
        <article className="roadmap-row">
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
        {tree.has(r.id) && <ul className="roadmap-tree">{rows(r.id)}</ul>}
      </li>
    ));
  return (
    <div className="bounded-screen">
      <header>
        <h1>Roadmaps</h1>
        <p className="lede">
          Plans broken into steps, with every saved version kept.
        </p>
      </header>
      <ErrorMessage error={roads.error || old.error || action.error} />
      <div className="split roadmaps-split">
        <div className="panel" role="region" aria-label="Your roadmaps">
          <div className="panel-head toolbar">
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
          <div className="panel-body">
            {!shown.length ? (
              <Empty>Your bigger picture starts here.</Empty>
            ) : (
              <ul className="roadmap-tree">{rows(null)}</ul>
            )}
          </div>
        </div>
        <div className="pane" role="region" aria-label="Start a roadmap">
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
        </div>
      </div>
    </div>
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
function readCollapsed(key: string) {
  try {
    const ids: unknown = JSON.parse(localStore.read(key).value ?? "[]");
    return new Set(
      Array.isArray(ids)
        ? ids.filter((i): i is string => typeof i === "string")
        : [],
    );
  } catch {
    return new Set<string>();
  }
}
export function RoadmapDetail({ id, step }: { id: string; step?: string }) {
  const query = useData<Roadmap>("/roadmaps/" + id);
  const action = useAction();
  const createItem = useCommand(commands.createItem);
  const [editing, setEditing] = useState(false);
  const [snapshot, setSnapshot] = useState<unknown>(null);
  // Collapsed steps persist per roadmap so a large plan reopens as it was left.
  const collapsedKey = contextStorageKey("roadmap-collapsed", id);
  const [collapsed, setCollapsedState] = useState<Set<string>>(() =>
    readCollapsed(collapsedKey),
  );
  const [revealed, setRevealed] = useState<string | undefined>();
  const [newItemTitle, setNewItemTitle] = useState("");
  const [newItemStepId, setNewItemStepId] = useState("");
  const [workQuery, setWorkQuery] = useState("");
  const [pickItemId, setPickItemId] = useState("");
  const [pickStepId, setPickStepId] = useState("");
  const [subTitle, setSubTitle] = useState("");
  const roads = useData<List<Roadmap>>("/roadmaps?archived=false");
  const outlineRef = useRef<HTMLDivElement>(null);
  const splitRef = useRef<HTMLDivElement>(null);
  const outlinePanelRef = useRef<HTMLDivElement>(null);
  const outlineWidth = useStoredWidth("outline-width", OUTLINE_MIN, 2000);
  const detailRef = useRef<HTMLDivElement>(null);
  const snapshotRef = useRef<HTMLElement>(null);
  const workList = useData<List<Item>>(
    `/work-items?view=all&archived=false&limit=100&q=${encodeURIComponent(workQuery)}`,
  );
  const road = query.data;
  const loaded = road !== undefined;
  // Panes scroll on their own; when stacked on a narrow window, the page scrolls to the detail.
  useEffect(() => {
    if (!step || !loaded) return;
    outlineRef.current
      ?.querySelector('[aria-current="true"]')
      ?.scrollIntoView?.({ block: "nearest" });
    detailRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [step, loaded]);
  useEffect(() => {
    if (snapshot !== null)
      snapshotRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [snapshot]);
  function setCollapsed(next: Set<string>) {
    setCollapsedState(next);
    localStore.write(collapsedKey, JSON.stringify([...next]));
  }
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
  const parentSteps = active.filter((s) =>
    active.some((child) => child.parentId === s.id),
  );
  const parents = parentSteps.map((s) => s.id);
  // A parent's progress is derived: it is done only once all of its leaves are.
  const doneParents = parentSteps
    .filter((s) => s.status === "done")
    .map((s) => s.id);
  const completed = leaves.filter((s) => s.status === "done").length;
  const roadWorkItems = road.workItems ?? [];
  const selected = step ? active.find((s) => s.id === step) : undefined;
  // A step opened by link shows in the outline: expand its collapsed ancestors once.
  if (step !== revealed) {
    setRevealed(step);
    const ancestors = new Set<string>();
    let parent = active.find((s) => s.id === selected?.parentId);
    while (parent && !ancestors.has(parent.id)) {
      ancestors.add(parent.id);
      parent = active.find((s) => s.id === parent?.parentId);
    }
    if ([...ancestors].some((a) => collapsed.has(a)))
      setCollapsedState(
        new Set([...collapsed].filter((c) => !ancestors.has(c))),
      );
  }
  const parentStep = selected && active.find((s) => s.id === selected.parentId);
  const children = road.children ?? [];
  // Any active roadmap outside this one's own subtree can become its parent.
  const tree = roadmapChildren(roads.data?.items ?? []);
  const placements = (
    parent: string | null,
    depth: number,
  ): { r: Roadmap; depth: number }[] =>
    (tree.get(parent) ?? []).flatMap((r) =>
      r.id === id ? [] : [{ r, depth }, ...placements(r.id, depth + 1)],
    );
  const parentOptions = placements(null, 0);
  // The current parent stays selectable while the list loads or once it is archived.
  const unlistedParent =
    road.parentId && !parentOptions.some(({ r }) => r.id === road.parentId)
      ? road.ancestors?.at(-1)
      : undefined;
  const selectedItems = selected
    ? roadWorkItems.filter((w) => w.stepId === selected.id)
    : [];
  const showHistory = async (path: string) => {
    try {
      setSnapshot(await request(path));
    } catch (error) {
      setSnapshot({ error: String(error) });
    }
  };
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
  const render = (parent: string | null): React.ReactNode =>
    active
      .filter((s) => s.parentId === parent)
      .sort((a, b) => a.position - b.position)
      .map((s) => {
        const kids = active.filter((c) => c.parentId === s.id).length;
        const closed = kids > 0 && collapsed.has(s.id);
        const items = roadWorkItems.filter((w) => w.stepId === s.id).length;
        const current = s.id === selected?.id;
        return (
          <li key={s.id} className={"step status-" + s.status}>
            <div className={"step-row" + (current ? " selected" : "")}>
              {kids > 0 ? (
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
                  <Icon name={closed ? "expand" : "collapse"} size={14} />
                </button>
              ) : (
                <span className="tree-spacer" />
              )}
              <span className={"status-dot " + s.status} aria-hidden="true" />
              <span className="step-text">
                <a
                  className="step-link"
                  href={`#roadmaps/${id}/${s.id}`}
                  aria-current={current ? "true" : undefined}
                >
                  {s.title}
                </a>
                {(closed || items > 0) && (
                  <span className="step-meta">
                    {closed && (
                      <small>
                        ({kids} sub-step{kids === 1 ? "" : "s"} hidden)
                      </small>
                    )}
                    {items > 0 && (
                      <small>
                        {items} work item{items === 1 ? "" : "s"}
                      </small>
                    )}
                  </span>
                )}
              </span>
              <StatusSelect
                disabled={road.archived || kids > 0}
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
            {kids > 0 && !closed && <ul>{render(s.id)}</ul>}
          </li>
        );
      });
  const overview = (
    <>
      {step && (
        <p className="warning">
          That step is not in the current plan. It may have been retired.
        </p>
      )}
      <h2>Overview</h2>
      {road.overview ? (
        <Markdown text={road.overview} />
      ) : (
        <p className="muted">No overview yet. Add one with Edit structure.</p>
      )}
      <PeopleLinks
        targetType="roadmap"
        targetId={id}
        people={road.people}
        disabled={road.archived}
      />
      <section>
        <h3>
          Sub-roadmaps <span className="count">{children.length}</span>
        </h3>
        {children.map((c) => (
          <div className="line" key={c.id}>
            <a className="item-title" href={"#roadmaps/" + c.id}>
              {c.title}
            </a>
            {c.archived && <small className="muted">Archived</small>}
          </div>
        ))}
        <form
          className="quick-add"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!subTitle.trim() || road.archived) return;
            await action
              .mutateAsync({
                path: "/roadmaps",
                body: {
                  title: subTitle,
                  overview: "",
                  steps: [],
                  parentId: id,
                },
              })
              .then(() => setSubTitle(""))
              .catch(() => undefined);
          }}
        >
          <input
            aria-label="New sub-roadmap title"
            placeholder="Name a sub-roadmap…"
            value={subTitle}
            onChange={(e) => setSubTitle(e.target.value)}
            maxLength={1000}
            disabled={road.archived}
          />
          <button
            type="submit"
            disabled={road.archived || action.isPending || !subTitle.trim()}
          >
            Add sub-roadmap
          </button>
        </form>
        <label className="line">
          Nested under
          <select
            aria-label="Nested under"
            value={road.parentId ?? ""}
            disabled={road.archived || action.isPending}
            onChange={(e) =>
              action.mutate({
                path: `/roadmaps/${id}/move`,
                body: {
                  revision: road.revision,
                  parentId: e.target.value || null,
                },
              })
            }
          >
            <option value="">Top level</option>
            {unlistedParent && (
              <option value={unlistedParent.id}>
                {unlistedParent.title +
                  (unlistedParent.archived ? " (archived)" : "")}
              </option>
            )}
            {parentOptions.map(({ r, depth }) => (
              <option key={r.id} value={r.id}>
                {"  ".repeat(depth) + (r.title || "Untitled")}
              </option>
            ))}
          </select>
        </label>
      </section>
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
                      w.id === i.id && (w.stepId ?? "") === (pickStepId || ""),
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
        {roadWorkItems.map(renderWorkItem)}
      </section>
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
      <section>
        <h3>Immutable versions</h3>
        {road.versions.map((v) => (
          <div className="line" key={v.number}>
            <button
              onClick={() =>
                showHistory(`/roadmaps/${id}/revisions/${v.number}`)
              }
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
      {road.steps.some((s) => s.retired) && (
        <details>
          <summary>Retired steps</summary>
          {road.steps
            .filter((s) => s.retired)
            .map((s) => (
              <p key={s.id}>
                {s.title} ({s.status})
              </p>
            ))}
        </details>
      )}
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
  const stepDetail = selected && (
    <>
      <a className="back" href={"#roadmaps/" + id}>
        <Icon name="back" />
        Roadmap overview
      </a>
      <div className="section-heading step-title">
        <h2>{selected.title}</h2>
        <button
          className="quiet"
          onClick={() => showHistory("/steps/" + selected.id + "/history")}
        >
          Step history
        </button>
      </div>
      {parentStep && (
        <p className="muted">
          Part of{" "}
          <a href={`#roadmaps/${id}/${parentStep.id}`}>{parentStep.title}</a>
        </p>
      )}
      {selected.description ? (
        <Markdown text={selected.description} />
      ) : (
        <p className="muted">
          No description yet. Add one with Edit structure.
        </p>
      )}
      <section>
        <h3>
          Work items <span className="count">{selectedItems.length}</span>
        </h3>
        {selectedItems.length ? (
          selectedItems.map(renderWorkItem)
        ) : (
          <p className="muted">
            None on this step. Attach work from the roadmap overview.
          </p>
        )}
      </section>
      <Notes
        ownerType="step"
        ownerId={selected.id}
        notes={selected.notes ?? []}
      />
      <Files
        ownerType="step"
        ownerId={selected.id}
        files={selected.attachments ?? []}
      />
      <RecoverRecords
        groups={[
          { label: "Notes", path: "notes", records: selected.notes ?? [] },
          {
            label: "Files",
            path: "attachments",
            records: selected.attachments ?? [],
          },
        ]}
      />
    </>
  );
  const editor = editing && !road.archived;
  return (
    <div className="bounded-screen">
      <header className="roadmap-head">
        <nav className="crumbs" aria-label="Roadmap location">
          <a className="back" href="#roadmaps">
            <Icon name="back" />
            All roadmaps
          </a>
          {road.ancestors?.map((a) => (
            <Fragment key={a.id}>
              <span aria-hidden="true">/</span>
              <a href={"#roadmaps/" + a.id}>
                {a.title}
                {a.archived && " (archived)"}
              </a>
            </Fragment>
          ))}
        </nav>
        <div className="roadmap-title">
          <h1>{road.title}</h1>
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
          </div>
        </div>
        <div className="roadmap-progress">
          <progress
            max={leaves.length || 1}
            value={completed}
            aria-label="Roadmap completion"
          />
          <span>
            {completed} / {leaves.length} steps complete
          </span>
          <span className="muted">Revision {road.revision}</span>
        </div>
      </header>
      <ErrorMessage
        error={action.error || createItem.error || workList.error}
      />
      <div
        className="split roadmap-split"
        ref={splitRef}
        style={
          outlineWidth.width === null
            ? undefined
            : ({
                "--outline-width": outlineWidth.width + "px",
              } as CSSProperties)
        }
      >
        <div
          className="panel"
          role="region"
          aria-label="Plan outline"
          ref={outlinePanelRef}
        >
          <div className="panel-head">
            <h2>Plan & progress</h2>
            {parents.length > 0 && (
              <span className="toolbar">
                <button
                  className="quiet"
                  disabled={doneParents.every((p) => collapsed.has(p))}
                  onClick={() =>
                    setCollapsed(new Set([...collapsed, ...doneParents]))
                  }
                >
                  Collapse done
                </button>
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
          <div className="panel-body" ref={outlineRef}>
            {!active.length ? (
              <Empty>Add the first step in the structure editor.</Empty>
            ) : (
              <ul className="outline">{render(null)}</ul>
            )}
          </div>
        </div>
        <ResizeHandle
          label="Resize plan outline"
          panel={outlinePanelRef}
          min={OUTLINE_MIN}
          max={() =>
            (splitRef.current?.getBoundingClientRect().width ?? 0) -
            HANDLE_WIDTH -
            DETAIL_MIN
          }
          onResize={outlineWidth.resize}
          onReset={outlineWidth.reset}
        />
        <div
          className="pane detail"
          role="region"
          aria-label={
            editor
              ? "Structure editor"
              : selected
                ? "Step details"
                : "Roadmap details"
          }
          ref={detailRef}
          key={(editor ? "editor:" : "") + (selected?.id ?? "roadmap")}
        >
          {snapshot !== null && (
            <section className="snapshot" ref={snapshotRef}>
              <div className="section-heading">
                <h3>Historical record</h3>
                <button onClick={() => setSnapshot(null)}>Close</button>
              </div>
              <pre>{JSON.stringify(snapshot, null, 2)}</pre>
            </section>
          )}
          {editor ? (
            <StructureEditor
              key={road.revision}
              roadmap={road}
              onDone={() => setEditing(false)}
            />
          ) : (
            (stepDetail ?? overview)
          )}
        </div>
      </div>
    </div>
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
        Overview (Markdown)
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
              <Icon name="up" />
            </button>
            <button
              type="button"
              aria-label={"Move step " + (i + 1) + " down"}
              disabled={i === steps.length - 1}
              onClick={() => move(i, 1)}
            >
              <Icon name="down" />
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
      <div className="editor-actions">
        <label>
          Version note
          <input value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <ErrorMessage error={action.error} />
        <button className="primary" disabled={action.isPending || stale}>
          Save new version
        </button>
      </div>
    </form>
  );
}
