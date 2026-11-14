import {
  Fragment,
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { localStore, readIdSet } from "./storage";
import { roadmapChildren } from "./RoadmapNav";
import { ApiError, contextStorageKey } from "./api";
import { commands } from "./commands";
import {
  refreshAfterWrite,
  useCollapsed,
  useCommand,
  useData,
  useStoredWidth,
  useWrites,
} from "./hooks";
import { StepAgentChats } from "./sections/AgentChats";
import { Files } from "./sections/Files";
import { GithubRefsSection } from "./sections/GithubRefs";
import { ChangeHistory, History } from "./sections/History";
import { LinksSection } from "./sections/Links";
import { Notes } from "./sections/Notes";
import { PeopleLinks, PersonBadges } from "./sections/PeopleLinks";
import { RecoverRecords } from "./sections/RecoverRecords";
import { Markdown } from "./ui/Markdown";
import { Empty, ErrorMessage } from "./ui/Messages";
import { ResizeHandle } from "./ui/ResizeHandle";
import {
  ITEM_STATUSES,
  STATUSES,
  StatusSelect,
  statusLabel,
} from "./ui/Status";
import { DecisionsSection, QuestionsSection } from "./QuestionsDecisions";
import { AgentChips, AgentRoster, rosterOn, workersOn } from "./RoadmapAgents";
import { AppliedProposals } from "./ProposalHistory";
import { RoadmapChats } from "./RoadmapChats";
import type {
  Answer,
  PlanStep,
  Roadmap,
  RoadmapAgents,
  RoadmapListed,
  RoadmapWorker,
  RoadmapWorkItem,
  Status,
  Step,
} from "./apiTypes";
import { Icon } from "./icons";
// Matches the outline column's clamp in styles/roadmaps.css: the outline never
// narrower than 300px, and the detail pane keeps 300px beside the 24px handle.
const OUTLINE_MIN = 300;
const DETAIL_MIN = 300;
const HANDLE_WIDTH = 24;
// Agents report as they work, so who works where refreshes as on the Agents page.
const AGENTS_REFRESH = 30_000;
// Steps by status, in the order a status bar fills: done first, then in progress, then blocked.
// Open steps are the bar's empty track. The roadmap's counts and the Phases columns follow it.
const BAR_STATUSES: readonly Status[] = ["done", "doing", "blocked"];
const COUNTED_STATUSES: readonly Status[] = [...BAR_STATUSES, "open"];
/** Steps by status in one bar; the counts beside it say the same in numbers. */
function StatusBar({ steps }: { steps: Step[] }) {
  return (
    <span className="status-bar" aria-hidden="true">
      {BAR_STATUSES.map((status) => {
        const share =
          steps.filter((s) => s.status === status).length / (steps.length || 1);
        return (
          share > 0 && (
            <span
              key={status}
              className={status}
              style={{ width: `${share * 100}%` }}
            />
          )
        );
      })}
    </span>
  );
}
export function Roadmaps({
  selected,
  step,
  chat,
}: {
  selected?: string;
  step?: string;
  chat?: string;
}) {
  const [archive, setArchive] = useState(false);
  const roads = useData<Answer<"/api/v1/roadmaps">>("/roadmaps?archived=false");
  const old = useData<Answer<"/api/v1/roadmaps">>("/roadmaps?archived=true");
  const action = useWrites();
  const [title, setTitle] = useState("");
  const { collapsed, toggle } = useCollapsed(
    contextStorageKey("roadmaps-collapsed", "list"),
  );
  if (selected) return <RoadmapDetail id={selected} step={step} chat={chat} />;
  const shown = (archive ? old : roads).data?.items ?? [];
  const tree = roadmapChildren(shown);
  // Rows keep one toggle column only when some roadmap has sub-roadmaps.
  const nested = tree.size > 1;
  const rows = (parent: string | null): React.ReactNode =>
    tree.get(parent)?.map((r) => {
      const kids = tree.get(r.id)?.length ?? 0;
      const closed = kids > 0 && collapsed.has(r.id);
      return (
        <li key={r.id}>
          <article className="roadmap-row">
            <div className="roadmap-summary">
              {kids > 0 ? (
                <button
                  type="button"
                  className="tree-toggle"
                  aria-expanded={!closed}
                  aria-label={(closed ? "Expand " : "Collapse ") + r.title}
                  onClick={() => toggle(r.id)}
                >
                  <Icon name={closed ? "expand" : "collapse"} size={14} />
                </button>
              ) : (
                nested && <span className="tree-spacer" />
              )}
              <div>
                <a className="item-title" href={"#roadmaps/" + r.id}>
                  {r.title}
                </a>
                <PersonBadges people={r.people} />
                <p>{r.overview?.slice(0, 160)}</p>
                {closed && (
                  <small>
                    ({kids} sub-roadmap{kids === 1 ? "" : "s"} hidden)
                  </small>
                )}
              </div>
            </div>
            <button
              onClick={() =>
                action.mutate(() =>
                  commands.setRoadmapArchived(r.id, !archive, {
                    revision: r.revision,
                  }),
                )
              }
            >
              {archive ? "Recover" : "Archive"}
            </button>
          </article>
          {kids > 0 && !closed && (
            <ul className="roadmap-tree">{rows(r.id)}</ul>
          )}
        </li>
      );
    });
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
                .mutateAsync(() =>
                  commands.createRoadmap({ title, overview: "", steps: [] }),
                )
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
        </div>
      </div>
    </div>
  );
}
/**
 * The roadmap page's views, each at its own address: the status board at the roadmap's own, the
 * others after it. A step's address opens the outline with the step beside it.
 */
const VIEWS = [
  { id: "status", label: "Status" },
  { id: "outline", label: "Outline" },
  { id: "questions", label: "Questions and decisions" },
  { id: "chats", label: "Chats" },
  { id: "material", label: "Material" },
  { id: "history", label: "History" },
] as const;
type View = (typeof VIEWS)[number]["id"];
const viewAddress = (roadmapId: string, view: View) =>
  "#roadmaps/" + roadmapId + (view === "status" ? "" : "/" + view);
// Each list on the status board shows this many, then says how many more there are.
const BOARD_ROWS = 8;
type Tree = Map<string | null, Step[]>;
/**
 * Steps under their parent's ID, in position order; a step whose parent isn't among them sits at
 * the top, under null.
 */
function stepTree(steps: Step[]): Tree {
  const ids = new Set(steps.map((s) => s.id));
  const tree: Tree = new Map();
  for (const s of steps) {
    const parent = s.parentId && ids.has(s.parentId) ? s.parentId : null;
    const siblings = tree.get(parent);
    if (siblings) siblings.push(s);
    else tree.set(parent, [s]);
  }
  for (const siblings of tree.values())
    siblings.sort(
      (a, b) => a.position - b.position || a.id.localeCompare(b.id),
    );
  return tree;
}
/** The tree's steps depth-first, each before its children: the outline's order. */
function outlineOrder(tree: Tree): Step[] {
  const ordered: Step[] = [];
  const seen = new Set<string>();
  const walk = (s: Step) => {
    if (seen.has(s.id)) return;
    seen.add(s.id);
    ordered.push(s);
    for (const kid of tree.get(s.id) ?? []) walk(kid);
  };
  for (const top of tree.get(null) ?? []) walk(top);
  // Steps in a loop of parents have no top to reach them from: each starts a branch of its own.
  for (const siblings of tree.values()) for (const s of siblings) walk(s);
  return ordered;
}
export function RoadmapDetail({
  id,
  step: sub,
  chat,
}: {
  id: string;
  // After the roadmap, the address names a view or a step.
  step?: string;
  // After the Chats view, the chat it opens.
  chat?: string;
}) {
  const named = VIEWS.find((v) => v.id === sub)?.id;
  const step = named ? undefined : sub;
  const addressed: View = named ?? (step ? "outline" : "status");
  const query = useData<Roadmap>("/roadmaps/" + id);
  const action = useWrites();
  const [editing, setEditing] = useState(false);
  // The step whose own text is being edited, alone, apart from the structure editor.
  const [editingStep, setEditingStep] = useState<string | null>(null);
  const [address, setAddress] = useState(sub);
  const [showRetired, setShowRetired] = useState(false);
  // Collapsed steps persist per roadmap so a large plan reopens as it was left.
  const collapsedKey = contextStorageKey("roadmap-collapsed", id);
  const [collapsed, setCollapsedState] = useState<Set<string>>(() =>
    readIdSet(collapsedKey),
  );
  const [revealed, setRevealed] = useState<string | undefined>();
  const [subTitle, setSubTitle] = useState("");
  const roads = useData<Answer<"/api/v1/roadmaps">>("/roadmaps?archived=false");
  const outlineRef = useRef<HTMLDivElement>(null);
  const splitRef = useRef<HTMLDivElement>(null);
  const outlinePanelRef = useRef<HTMLDivElement>(null);
  const outlineWidth = useStoredWidth("outline-width", OUTLINE_MIN, 2000);
  const detailRef = useRef<HTMLDivElement>(null);
  const viewHeading = useId();
  const stepAgents = useData<RoadmapAgents>(
    `/roadmaps/${id}/agents`,
    AGENTS_REFRESH,
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
  // The structure editor sits beside the outline. Another view closes it; it keeps its draft.
  if (sub !== address) {
    setAddress(sub);
    if (addressed !== "outline") setEditing(false);
  }
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
  const editor = editing && !road.archived;
  const view: View = editor ? "outline" : addressed;
  const active = road.steps.filter((s) => !s.retired);
  const tree = stepTree(active);
  const ordered = outlineOrder(tree);
  const leaves = active.filter((s) => !tree.has(s.id));
  const parentSteps = active.filter((s) => tree.has(s.id));
  // A parent's progress is derived: it is done only once all of its leaves are.
  const doneParents = parentSteps
    .filter((s) => s.status === "done")
    .map((s) => s.id);
  const roadWorkItems = road.workItems ?? [];
  const roadChats = road.chats ?? [];
  // Opening another roadmap keeps the last one's answer as a placeholder: its agents aren't this one's.
  const agents = stepAgents.isPlaceholderData ? undefined : stepAgents.data;
  const workers = agents?.items ?? [];
  const roster = agents?.roster;
  const titles = new Map(active.map((s) => [s.id, s.title]));
  const stepTitle = (stepId: string) =>
    titles.get(stepId) ?? "a step no longer in the plan";
  // A collapsed step shows who works on the sub-steps it hides.
  const subtree = (root: string) => {
    const ids = new Set([root]);
    for (const id of ids) for (const kid of tree.get(id) ?? []) ids.add(kid.id);
    return ids;
  };
  const selected = step ? active.find((s) => s.id === step) : undefined;
  // A step opened by link shows in the outline: expand its collapsed ancestors once.
  if (step !== revealed) {
    setRevealed(step);
    // Another step's page closes an unsaved edit of this one's text.
    setEditingStep(null);
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
  const family = roadmapChildren(roads.data?.items ?? []);
  const placements = (
    parent: string | null,
    depth: number,
  ): { r: RoadmapListed; depth: number }[] =>
    (family.get(parent) ?? []).flatMap((r) =>
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
  // Work on a sub-step is work on the step above it. The roster holds a roadmap's first 200
  // agents, so past that a step lists only those among them.
  const stepRoster = selected
    ? rosterOn(roster?.items ?? [], subtree(selected.id))
    : [];
  const openQuestions = (road.questions ?? []).filter(
    (q) => q.status === "open" && !q.archived,
  ).length;
  /** A work item's row. Material names the step it is on, which its page attaches work to. */
  const renderWorkItem = (item: RoadmapWorkItem, onStepPage = false) => (
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
        options={ITEM_STATUSES}
        onChange={(status) =>
          action.mutate(() =>
            commands.updateItem(item.id, { revision: item.revision, status }),
          )
        }
      />
      {!onStepPage &&
        item.stepId &&
        (titles.has(item.stepId) ? (
          <a href={`#roadmaps/${id}/${item.stepId}`}>
            Step: {stepTitle(item.stepId)}
          </a>
        ) : (
          <small className="muted">On {stepTitle(item.stepId)}</small>
        ))}
      <button
        type="button"
        className="quiet"
        disabled={road.archived}
        aria-label={"Unlink " + item.title}
        onClick={() =>
          action.mutate(() =>
            commands.updateItem(item.id, {
              revision: item.revision,
              roadmapId: null,
              stepId: null,
            }),
          )
        }
      >
        Unlink
      </button>
    </div>
  );
  const hasRetired = road.steps.some((s) => s.retired);
  // Asked for, retired steps show at their old places in the outline.
  const shownTree = showRetired && hasRetired ? stepTree(road.steps) : tree;
  // The steps the outline can fold: those it shows steps under.
  const foldable = [...shownTree.keys()].filter(
    (key): key is string => key !== null,
  );
  const toggle = (s: Step, closed: boolean) => (
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
  );
  const render = (parent: string | null): React.ReactNode =>
    (shownTree.get(parent) ?? []).map((s) => {
      const kids = shownTree.get(s.id)?.length ?? 0;
      const closed = kids > 0 && collapsed.has(s.id);
      const head =
        kids > 0 ? toggle(s, closed) : <span className="tree-spacer" />;
      const below = kids > 0 && !closed && <ul>{render(s.id)}</ul>;
      if (s.retired)
        return (
          <li key={s.id} className="step retired">
            <div className="step-row">
              {head}
              <span className={"status-dot " + s.status} aria-hidden="true" />
              <span className="step-text">
                <span className="step-name" title={s.title}>
                  {s.title}
                </span>
                <span className="step-meta">
                  <small>Retired · {statusLabel(s.status)}</small>
                </span>
              </span>
            </div>
            {below}
          </li>
        );
      const items = roadWorkItems.filter((w) => w.stepId === s.id).length;
      const placed = workers.length
        ? workersOn(workers, closed ? subtree(s.id) : new Set([s.id]))
        : [];
      const current = s.id === selected?.id;
      return (
        <li key={s.id} className={"step status-" + s.status}>
          <div className={"step-row" + (current ? " selected" : "")}>
            {head}
            <span className={"status-dot " + s.status} aria-hidden="true" />
            <span className="step-text">
              <a
                className="step-link"
                href={`#roadmaps/${id}/${s.id}`}
                title={s.title}
                aria-current={current ? "true" : undefined}
              >
                {s.title}
              </a>
              {(closed || items > 0 || placed.length > 0) && (
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
                  <AgentChips
                    placed={placed}
                    stepTitle={stepTitle}
                    here={s.id}
                  />
                </span>
              )}
            </span>
            <StatusSelect
              disabled={road.archived || tree.has(s.id)}
              label={"Progress for " + s.title}
              value={s.status}
              options={STATUSES}
              onChange={(status) =>
                action.mutate(() =>
                  commands.setStepProgress(s.id, {
                    revision: s.revision,
                    status,
                  }),
                )
              }
            />
          </div>
          {below}
        </li>
      );
    });
  const stepDetail = selected && (
    <>
      {editingStep === selected.id && !road.archived ? (
        <StepTextForm step={selected} onDone={() => setEditingStep(null)} />
      ) : (
        <div className="section-heading step-title">
          <h2>{selected.title}</h2>
          <div className="toolbar">
            <button
              className="quiet"
              disabled={road.archived}
              onClick={() => setEditingStep(selected.id)}
            >
              Edit step
            </button>
          </div>
        </div>
      )}
      {parentStep && (
        <p className="muted">
          Part of{" "}
          <a href={`#roadmaps/${id}/${parentStep.id}`}>{parentStep.title}</a>
        </p>
      )}
      {editingStep === selected.id && !road.archived ? null : (
        <StepOutcome outcome={selected.outcome} />
      )}
      {editingStep === selected.id &&
      !road.archived ? null : selected.description ? (
        <Markdown text={selected.description} />
      ) : (
        <p className="muted">No description yet. Add one with Edit step.</p>
      )}
      <section>
        <h3>
          Work items <span className="count">{selectedItems.length}</span>
        </h3>
        {selectedItems.length ? (
          selectedItems.map((item) => renderWorkItem(item, true))
        ) : (
          <p className="muted">None on this step yet.</p>
        )}
        <StepWorkForms
          key={selected.id}
          roadmapId={id}
          stepId={selected.id}
          onStep={new Set(selectedItems.map((w) => w.id))}
          disabled={road.archived}
        />
      </section>
      <StepAgentChats
        roadmapId={id}
        step={selected}
        steps={active}
        chats={roadChats}
        disabled={road.archived}
      />
      <AgentRoster
        agents={stepRoster}
        total={stepRoster.length}
        loaded={!!roster}
        error={stepAgents.error}
        note={
          tree.has(selected.id)
            ? "From their work on this step and the steps under it."
            : "From their work on this step."
        }
        empty="No agent has worked on this step yet."
      />
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
      <ChangeHistory id={selected.id} />
    </>
  );
  const statusView = (
    <>
      <StatusBoard
        id={id}
        road={road}
        tree={tree}
        ordered={ordered}
        workers={workers}
        stepTitle={stepTitle}
      />
      <section>
        <h3>Overview</h3>
        {road.overview ? (
          <Markdown text={road.overview} />
        ) : (
          <p className="muted">No overview yet. Add one with Edit structure.</p>
        )}
      </section>
      <PeopleLinks
        targetType="roadmap"
        targetId={id}
        people={road.people}
        disabled={road.archived}
      />
      <AgentRoster
        agents={roster?.items ?? []}
        total={roster?.total ?? 0}
        loaded={!!roster}
        error={stepAgents.error}
        note="From their work here: reported work, status updates, responsibilities, discussions and tracked chats."
        empty="No agent has worked on this roadmap yet."
      />
    </>
  );
  const questionsView = (
    <>
      <QuestionsSection
        roadmapId={id}
        questions={road.questions}
        disabled={road.archived}
      />
      <DecisionsSection
        roadmapId={id}
        decisions={road.decisions}
        disabled={road.archived}
      />
      <RecoverRecords
        groups={[
          {
            label: "Questions",
            path: "questions",
            records: road.questions ?? [],
          },
          {
            label: "Decisions",
            path: "decisions",
            records: road.decisions ?? [],
          },
        ]}
      />
    </>
  );
  const materialView = (
    <>
      <section>
        <h3>
          Work items <span className="count">{roadWorkItems.length}</span>
        </h3>
        {roadWorkItems.length ? (
          roadWorkItems.map((item) => renderWorkItem(item))
        ) : (
          <p className="muted">
            None yet. Add or attach work on a step's page.
          </p>
        )}
      </section>
      <Notes ownerType="roadmap" ownerId={id} notes={road.notes} />
      <LinksSection
        ownerType="roadmap"
        ownerId={id}
        links={road.links}
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
            label: "GitHub",
            path: "github",
            records: road.github ?? [],
          },
        ]}
      />
    </>
  );
  const historyView = (
    <>
      <section>
        <h3>Immutable versions</h3>
        {road.versions.map((v) => (
          <div className="line" key={v.number}>
            <strong>Version {v.number}</strong>
            <span>{v.note || "Structure saved"}</span>
            <small>{new Date(v.createdAt).toLocaleString()}</small>
            <button
              onClick={() =>
                action.mutate(() =>
                  commands.restoreRoadmap(id, {
                    revision: road.revision,
                    number: v.number,
                  }),
                )
              }
            >
              Restore as new version
            </button>
          </div>
        ))}
      </section>
      <AppliedProposals path={"/roadmaps/" + id} noun="roadmap" />
      <History items={road.history} />
    </>
  );
  const shown = VIEWS.find((v) => v.id === view)!;
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
        <div className="roadmap-title title-row">
          <h1>{road.title}</h1>
          <div className="toolbar">
            <button
              className="primary"
              disabled={road.archived}
              onClick={() => {
                if (!editing && addressed !== "outline")
                  location.hash = viewAddress(id, "outline");
                setEditing(!editing);
              }}
            >
              {editing ? "Close editor" : "Edit structure"}
            </button>
            <button
              onClick={() =>
                action.mutate(() =>
                  commands.setRoadmapArchived(id, !road.archived, {
                    revision: road.revision,
                  }),
                )
              }
            >
              {road.archived ? "Recover" : "Archive"}
            </button>
          </div>
        </div>
        <div className="roadmap-progress">
          <StatusBar steps={leaves} />
          <span>
            {leaves.length} step{leaves.length === 1 ? "" : "s"}
          </span>
          <ul className="status-counts" aria-label="Steps by status">
            {COUNTED_STATUSES.map((status) => (
              <li key={status}>
                <span className={"status-dot " + status} aria-hidden="true" />
                {leaves.filter((s) => s.status === status).length} {status}
              </li>
            ))}
          </ul>
        </div>
        {/* Where the roadmap sits among the others, until a step opens into its own roadmap (2.1). */}
        <div className="roadmap-family">
          <label>
            Nested under
            <select
              aria-label="Nested under"
              value={road.parentId ?? ""}
              disabled={road.archived || action.isPending}
              onChange={(e) =>
                action.mutate(() =>
                  commands.moveRoadmap(id, {
                    revision: road.revision,
                    parentId: e.target.value || null,
                  }),
                )
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
          {children.length > 0 && (
            <span>
              Sub-roadmaps:{" "}
              {children.map((c, index) => (
                <Fragment key={c.id}>
                  {index > 0 && ", "}
                  <a href={"#roadmaps/" + c.id}>
                    {c.title}
                    {c.archived && " (archived)"}
                  </a>
                </Fragment>
              ))}
            </span>
          )}
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (!subTitle.trim() || road.archived) return;
              await action
                .mutateAsync(() =>
                  commands.createRoadmap({
                    title: subTitle,
                    overview: "",
                    steps: [],
                    parentId: id,
                  }),
                )
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
        </div>
        <nav className="roadmap-views" aria-label="Roadmap views">
          {VIEWS.map((v) => (
            <a
              key={v.id}
              href={viewAddress(id, v.id)}
              aria-current={v.id === view ? "page" : undefined}
              // A count would read as a number run into the name, so the name says it in words.
              aria-label={
                v.id === "questions" && openQuestions > 0
                  ? `${v.label}, ${openQuestions} open`
                  : undefined
              }
            >
              {v.label}
              {v.id === "questions" && openQuestions > 0 && (
                <span className="count">{openQuestions}</span>
              )}
            </a>
          ))}
        </nav>
      </header>
      <ErrorMessage error={action.error} />
      {view === "outline" ? (
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
              {(foldable.length > 0 || hasRetired) && (
                <span className="toolbar">
                  {foldable.length > 0 && (
                    <>
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
                        disabled={foldable.every((p) => collapsed.has(p))}
                        onClick={() => setCollapsed(new Set(foldable))}
                      >
                        Collapse all
                      </button>
                      <button
                        className="quiet"
                        disabled={!foldable.some((p) => collapsed.has(p))}
                        onClick={() => setCollapsed(new Set())}
                      >
                        Expand all
                      </button>
                    </>
                  )}
                  {hasRetired && (
                    <label className="retired-toggle">
                      <input
                        type="checkbox"
                        checked={showRetired}
                        onChange={(e) => setShowRetired(e.target.checked)}
                      />{" "}
                      Show retired steps
                    </label>
                  )}
                </span>
              )}
            </div>
            <div className="panel-body" ref={outlineRef}>
              <ErrorMessage error={stepAgents.error} />
              {workers.length > 0 && (
                <p className="agents-here">
                  <small className="muted">Agents working here</small>
                  <AgentChips
                    placed={workersOn(workers)}
                    stepTitle={stepTitle}
                    limit={8}
                  />
                </p>
              )}
              {!shownTree.size ? (
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
            aria-label={editor ? "Structure editor" : "Step details"}
            ref={detailRef}
            key={(editor ? "editor:" : "") + (selected?.id ?? "roadmap")}
          >
            {editor ? (
              <StructureEditor
                key={road.revision}
                roadmap={road}
                onDone={() => setEditing(false)}
              />
            ) : selected ? (
              stepDetail
            ) : (
              <>
                {step && (
                  <p className="warning">
                    That step is not in the current plan. It may have been
                    retired.
                  </p>
                )}
                <p className="muted">
                  Choose a step to see its work, chats and notes.
                </p>
              </>
            )}
          </div>
        </div>
      ) : view === "chats" ? (
        <RoadmapChats road={road} chat={chat} />
      ) : (
        <div
          className="pane roadmap-view"
          role="region"
          aria-labelledby={viewHeading}
        >
          <h2 className="visually-hidden" id={viewHeading}>
            {shown.label}
          </h2>
          {view === "status"
            ? statusView
            : view === "questions"
              ? questionsView
              : view === "material"
                ? materialView
                : historyView}
        </div>
      )}
    </div>
  );
}
/**
 * A roadmap's status board: where each phase stands, the steps in progress with their agents, the
 * open steps next in outline order, what waits on the person, and the steps lately done.
 */
function StatusBoard({
  id,
  road,
  tree,
  ordered,
  workers,
  stepTitle,
}: {
  id: string;
  road: Roadmap;
  // The plan's steps under their parents, and in outline order; retired ones aside.
  tree: Tree;
  ordered: Step[];
  workers: RoadmapWorker[];
  stepTitle: (id: string) => string;
}) {
  // Agents propose as they work, so what waits on the person refreshes as their places do.
  const board = useData<Answer<"/api/v1/roadmaps/{identity}/board">>(
    `/roadmaps/${id}/board`,
    AGENTS_REFRESH,
  );
  const read = board.isPlaceholderData ? undefined : board.data;
  const phasesHeading = useId();
  const phases = tree.get(null) ?? [];
  // Each step's phase: the step at the top of the outline above it.
  const phaseOf = new Map<string, Step>();
  const place = (s: Step, phase: Step) => {
    phaseOf.set(s.id, phase);
    for (const kid of tree.get(s.id) ?? []) place(kid, phase);
  };
  for (const phase of phases) place(phase, phase);
  const leaves = ordered.filter((s) => !tree.has(s.id));
  const doing = leaves.filter((s) => s.status === "doing");
  const next = leaves.filter((s) => s.status === "open");
  const blocked = leaves.filter((s) => s.status === "blocked");
  const questions = (road.questions ?? []).filter(
    (q) => q.status === "open" && !q.archived,
  );
  const waiting = read?.waiting ?? [];
  const byId = new Map(ordered.map((s) => [s.id, s]));
  const done = (read?.recentlyDone ?? []).flatMap((d) => {
    const s = byId.get(d.id);
    return s ? [{ step: s, at: d.doneAt }] : [];
  });
  const needs = questions.length + blocked.length + waiting.length;
  const outline = viewAddress(id, "outline");
  const stepRow = (s: Step, aside?: React.ReactNode) => {
    const phase = phaseOf.get(s.id);
    return (
      <li className="line board-step" key={s.id}>
        <span className={"status-dot " + s.status} aria-hidden="true" />
        <span className="board-step-text">
          <a
            className="item-title"
            href={`#roadmaps/${id}/${s.id}`}
            title={s.title}
          >
            {s.title}
          </a>
          {phase && phase.id !== s.id && (
            <small className="muted" title={phase.title}>
              {phase.title}
            </small>
          )}
        </span>
        {aside}
      </li>
    );
  };
  const more = (rest: number, what: string, href: string) =>
    rest > 0 && (
      <p className="board-more">
        <a href={href}>
          {rest} more {what}
        </a>
      </p>
    );
  return (
    <div className="roadmap-board">
      <ErrorMessage error={board.error} />
      {/* A plan without phases has nothing here the progress bar above doesn't say. */}
      {phases.some((p) => tree.has(p.id)) && (
        <section className="board-phases">
          <h3 id={phasesHeading}>Phases</h3>
          <table className="phase-table" aria-labelledby={phasesHeading}>
            <thead>
              <tr>
                <th scope="col">
                  <span className="visually-hidden">Phase</span>
                </th>
                <th scope="col">
                  <span className="visually-hidden">Progress</span>
                </th>
                {COUNTED_STATUSES.map((status) => (
                  <th scope="col" key={status} title={statusLabel(status)}>
                    <span className={"status-dot " + status} aria-hidden="true" />
                    <span className="phase-label">{statusLabel(status)}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {phases.map((phase) => {
                // A phase counts its leaves; a parent's own status is derived from them.
                const under = leaves.filter((s) => phaseOf.get(s.id) === phase);
                const count = (status: Status) =>
                  under.filter((s) => s.status === status).length;
                return (
                  <tr key={phase.id}>
                    <th scope="row">
                      <a
                        className="item-title"
                        href={`#roadmaps/${id}/${phase.id}`}
                        title={phase.title}
                      >
                        {phase.title}
                      </a>
                    </th>
                    <td>
                      <StatusBar steps={under} />
                    </td>
                    {COUNTED_STATUSES.map((status) => {
                      const n = count(status);
                      return (
                        <td key={status} className={n ? undefined : "none"}>
                          {n}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
      <section>
        <h3>
          Now <span className="count">{doing.length}</span>
        </h3>
        {doing.length ? (
          <ul className="board-list">
            {doing
              .slice(0, BOARD_ROWS)
              .map((s) =>
                stepRow(
                  s,
                  <AgentChips
                    placed={workersOn(workers, new Set([s.id]))}
                    stepTitle={stepTitle}
                    here={s.id}
                  />,
                ),
              )}
          </ul>
        ) : (
          <p className="muted">No step is in progress.</p>
        )}
        {more(doing.length - BOARD_ROWS, "in progress", outline)}
      </section>
      <section>
        <h3>
          Next <span className="count">{next.length}</span>
        </h3>
        {next.length ? (
          <ul className="board-list">
            {next.slice(0, BOARD_ROWS).map((s) => stepRow(s))}
          </ul>
        ) : (
          <p className="muted">No step is open.</p>
        )}
        {more(next.length - BOARD_ROWS, "open", outline)}
      </section>
      <section>
        {/* Counted once the proposals are read, so the count doesn't jump or come up short. */}
        <h3>Needs you {read && <span className="count">{needs}</span>}</h3>
        {!needs && read && <p className="muted">Nothing waits on you here.</p>}
        {questions.length > 0 && (
          <>
            <h4>Open questions</h4>
            <ul className="board-list">
              {questions.slice(0, BOARD_ROWS).map((q) => (
                <li className="line" key={q.id}>
                  <a
                    className="item-title"
                    href={viewAddress(id, "questions")}
                    title={q.title}
                  >
                    {q.title}
                  </a>
                </li>
              ))}
            </ul>
            {more(
              questions.length - BOARD_ROWS,
              "open questions",
              viewAddress(id, "questions"),
            )}
          </>
        )}
        {blocked.length > 0 && (
          <>
            <h4>Blocked steps</h4>
            <ul className="board-list">
              {blocked.slice(0, BOARD_ROWS).map((s) => stepRow(s))}
            </ul>
            {more(blocked.length - BOARD_ROWS, "blocked", outline)}
          </>
        )}
        {waiting.length > 0 && (
          <>
            <h4>Proposals to review</h4>
            <ul className="board-list">
              {waiting.slice(0, BOARD_ROWS).map((p) => (
                <li className="line" key={p.id}>
                  <a
                    className="item-title"
                    href={"#review/" + p.id}
                    title={p.title}
                  >
                    {p.title}
                  </a>
                  <small className="muted">
                    {p.actorLabel} · {p.operationCount} change
                    {p.operationCount === 1 ? "" : "s"}
                  </small>
                </li>
              ))}
            </ul>
            {more(waiting.length - BOARD_ROWS, "to review", "#review")}
          </>
        )}
      </section>
      <section>
        <h3>Recently done</h3>
        {done.length ? (
          <ul className="board-list">
            {done.map(({ step, at }) =>
              stepRow(
                step,
                <small className="muted">
                  <time dateTime={at}>{new Date(at).toLocaleDateString()}</time>
                </small>,
              ),
            )}
          </ul>
        ) : (
          read && <p className="muted">No step marked done yet.</p>
        )}
      </section>
    </div>
  );
}
/**
 * Adds a work item to a step, or attaches existing work to it, from the step's own page: no step to
 * find in a list. Attaching work that sits on another step moves it here.
 */
function StepWorkForms({
  roadmapId,
  stepId,
  onStep,
  disabled,
}: {
  roadmapId: string;
  stepId: string;
  // The work items already on this step, which attaching does not offer.
  onStep: Set<string>;
  disabled: boolean;
}) {
  const createItem = useCommand(commands.createItem);
  const attach = useWrites();
  const [title, setTitle] = useState("");
  const [query, setQuery] = useState("");
  const [pick, setPick] = useState("");
  const work = useData<Answer<"/api/v1/work-items">>(
    `/work-items?view=all&archived=false&limit=100&q=${encodeURIComponent(query)}`,
  );
  return (
    <>
      <form
        className="quick-add"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!title.trim() || disabled) return;
          await createItem
            .mutateAsync({
              title,
              description: "",
              roadmapId,
              stepId,
              worked: false,
            })
            .then(() => setTitle(""))
            .catch(() => undefined);
        }}
      >
        <input
          aria-label="New work item on this step"
          placeholder="Add a work item to this step…"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={500}
          disabled={disabled}
        />
        <button
          type="submit"
          className="primary"
          disabled={disabled || createItem.isPending || !title.trim()}
        >
          Add work item
        </button>
      </form>
      <form
        className="inline plan-picker"
        onSubmit={async (e) => {
          e.preventDefault();
          const chosen = work.data?.items.find((i) => i.id === pick);
          if (!chosen || disabled) return;
          await attach
            .mutateAsync(() =>
              commands.updateItem(chosen.id, {
                revision: chosen.revision,
                roadmapId,
                stepId,
              }),
            )
            .then(() => setPick(""))
            .catch(() => undefined);
        }}
      >
        <input
          aria-label="Find existing work"
          placeholder="Find existing work…"
          value={query}
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          disabled={disabled}
          onChange={(e) => {
            setQuery(e.target.value);
            setPick("");
          }}
        />
        <select
          aria-label="Choose existing work"
          value={pick}
          disabled={disabled}
          onChange={(e) => setPick(e.target.value)}
        >
          <option value="">Attach existing work to this step…</option>
          {work.data?.items
            .filter((i) => !onStep.has(i.id))
            .map((i) => (
              <option value={i.id} key={i.id}>
                {i.title}
              </option>
            ))}
        </select>
        <button type="submit" disabled={disabled || !pick || attach.isPending}>
          Attach work item
        </button>
      </form>
      <ErrorMessage error={createItem.error || attach.error || work.error} />
    </>
  );
}
/**
 * What a step's work produces, the goal it is done for: shown first on the step's page, and larger
 * than anything else under its title. A step without one says so just as prominently.
 */
export function StepOutcome({ outcome }: { outcome: string }) {
  return (
    <section
      className={"step-outcome" + (outcome ? "" : " missing")}
      aria-labelledby="step-outcome-heading"
    >
      <h3 id="step-outcome-heading">Outcome</h3>
      {outcome ? (
        <p className="step-outcome-text">{outcome}</p>
      ) : (
        <p className="step-outcome-text">
          No outcome yet. What does this step produce? Add it with Edit step.
        </p>
      )}
    </section>
  );
}
/**
 * One step's title, outcome and description, saved alone with the step's own revision
 * (PATCH /steps/ID), so the rest of the plan, and other agents' changes to it, stay as they are.
 */
function StepTextForm({ step, onDone }: { step: Step; onDone: () => void }) {
  const action = useWrites();
  const client = useQueryClient();
  const [title, setTitle] = useState(step.title);
  const [outcome, setOutcome] = useState(step.outcome);
  const [description, setDescription] = useState(step.description);
  // The text this edit started from. Progress set meanwhile changes the step's revision but not
  // its text, so only new text elsewhere asks before it is replaced.
  const [opened] = useState({
    title: step.title,
    outcome: step.outcome,
    description: step.description,
  });
  const replaced =
    step.title !== opened.title ||
    step.outcome !== opened.outcome ||
    step.description !== opened.description;
  const changed =
    title.trim() !== step.title ||
    outcome !== step.outcome ||
    description !== step.description;
  return (
    <form
      className="roadmap-record-form"
      aria-label={"Edit step " + step.title}
      onSubmit={async (e) => {
        e.preventDefault();
        if (!title.trim() || !changed) return;
        await action
          .mutateAsync(() =>
            commands.updateStep(step.id, {
              revision: step.revision,
              title: title.trim(),
              outcome,
              description,
            }),
          )
          .then(onDone)
          .catch((error) => {
            // The step changed elsewhere first: load it, keeping what was typed here.
            if (error instanceof ApiError && error.status === 409)
              void refreshAfterWrite(client);
          });
      }}
    >
      <label>
        Step title
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={1000}
          required
          autoFocus
        />
      </label>
      <label className="step-outcome-field">
        Outcome: what this step produces
        <textarea
          value={outcome}
          onChange={(e) => setOutcome(e.target.value)}
          placeholder="The result that says this step is done"
          rows={2}
        />
      </label>
      <label>
        Description (Markdown)
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      {replaced && (
        <div className="warning" role="alert">
          <p>
            This step's text changed since you opened it. Saving replaces it
            with yours. It now reads:
          </p>
          <strong>{step.title}</strong>
          {step.outcome && <p>Outcome: {step.outcome}</p>}
          {step.description && <Markdown text={step.description} />}
        </div>
      )}
      <p className="muted">
        Saves this step alone: the rest of the roadmap stays as it is.
      </p>
      <div className="toolbar">
        <button
          type="submit"
          className="primary"
          disabled={action.isPending || !title.trim() || !changed}
        >
          Save step
        </button>
        <button type="button" className="quiet" onClick={onDone}>
          Cancel
        </button>
      </div>
      <ErrorMessage error={action.error} />
    </form>
  );
}
// The roadmap's text as a structure draft found it, so a rebase can tell the person's edits from
// changes saved elsewhere since, such as an agent's step.update.
type DraftBase = {
  title: string;
  overview: string;
  steps: Record<
    string,
    Pick<PlanStep, "title" | "description" | "parentId"> & {
      // Missing on drafts saved before steps had outcomes.
      outcome?: string;
    }
  >;
};
function draftBase(roadmap: Roadmap): DraftBase {
  return {
    title: roadmap.title,
    overview: roadmap.overview,
    steps: Object.fromEntries(
      roadmap.steps
        .filter((s) => !s.retired)
        .map((s) => [
          s.id,
          {
            title: s.title,
            outcome: s.outcome,
            description: s.description,
            parentId: s.parentId,
          },
        ]),
    ),
  };
}
// A draft saved before steps had outcomes holds none for its steps or its base: none counts as empty.
const sameOutcome = (step: { outcome?: string }, was: { outcome?: string }) =>
  (step.outcome ?? "") === (was.outcome ?? "");
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
    steps: PlanStep[];
    revision: number;
    // Active step ids when the draft started; missing on drafts saved before it was recorded.
    baseStepIds?: string[];
    // The text it started from; missing on drafts saved before it was recorded.
    base?: DraftBase;
    note?: string;
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
  const [steps, setSteps] = useState<PlanStep[]>(
    () => draft?.steps ?? active().map((s) => ({ ...s })),
  );
  const [revision, setRevision] = useState(draft?.revision ?? roadmap.revision);
  const [baseStepIds, setBaseStepIds] = useState<string[] | null>(() =>
    draft ? (draft.baseStepIds ?? null) : active().map((s) => s.id),
  );
  const [base, setBase] = useState<DraftBase | null>(() =>
    draft ? (draft.base ?? null) : draftBase(roadmap),
  );
  const [recovered, setRecovered] = useState(draft !== null);
  const serverOnly = active().filter((s) => !steps.some((d) => d.id === s.id));
  const [keep, setKeep] = useState(() => new Set(serverOnly.map((s) => s.id)));
  const [note, setNote] = useState(draft?.note ?? "");
  const [storageWarning, setStorageWarning] = useState(!storedResult.ok);
  const action = useWrites();
  const client = useQueryClient();
  const stale = revision !== roadmap.revision;
  function persist(
    nextTitle: string,
    nextOverview: string,
    nextSteps: PlanStep[],
    baseRevision = revision,
    baseIds = baseStepIds,
    nextNote = note,
    nextBase = base,
  ) {
    const persisted = localStore.write(
      key,
      JSON.stringify({
        title: nextTitle,
        overview: nextOverview,
        steps: nextSteps,
        revision: baseRevision,
        ...(baseIds ? { baseStepIds: baseIds } : {}),
        ...(nextBase ? { base: nextBase } : {}),
        ...(nextNote ? { note: nextNote } : {}),
      }),
    );
    if (!persisted) setStorageWarning(true);
  }
  function update(id: string, fields: Partial<PlanStep>) {
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
    setBase(draftBase(roadmap));
    setNote("");
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
    // What the draft left as it found it takes its current value, so a change saved elsewhere
    // since, such as an agent's step.update, survives the save. The person's edits stay theirs.
    const current = new Map(active().map((s) => [s.id, s]));
    const untouched = (s: PlanStep) => {
      const was = base?.steps[s.id];
      return (
        !!was &&
        s.title === was.title &&
        sameOutcome(s, was) &&
        s.description === was.description &&
        s.parentId === was.parentId
      );
    };
    // A step retired elsewhere since stays retired, unless the draft changed it.
    const kept = steps.filter((s) => current.has(s.id) || !untouched(s));
    const merged = kept.map((s) => {
      const was = base?.steps[s.id];
      const now = current.get(s.id);
      if (!was || !now) return s;
      return {
        ...s,
        title: s.title === was.title ? now.title : s.title,
        outcome: sameOutcome(s, was) ? now.outcome : s.outcome,
        description:
          s.description === was.description ? now.description : s.description,
        parentId: s.parentId === was.parentId ? now.parentId : s.parentId,
      };
    });
    const nextTitle = base && title === base.title ? roadmap.title : title;
    const nextOverview =
      base && overview === base.overview ? roadmap.overview : overview;
    const ids = new Set([...merged, ...added].map((s) => s.id));
    const next = [...merged, ...added].map((s, index) => ({
      ...s,
      parentId: s.parentId && ids.has(s.parentId) ? s.parentId : null,
      position: index,
    }));
    const baseIds = active().map((s) => s.id);
    const nextBase = draftBase(roadmap);
    setTitle(nextTitle);
    setOverview(nextOverview);
    setSteps(next);
    setRevision(roadmap.revision);
    setBaseStepIds(baseIds);
    setBase(nextBase);
    setRecovered(false);
    persist(
      nextTitle,
      nextOverview,
      next,
      roadmap.revision,
      baseIds,
      note,
      nextBase,
    );
  }
  return (
    <form
      className="structure-editor"
      onSubmit={async (e) => {
        e.preventDefault();
        await action
          .mutateAsync(() =>
            commands.reviseRoadmap(roadmap.id, {
              revision,
              title,
              overview,
              steps: steps.map((s, index) => ({
                id: s.id,
                title: s.title,
                // Left out when a draft saved before steps had outcomes has none: the step keeps its own.
                outcome: s.outcome,
                description: s.description,
                parentId: s.parentId,
                position: index,
              })),
              note,
            }),
          )
          .then(() => {
            localStore.remove(key);
            onDone();
          })
          .catch((error) => {
            // The roadmap moved past this draft's base. Refetching it remounts the
            // editor from the stored draft, which then offers a rebase.
            if (error instanceof ApiError && error.status === 409)
              void refreshAfterWrite(client);
          });
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
            at revision {roadmap.revision}, so saving it would be rejected.{" "}
            {base
              ? "Rebase moves your edits onto the current revision: a title, outcome, description or parent you didn't change takes its current one, and a step retired since stays retired unless you changed it. Discard drops your edits."
              : "Rebase keeps your draft's version of every step on the current revision; discard drops your edits."}
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
            className="step-outcome-input"
            aria-label={"Step " + (i + 1) + " outcome"}
            placeholder="Outcome: what this step produces"
            rows={2}
            value={s.outcome ?? ""}
            onChange={(e) => update(s.id, { outcome: e.target.value })}
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
                let ancestor: PlanStep | undefined = p;
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
              outcome: "",
              description: "",
              parentId: null,
              position: steps.length,
              status: "open",
              revision: 0,
              retired: false,
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
          <input
            value={note}
            onChange={(e) => {
              setNote(e.target.value);
              persist(
                title,
                overview,
                steps,
                revision,
                baseStepIds,
                e.target.value,
              );
            }}
          />
        </label>
        <ErrorMessage error={action.error} />
        <button className="primary" disabled={action.isPending || stale}>
          Save new version
        </button>
      </div>
    </form>
  );
}
