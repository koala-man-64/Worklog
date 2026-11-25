import { useEffect, useId, useRef, useState } from "react";
import { commands } from "./commands";
import { useData, useWrites } from "./hooks";
import { RepositoryPicker } from "./RepositoryControls";
import { Empty, ErrorMessage } from "./ui/Messages";
import { STATUSES, StatusSelect } from "./ui/Status";
import type {
  Answer,
  DispatchRepoLane,
  DispatchStepCard,
  DispatchUnassignedStepCard,
  Repository,
  RoadmapDispatch,
  WaveDetail,
  WaveRepin,
  WaveTierRepo,
} from "./apiTypes";

const DISPATCH_REFRESH = 30_000;

export const WORK_CLASS_OPTIONS = [
  { value: "", label: "Unclassified" },
  { value: "stabilization", label: "Stabilization" },
  { value: "critical_path", label: "Critical path" },
  { value: "standard", label: "Standard" },
  { value: "refactor", label: "Refactor" },
  { value: "gate", label: "Readiness gate" },
] as const;

export type WorkClassValue = (typeof WORK_CLASS_OPTIONS)[number]["value"];

export function workClassLabel(value: string): string {
  return (
    WORK_CLASS_OPTIONS.find((opt) => opt.value === value)?.label ??
    (value || "Unclassified")
  );
}

const WAVE_STATUSES = [
  { value: "planning", label: "Planning" },
  { value: "active", label: "Active" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
] as const;

const FREEZE_STATUSES = [
  { value: "open", label: "Open" },
  { value: "batching", label: "Batching" },
  { value: "frozen", label: "Frozen" },
] as const;

type SplitChildDraft = {
  key: string;
  title: string;
  outcome: string;
  repositoryId: string;
  workClass: WorkClassValue;
};

/**
 * Native modal `<dialog>` for splitting a multi-repo leaf step into single-repo child steps
 * while keeping the parent's phase/milestone position and inheriting active/planning wave membership.
 */
export function StepSplitDialog({
  step,
  repositories,
  onClose,
}: {
  step: {
    id: string;
    title: string;
    revision: number;
    workClass: string;
    repositoryIds: string[];
  };
  repositories: Repository[];
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const action = useWrites();
  const repoNames = new Map(repositories.map((r) => [r.id, r.name]));
  const initialWorkClass = (
    WORK_CLASS_OPTIONS.some((o) => o.value === step.workClass)
      ? step.workClass
      : ""
  ) as WorkClassValue;
  const [children, setChildren] = useState<SplitChildDraft[]>(() => {
    const seedRepos =
      step.repositoryIds.length >= 2
        ? step.repositoryIds
        : [step.repositoryIds[0] ?? "", ""];
    return seedRepos.map((rid, idx) => {
      const rname = rid ? repoNames.get(rid) : undefined;
      return {
        key: `child-${idx}`,
        title: rname ? `${step.title} (${rname})` : `${step.title} — Part ${idx + 1}`,
        outcome: "",
        repositoryId: rid,
        workClass: initialWorkClass,
      };
    });
  });
  const [clearParentRepos, setClearParentRepos] = useState(true);
  const [inheritWaves, setInheritWaves] = useState(true);
  const [rehomeIndex, setRehomeIndex] = useState<string>("");

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (typeof el.showModal === "function") {
      if (!el.open) el.showModal();
    } else {
      el.setAttribute("open", "");
    }
    const handleClose = () => {
      el.returnValue = "";
      onClose();
    };
    el.addEventListener("close", handleClose);
    return () => {
      el.removeEventListener("close", handleClose);
      if (el.open && typeof el.close === "function") el.close();
    };
  }, [onClose]);

  const valid =
    children.length >= 2 && children.every((child) => child.title.trim() !== "");

  return (
    <dialog
      ref={dialogRef}
      className="exec-dialog"
      aria-labelledby={titleId}
    >
      <div className="exec-dialog-head">
        <h1 id={titleId}>Split step by repository</h1>
        <button
          type="button"
          className="quiet"
          aria-label="Close split dialog"
          onClick={() => {
            if (dialogRef.current?.open && typeof dialogRef.current.close === "function") {
              dialogRef.current.close();
            } else {
              onClose();
            }
          }}
        >
          Close
        </button>
      </div>
      <p className="muted">
        Parent step: <strong>{step.title}</strong>. Child steps stay under this
        parent in the Phase Outline and appear in their repository lanes.
      </p>
      <form
        className="roadmap-record-form"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!valid || action.isPending) return;
          const rehome =
            rehomeIndex === "" ? null : Number.parseInt(rehomeIndex, 10);
          await action
            .mutateAsync(() =>
              commands.splitStep(step.id, {
                revision: step.revision,
                clearParentRepositories: clearParentRepos,
                inheritWaves,
                rehomeToChildIndex: Number.isNaN(rehome as number)
                  ? null
                  : rehome,
                children: children.map((child) => ({
                  title: child.title.trim(),
                  outcome: child.outcome,
                  description: "",
                  workClass: child.workClass,
                  repositoryIds: child.repositoryId ? [child.repositoryId] : [],
                  status: "open",
                })),
              }),
            )
            .then(() => {
              if (dialogRef.current?.open && typeof dialogRef.current.close === "function") {
                dialogRef.current.close("saved");
              } else {
                onClose();
              }
            })
            .catch(() => undefined);
        }}
      >
        {children.map((child, idx) => (
          <fieldset className="exec-split-child" key={child.key}>
            <legend>Child step {idx + 1}</legend>
            <label>
              Title
              <input
                autoFocus={idx === 0}
                required
                maxLength={1000}
                value={child.title}
                onChange={(e) =>
                  setChildren((prev) =>
                    prev.map((c, i) =>
                      i === idx ? { ...c, title: e.target.value } : c,
                    ),
                  )
                }
              />
            </label>
            <div className="exec-inline-fields">
              <label>
                Repository
                <select
                  aria-label={`Child ${idx + 1} repository`}
                  value={child.repositoryId}
                  onChange={(e) =>
                    setChildren((prev) =>
                      prev.map((c, i) =>
                        i === idx ? { ...c, repositoryId: e.target.value } : c,
                      ),
                    )
                  }
                >
                  <option value="">No repository</option>
                  {repositories.map((repo) => (
                    <option key={repo.id} value={repo.id}>
                      {repo.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Work class
                <select
                  aria-label={`Child ${idx + 1} work class`}
                  value={child.workClass}
                  onChange={(e) =>
                    setChildren((prev) =>
                      prev.map((c, i) =>
                        i === idx
                          ? { ...c, workClass: e.target.value as WorkClassValue }
                          : c,
                      ),
                    )
                  }
                >
                  {WORK_CLASS_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              Outcome (optional)
              <input
                value={child.outcome}
                maxLength={2000}
                placeholder="What this repo-scoped child produces"
                onChange={(e) =>
                  setChildren((prev) =>
                    prev.map((c, i) =>
                      i === idx ? { ...c, outcome: e.target.value } : c,
                    ),
                  )
                }
              />
            </label>
            {children.length > 2 && (
              <button
                type="button"
                className="quiet"
                onClick={() =>
                  setChildren((prev) => prev.filter((_, i) => i !== idx))
                }
              >
                Remove child
              </button>
            )}
          </fieldset>
        ))}
        <div className="toolbar">
          <button
            type="button"
            onClick={() =>
              setChildren((prev) => [
                ...prev,
                {
                  key: `child-${Date.now()}-${prev.length}`,
                  title: `${step.title} — Part ${prev.length + 1}`,
                  outcome: "",
                  repositoryId: "",
                  workClass: initialWorkClass,
                },
              ])
            }
          >
            Add child step
          </button>
        </div>
        <div className="exec-split-options">
          <label>
            <input
              type="checkbox"
              checked={clearParentRepos}
              onChange={(e) => setClearParentRepos(e.target.checked)}
            />{" "}
            Clear parent step repository links
          </label>
          <label>
            <input
              type="checkbox"
              checked={inheritWaves}
              onChange={(e) => setInheritWaves(e.target.checked)}
            />{" "}
            Move active/planning wave membership to children
          </label>
          <label>
            Move linked work items &amp; active participations to
            <select
              value={rehomeIndex}
              onChange={(e) => setRehomeIndex(e.target.value)}
            >
              <option value="">Keep on parent step</option>
              {children.map((child, idx) => (
                <option key={child.key} value={String(idx)}>
                  Child {idx + 1}: {child.title || "Untitled"}
                </option>
              ))}
            </select>
          </label>
        </div>
        <ErrorMessage error={action.error} />
        <div className="toolbar">
          <button
            type="submit"
            className="primary"
            disabled={!valid || action.isPending}
          >
            Create {children.length} child steps
          </button>
          <button
            type="button"
            className="quiet"
            onClick={() => {
              if (dialogRef.current?.open && typeof dialogRef.current.close === "function") {
                dialogRef.current.close();
              } else {
                onClose();
              }
            }}
          >
            Cancel
          </button>
        </div>
      </form>
    </dialog>
  );
}

/**
 * Repo + Wave Execution / Dispatch view inside a roadmap (`#roadmaps/{id}/execution`).
 * Keeps phases as the planning hierarchy while organizing daily execution by repository lane,
 * WIP cap, work-class sequencing, and synchronized dependency-tier waves.
 */
export function RoadmapExecution({
  roadmapId,
  archived,
}: {
  roadmapId: string;
  archived: boolean;
}) {
  const [selectedWaveId, setSelectedWaveId] = useState("");
  const [repoFilter, setRepoFilter] = useState("");
  const [includeDone, setIncludeDone] = useState(false);
  const [creatingWave, setCreatingWave] = useState(false);
  const [splittingStep, setSplittingStep] = useState<DispatchStepCard | null>(
    null,
  );

  const params = new URLSearchParams();
  if (selectedWaveId) params.set("waveId", selectedWaveId);
  if (repoFilter) params.set("repositoryId", repoFilter);
  if (includeDone) params.set("includeDone", "true");
  const queryStr = params.toString();

  const dispatchQuery = useData<RoadmapDispatch>(
    `/roadmaps/${roadmapId}/dispatch${queryStr ? "?" + queryStr : ""}`,
    DISPATCH_REFRESH,
  );
  const reposQuery = useData<Answer<"/api/v1/repositories">>(
    "/repositories?archived=false&limit=200",
  );
  const action = useWrites();

  const data = dispatchQuery.data;
  const repositories = reposQuery.data?.items ?? [];
  const repoMap = new Map(repositories.map((r) => [r.id, r]));

  if (!data) {
    return (
      <div className="pane roadmap-view">
        <ErrorMessage error={dispatchQuery.error || reposQuery.error} />
        {dispatchQuery.isPending && <p>Loading execution dispatch…</p>}
      </div>
    );
  }

  const { summary, waves, activeWave, repoLanes, multiRepoLeaves, unassignedLeaves, gates } = data;

  return (
    <div className="pane roadmap-view exec-view">
      <ErrorMessage error={dispatchQuery.error || action.error} />

      <dl className="exec-summary" aria-label="Execution summary">
        <div>
          <dt>Leaves</dt>
          <dd>
            {summary.doneLeaves}/{summary.totalLeaves}
          </dd>
        </div>
        <div>
          <dt>Doing</dt>
          <dd>{summary.doingLeaves}</dd>
        </div>
        <div>
          <dt>Blocked</dt>
          <dd>{summary.blockedLeaves}</dd>
        </div>
        <div className={summary.overCapRepos > 0 ? "is-warning" : undefined}>
          <dt>Over-cap repos</dt>
          <dd>{summary.overCapRepos}</dd>
        </div>
        <div
          className={summary.competingDoingCount > 0 ? "is-warning" : undefined}
        >
          <dt>Competing WIP</dt>
          <dd>{summary.competingDoingCount}</dd>
        </div>
        <div
          className={summary.multiRepoLeafCount > 0 ? "is-warning" : undefined}
        >
          <dt>Multi-repo leaves</dt>
          <dd>{summary.multiRepoLeafCount}</dd>
        </div>
        <div>
          <dt>Unassigned</dt>
          <dd>{summary.unassignedLeafCount}</dd>
        </div>
        <div
          className={summary.pendingRepinCount > 0 ? "is-warning" : undefined}
        >
          <dt>Pending re-pins</dt>
          <dd>{summary.pendingRepinCount}</dd>
        </div>
      </dl>

      <div className="toolbar exec-toolbar">
        <label>
          Wave
          <select
            aria-label="Active wave"
            value={selectedWaveId || activeWave?.id || ""}
            onChange={(e) => setSelectedWaveId(e.target.value)}
          >
            {waves.length === 0 ? (
              <option value="">No waves yet</option>
            ) : (
              waves.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} ({w.status})
                </option>
              ))
            )}
          </select>
        </label>
        <label>
          Repository
          <select
            aria-label="Filter execution by repository"
            value={repoFilter}
            onChange={(e) => setRepoFilter(e.target.value)}
          >
            <option value="">All repositories</option>
            {repositories.map((repo) => (
              <option key={repo.id} value={repo.id}>
                {repo.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={includeDone}
            onChange={(e) => setIncludeDone(e.target.checked)}
          />{" "}
          Show done steps
        </label>
        {!archived && (
          <button
            type="button"
            className="primary exec-push-end"
            onClick={() => setCreatingWave((open) => !open)}
          >
            {creatingWave ? "Close wave form" : "New wave"}
          </button>
        )}
      </div>

      {creatingWave && !archived && (
        <NewWaveForm
          roadmapId={roadmapId}
          repositories={repositories}
          onCreated={(wave) => {
            setSelectedWaveId(wave.id);
            setCreatingWave(false);
          }}
          onCancel={() => setCreatingWave(false)}
        />
      )}

      {activeWave ? (
        <WavePanel
          wave={activeWave}
          archived={archived}
          repositories={repositories}
        />
      ) : (
        !creatingWave && (
          <section className="exec-card">
            <h3>Synchronized waves</h3>
            <Empty>
              No wave created for this roadmap yet. Create a wave to batch
              upstream contract changes, freeze tiers, and track consumer
              re-pins.
            </Empty>
          </section>
        )
      )}

      <section aria-label="Repository execution lanes">
        <div className="section-heading">
          <h3>
            Repository lanes <span className="count">{repoLanes.length}</span>
          </h3>
        </div>
        {repoLanes.length === 0 ? (
          <Empty>
            No leaf steps are linked to repositories yet. Assign repositories
            under Unassigned leaves below or on any step in the Outline.
          </Empty>
        ) : (
          <div className="exec-lanes">
            {repoLanes.map((lane) => (
              <RepoLaneCard
                key={lane.repositoryId}
                roadmapId={roadmapId}
                lane={lane}
                activeWave={activeWave}
                archived={archived}
                includeDone={includeDone}
                onSplit={setSplittingStep}
              />
            ))}
          </div>
        )}
      </section>

      {multiRepoLeaves.length > 0 && (
        <section className="exec-card" aria-label="Multi-repo leaves">
          <h3>
            Multi-repo leaves to split{" "}
            <span className="count">{multiRepoLeaves.length}</span>
          </h3>
          <p className="muted">
            Implementation leaves linked to multiple repositories cause
            cross-repo contention. Split them into single-repo child steps, or
            mark cross-repo verification milestones as Readiness gates.
          </p>
          <ul className="exec-step-list">
            {multiRepoLeaves.map((st) => (
              <li className="exec-step-item" key={st.id}>
                <div className="exec-step-main">
                  <span className={"status-dot " + st.status} aria-hidden="true" />
                  <div className="exec-step-text">
                    <a
                      className="item-title"
                      href={`#roadmaps/${roadmapId}/${st.id}`}
                    >
                      {st.title}
                    </a>
                    {st.phasePath.length > 0 && (
                      <small className="muted">
                        {st.phasePath.map((p) => p.title).join(" › ")}
                      </small>
                    )}
                    <div className="exec-pills">
                      {st.repositoryIds.map((rid) => (
                        <a
                          key={rid}
                          className="exec-pill"
                          href={"#repositories/" + rid}
                        >
                          {repoMap.get(rid)?.name ?? rid}
                        </a>
                      ))}
                    </div>
                  </div>
                </div>
                {!archived && (
                  <div className="toolbar">
                    <button
                      type="button"
                      className="primary"
                      onClick={() => setSplittingStep(st)}
                    >
                      Split by repo
                    </button>
                    <button
                      type="button"
                      className="quiet"
                      disabled={action.isPending}
                      onClick={() =>
                        action.mutate(() =>
                          commands.updateStep(st.id, {
                            revision: st.revision,
                            workClass: "gate",
                          }),
                        )
                      }
                    >
                      Mark as gate
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {unassignedLeaves.length > 0 && (
        <section className="exec-card" aria-label="Unassigned leaves">
          <h3>
            Unassigned leaves{" "}
            <span className="count">{unassignedLeaves.length}</span>
          </h3>
          <p className="muted">
            Link each leaf step to its repository so it enters a repository lane
            and respects WIP caps.
          </p>
          <ul className="exec-step-list">
            {unassignedLeaves.map((st) => (
              <UnassignedLeafRow
                key={st.id}
                roadmapId={roadmapId}
                step={st}
                repoMap={repoMap}
                archived={archived}
              />
            ))}
          </ul>
        </section>
      )}

      {(gates.gateSteps.length > 0 || gates.phases.length > 0) && (
        <section className="exec-card" aria-label="Readiness gates and phases">
          <h3>Readiness gates &amp; phase progress</h3>
          {gates.gateSteps.length > 0 && (
            <div className="exec-gates-list">
              <h4>Readiness gates</h4>
              <ul className="exec-step-list">
                {gates.gateSteps.map((g) => (
                  <li className="exec-step-item" key={g.id}>
                    <div className="exec-step-main">
                      <span
                        className={"status-dot " + g.status}
                        aria-hidden="true"
                      />
                      <div className="exec-step-text">
                        <a
                          className="item-title"
                          href={`#roadmaps/${roadmapId}/${g.id}`}
                        >
                          {g.title}
                        </a>
                        {g.phasePath.length > 0 && (
                          <small className="muted">
                            {g.phasePath.map((p) => p.title).join(" › ")}
                          </small>
                        )}
                      </div>
                    </div>
                    <span className="exec-pill">Gate · {g.status}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {gates.phases.length > 0 && (
            <div className="exec-phases-strip">
              {gates.phases.map((phase) => (
                <a
                  key={phase.id}
                  className="exec-phase-pill"
                  href={`#roadmaps/${roadmapId}/${phase.id}`}
                >
                  <span
                    className={"status-dot " + phase.status}
                    aria-hidden="true"
                  />
                  <strong>{phase.title}</strong>
                  <small>
                    {phase.doneLeaves}/{phase.totalLeaves} done
                    {phase.doingLeaves > 0 ? ` · ${phase.doingLeaves} doing` : ""}
                    {phase.blockedLeaves > 0
                      ? ` · ${phase.blockedLeaves} blocked`
                      : ""}
                  </small>
                </a>
              ))}
            </div>
          )}
        </section>
      )}

      {splittingStep && (
        <StepSplitDialog
          step={splittingStep}
          repositories={repositories}
          onClose={() => setSplittingStep(null)}
        />
      )}
    </div>
  );
}

function NewWaveForm({
  roadmapId,
  repositories,
  onCreated,
  onCancel,
}: {
  roadmapId: string;
  repositories: Repository[];
  onCreated: (wave: WaveDetail) => void;
  onCancel: () => void;
}) {
  const action = useWrites();
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [selectedRepos, setSelectedRepos] = useState<string[]>([]);
  const [openDiscussion, setOpenDiscussion] = useState(false);

  return (
    <form
      className="exec-card roadmap-record-form"
      aria-label="Create wave"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!name.trim() || action.isPending) return;
        await action
          .mutateAsync(() =>
            commands.createWave(roadmapId, {
              name: name.trim(),
              summary,
              stepIds: [],
              repositories: selectedRepos.map((rid) => {
                const repo = repositories.find((r) => r.id === rid);
                return { repositoryId: rid, tier: repo?.tier ?? null };
              }),
              openDiscussion,
            }),
          )
          .then((created) => onCreated(created as WaveDetail))
          .catch(() => undefined);
      }}
    >
      <h3>New synchronized wave</h3>
      <label>
        Wave name
        <input
          required
          maxLength={200}
          placeholder="e.g. Wave 1 — Contracts & Runtime Freeze"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label>
        Summary
        <textarea
          rows={2}
          maxLength={10000}
          placeholder="Scope, freeze criteria, and target readiness gate"
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
        />
      </label>
      <RepositoryPicker
        label="Wave repositories"
        value={selectedRepos}
        onChange={setSelectedRepos}
      />
      <label>
        <input
          type="checkbox"
          checked={openDiscussion}
          onChange={(e) => setOpenDiscussion(e.target.checked)}
        />{" "}
        Open a dedicated coordination discussion for this wave
      </label>
      <ErrorMessage error={action.error} />
      <div className="toolbar">
        <button
          type="submit"
          className="primary"
          disabled={!name.trim() || action.isPending}
        >
          Create wave
        </button>
        <button type="button" className="quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function WavePanel({
  wave,
  archived,
  repositories,
}: {
  wave: WaveDetail;
  archived: boolean;
  repositories: Repository[];
}) {
  const action = useWrites();
  const repinHeadingId = useId();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(wave.name);
  const [summary, setSummary] = useState(wave.summary);
  const [waveRepoIds, setWaveRepoIds] = useState(
    wave.repositories.map((r) => r.repositoryId),
  );
  const [freezeRepoId, setFreezeRepoId] = useState<string | null>(null);
  const editable =
    !archived && !wave.archived && wave.status !== "completed" && wave.status !== "cancelled";

  return (
    <section className="exec-card exec-wave-panel" aria-label={`Wave ${wave.name}`}>
      <div className="exec-wave-head">
        <div>
          <h3>
            {wave.name}{" "}
            <span className={`exec-pill wave-${wave.status}`}>{wave.status}</span>
          </h3>
          {wave.summary && <p className="muted">{wave.summary}</p>}
          <small className="muted">
            {wave.doneStepCount}/{wave.stepCount} steps done ·{" "}
            {wave.frozenRepoCount} frozen · {wave.pendingRepinCount} pending
            re-pin{wave.pendingRepinCount === 1 ? "" : "s"}
            {wave.discussionId && (
              <>
                {" "}
                · <a href={`#discussions/${wave.discussionId}`}>Wave thread</a>
              </>
            )}
          </small>
        </div>
        {!archived && (
          <div className="toolbar">
            <label>
              <span className="visually-hidden">Wave status</span>
              <select
                aria-label="Wave status"
                value={wave.status}
                disabled={action.isPending}
                onChange={(e) =>
                  action.mutate(() =>
                    commands.setWaveStatus(wave.id, {
                      revision: wave.revision,
                      status: e.target.value as WaveDetail["status"],
                    }),
                  )
                }
              >
                {WAVE_STATUSES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="quiet"
              onClick={() => {
                setName(wave.name);
                setSummary(wave.summary);
                setWaveRepoIds(wave.repositories.map((r) => r.repositoryId));
                setEditing((v) => !v);
              }}
            >
              {editing ? "Close edit" : "Edit wave"}
            </button>
          </div>
        )}
      </div>

      {editing && !archived && (
        <form
          className="roadmap-record-form exec-subform"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!name.trim() || action.isPending) return;
            await action
              .mutateAsync(() =>
                commands.updateWave(wave.id, {
                  revision: wave.revision,
                  name: name.trim(),
                  summary,
                  repositories: waveRepoIds.map((rid) => {
                    const existing = wave.repositories.find(
                      (r) => r.repositoryId === rid,
                    );
                    const catalog = repositories.find((r) => r.id === rid);
                    return {
                      repositoryId: rid,
                      tier: existing?.tier ?? catalog?.tier ?? null,
                    };
                  }),
                }),
              )
              .then(() => setEditing(false))
              .catch(() => undefined);
          }}
        >
          <label>
            Wave name
            <input
              required
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            Summary
            <textarea
              rows={2}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
            />
          </label>
          <RepositoryPicker
            label="Wave repositories"
            value={waveRepoIds}
            onChange={setWaveRepoIds}
          />
          <div className="toolbar">
            <button type="submit" className="primary" disabled={action.isPending}>
              Save wave
            </button>
            <button
              type="button"
              className="quiet"
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <ErrorMessage error={action.error} />

      {wave.tierPipeline.length > 0 && (
        <div className="exec-tier-pipeline" aria-label="Dependency tier pipeline">
          {wave.tierPipeline.map((group) => (
            <div className="exec-tier-col" key={String(group.tier)}>
              <h4>{group.tier !== null ? `Tier ${group.tier}` : "Untiered"}</h4>
              <div className="exec-tier-repos">
                {group.repositories.map((repo) => (
                  <TierRepoTile
                    key={repo.repositoryId}
                    wave={wave}
                    repo={repo}
                    editable={editable}
                    editingFreeze={freezeRepoId === repo.repositoryId}
                    onToggleFreeze={() =>
                      setFreezeRepoId((cur) =>
                        cur === repo.repositoryId ? null : repo.repositoryId,
                      )
                    }
                    onCloseFreeze={() => setFreezeRepoId(null)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {wave.repins.length > 0 && (
        <div className="exec-repins">
          <h4 id={repinHeadingId}>
            Consumer re-pins <span className="count">{wave.repins.length}</span>
          </h4>
          <div
            className="exec-table-wrapper"
            tabIndex={0}
            role="region"
            aria-labelledby={repinHeadingId}
          >
            <table className="exec-repin-table">
              <thead>
                <tr>
                  <th scope="col">Upstream → Consumer</th>
                  <th scope="col">Status</th>
                  <th scope="col">Ref / PR / SHA</th>
                  <th scope="col">Reported by</th>
                  {!archived && <th scope="col">Action</th>}
                </tr>
              </thead>
              <tbody>
                {wave.repins.map((rp) => (
                  <RepinRow
                    key={`${rp.upstreamRepoId}:${rp.consumerRepoId}`}
                    waveId={wave.id}
                    repin={rp}
                    archived={archived}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

function TierRepoTile({
  wave,
  repo,
  editable,
  editingFreeze,
  onToggleFreeze,
  onCloseFreeze,
}: {
  wave: WaveDetail;
  repo: WaveTierRepo;
  editable: boolean;
  editingFreeze: boolean;
  onToggleFreeze: () => void;
  onCloseFreeze: () => void;
}) {
  const action = useWrites();
  const [freezeStatus, setFreezeStatus] = useState(repo.freezeStatus);
  const [frozenRef, setFrozenRef] = useState(repo.frozenRef);
  const [notes, setNotes] = useState(repo.notes);

  return (
    <div className={`exec-tier-repo freeze-${repo.freezeStatus}`}>
      <div className="exec-tier-repo-top">
        <a href={"#repositories/" + repo.repositoryId}>
          <strong>{repo.repositoryName}</strong>
        </a>
        <span className={`exec-pill freeze-${repo.freezeStatus}`}>
          {repo.freezeStatus}
        </span>
      </div>
      <small className="muted">
        {repo.doneCount}/{repo.stepCount} done
        {repo.doingCount > 0 ? ` · ${repo.doingCount} doing` : ""}
        {repo.blockedCount > 0 ? ` · ${repo.blockedCount} blocked` : ""}
      </small>
      {repo.frozenRef && <code className="exec-ref">{repo.frozenRef}</code>}
      {repo.pendingDownstreamRepins > 0 && (
        <small className="exec-warn-text">
          {repo.pendingDownstreamRepins} downstream re-pin
          {repo.pendingDownstreamRepins === 1 ? "" : "s"} pending
        </small>
      )}
      {repo.pendingUpstreamRepins > 0 && (
        <small className="exec-warn-text">
          Needs {repo.pendingUpstreamRepins} upstream re-pin
          {repo.pendingUpstreamRepins === 1 ? "" : "s"}
        </small>
      )}
      {editable && (
        <button
          type="button"
          className="quiet"
          onClick={() => {
            setFreezeStatus(repo.freezeStatus);
            setFrozenRef(repo.frozenRef);
            setNotes(repo.notes);
            onToggleFreeze();
          }}
        >
          {editingFreeze
            ? "Cancel freeze edit"
            : repo.freezeStatus === "frozen"
              ? "Update freeze"
              : "Freeze / batch"}
        </button>
      )}
      {editingFreeze && editable && (
        <form
          className="exec-freeze-form"
          onSubmit={async (e) => {
            e.preventDefault();
            await action
              .mutateAsync(() =>
                commands.setWaveFreeze(wave.id, {
                  revision: wave.revision,
                  repositoryId: repo.repositoryId,
                  freezeStatus,
                  frozenRef: frozenRef.trim(),
                  notes: notes.trim(),
                }),
              )
              .then(onCloseFreeze)
              .catch(() => undefined);
          }}
        >
          <label>
            Freeze state
            <select
              aria-label={`Freeze state for ${repo.repositoryName}`}
              value={freezeStatus}
              onChange={(e) =>
                setFreezeStatus(e.target.value as WaveTierRepo["freezeStatus"])
              }
            >
              {FREEZE_STATUSES.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Frozen ref / tag / SHA
            <input
              placeholder="e.g. v1.4.0 or commit SHA"
              value={frozenRef}
              maxLength={500}
              onChange={(e) => setFrozenRef(e.target.value)}
            />
          </label>
          <label>
            Notes
            <input
              placeholder="Optional freeze notes"
              value={notes}
              maxLength={4000}
              onChange={(e) => setNotes(e.target.value)}
            />
          </label>
          <ErrorMessage error={action.error} />
          <div className="toolbar">
            <button type="submit" className="primary" disabled={action.isPending}>
              Save freeze
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function RepinRow({
  waveId,
  repin,
  archived,
}: {
  waveId: string;
  repin: WaveRepin;
  archived: boolean;
}) {
  const action = useWrites();
  const verify = (status: WaveRepin["status"]) =>
    action.mutate(() =>
      commands.verifyWaveRepin(waveId, {
        revision: repin.revision,
        upstreamRepoId: repin.upstreamRepoId,
        consumerRepoId: repin.consumerRepoId,
        status,
      }),
    );

  return (
    <tr>
      <th scope="row">
        <a href={"#repositories/" + repin.upstreamRepoId}>
          {repin.upstreamRepoName}
        </a>{" "}
        →{" "}
        <a href={"#repositories/" + repin.consumerRepoId}>
          {repin.consumerRepoName}
        </a>
      </th>
      <td>
        <span className={`exec-pill repin-${repin.status}`}>
          {repin.status.replace("_", " ")}
        </span>
      </td>
      <td>
        {repin.pinnedRef && <code>{repin.pinnedRef}</code>}{" "}
        {repin.prUrl && (
          <a href={repin.prUrl} target="_blank" rel="noreferrer">
            PR
          </a>
        )}{" "}
        {repin.commitSha && <code>{repin.commitSha.slice(0, 10)}</code>}
        {!repin.pinnedRef && !repin.prUrl && !repin.commitSha && (
          <span className="muted">—</span>
        )}
      </td>
      <td>{repin.reportedByLabel || <span className="muted">—</span>}</td>
      {!archived && (
        <td>
          <div className="toolbar">
            {repin.status !== "verified" && (
              <button
                type="button"
                className="primary"
                disabled={action.isPending}
                onClick={() => verify("verified")}
              >
                Verify
              </button>
            )}
            {repin.status !== "waived" && repin.status !== "verified" && (
              <button
                type="button"
                className="quiet"
                disabled={action.isPending}
                onClick={() => verify("waived")}
              >
                Waive
              </button>
            )}
            {(repin.status === "verified" || repin.status === "waived") && (
              <button
                type="button"
                className="quiet"
                disabled={action.isPending}
                onClick={() => verify("pending")}
              >
                Reopen
              </button>
            )}
          </div>
          <ErrorMessage error={action.error} />
        </td>
      )}
    </tr>
  );
}

function RepoLaneCard({
  roadmapId,
  lane,
  activeWave,
  archived,
  includeDone,
  onSplit,
}: {
  roadmapId: string;
  lane: DispatchRepoLane;
  activeWave: WaveDetail | null;
  archived: boolean;
  includeDone: boolean;
  onSplit: (step: DispatchStepCard) => void;
}) {
  const action = useWrites();
  const [confirmStartId, setConfirmStartId] = useState<string | null>(null);
  const waveEditable =
    !archived &&
    activeWave !== null &&
    !activeWave.archived &&
    activeWave.status !== "completed" &&
    activeWave.status !== "cancelled";
  const pendingRepins = lane.repins.filter(
    (rp) => rp.status === "pending" || rp.status === "in_progress",
  ).length;
  const atOrOverCap =
    lane.effectiveWipCap !== null &&
    (lane.doingCount >= lane.effectiveWipCap ||
      lane.activeWriters >= lane.effectiveWipCap);
  const isFrozen = lane.freezeState?.freezeStatus === "frozen";

  const reorderQueue = (index: number, delta: number) => {
    const ids = lane.queued.map((s) => s.id);
    const swap = index + delta;
    if (swap < 0 || swap >= ids.length) return;
    [ids[index], ids[swap]] = [ids[swap], ids[index]];
    action.mutate(() =>
      commands.reorderRepositoryQueue(lane.repositoryId, { stepIds: ids }),
    );
  };

  const toggleWave = (step: DispatchStepCard) => {
    if (!activeWave) return;
    action.mutate(() =>
      commands.setWaveSteps(activeWave.id, {
        revision: activeWave.revision,
        mode: step.inActiveWave ? "remove" : "add",
        stepIds: [step.id],
      }),
    );
  };

  const renderStepRow = (
    step: DispatchStepCard,
    options: {
      queueIndex?: number;
      competing?: boolean;
    } = {},
  ) => (
    <li className="exec-step-item" key={step.id}>
      <div className="exec-step-main">
        <span className={"status-dot " + step.status} aria-hidden="true" />
        <div className="exec-step-text">
          <a
            className="item-title"
            href={`#roadmaps/${roadmapId}/${step.id}`}
            title={step.title}
          >
            {step.title}
          </a>
          {step.phasePath.length > 0 && (
            <small className="muted">
              {step.phasePath.map((p) => p.title).join(" › ")}
            </small>
          )}
          <div className="exec-pills">
            {step.workClass && (
              <span className={`exec-pill wc-${step.workClass}`}>
                {workClassLabel(step.workClass)}
              </span>
            )}
            {step.inActiveWave && activeWave && (
              <span className="exec-pill in-wave">{activeWave.name}</span>
            )}
            {step.multiRepo && (
              <span className="exec-pill is-warning">
                Multi-repo ({step.repositoryIds.length})
              </span>
            )}
            {step.activeAgents.map((a) => (
              <span
                key={a.agentId}
                className={"exec-pill" + (a.stale ? " is-stale" : "")}
              >
                {a.label}
              </span>
            ))}
          </div>
        </div>
      </div>

      {!archived && (
        <div className="exec-step-actions">
          {options.competing ? (
            <>
              <button
                type="button"
                disabled={action.isPending}
                onClick={() =>
                  action.mutate(() =>
                    commands.setStepProgress(step.id, {
                      revision: step.revision,
                      status: "open",
                    }),
                  )
                }
              >
                Pause to open
              </button>
              {waveEditable && (
                <button
                  type="button"
                  className="quiet"
                  disabled={action.isPending}
                  onClick={() => toggleWave(step)}
                >
                  Add to wave
                </button>
              )}
            </>
          ) : options.queueIndex !== undefined ? (
            <>
              <button
                type="button"
                className="quiet"
                aria-label={`Move ${step.title} up in ${lane.repositoryName} queue`}
                disabled={options.queueIndex === 0 || action.isPending}
                onClick={() => reorderQueue(options.queueIndex!, -1)}
              >
                ↑
              </button>
              <button
                type="button"
                className="quiet"
                aria-label={`Move ${step.title} down in ${lane.repositoryName} queue`}
                disabled={
                  options.queueIndex === lane.queued.length - 1 ||
                  action.isPending
                }
                onClick={() => reorderQueue(options.queueIndex!, 1)}
              >
                ↓
              </button>
              {confirmStartId === step.id ? (
                <div className="exec-start-confirm" role="alert">
                  <small>
                    {isFrozen
                      ? `${lane.repositoryName} is frozen in ${activeWave?.name}.`
                      : `${lane.repositoryName} is at or over its WIP cap (${lane.effectiveWipCap}).`}{" "}
                    Start anyway?
                  </small>
                  <button
                    type="button"
                    className="primary"
                    disabled={action.isPending}
                    onClick={() =>
                      action.mutate(
                        () =>
                          commands.setStepProgress(step.id, {
                            revision: step.revision,
                            status: "doing",
                          }),
                        { onSuccess: () => setConfirmStartId(null) },
                      )
                    }
                  >
                    Confirm start
                  </button>
                  <button
                    type="button"
                    className="quiet"
                    onClick={() => setConfirmStartId(null)}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  disabled={action.isPending}
                  onClick={() => {
                    if (atOrOverCap || isFrozen) {
                      setConfirmStartId(step.id);
                    } else {
                      action.mutate(() =>
                        commands.setStepProgress(step.id, {
                          revision: step.revision,
                          status: "doing",
                        }),
                      );
                    }
                  }}
                >
                  Start
                </button>
              )}
            </>
          ) : (
            <StatusSelect
              disabled={action.isPending}
              label={"Progress for " + step.title}
              value={step.status as (typeof STATUSES)[number]}
              options={STATUSES}
              onChange={(status) =>
                action.mutate(() =>
                  commands.setStepProgress(step.id, {
                    revision: step.revision,
                    status,
                  }),
                )
              }
            />
          )}
          {step.multiRepo && (
            <button
              type="button"
              className="quiet"
              onClick={() => onSplit(step)}
            >
              Split by repo
            </button>
          )}
          {waveEditable && !options.competing && (
            <button
              type="button"
              className="quiet"
              disabled={action.isPending}
              onClick={() => toggleWave(step)}
            >
              {step.inActiveWave ? "Leave wave" : "Add to wave"}
            </button>
          )}
        </div>
      )}
    </li>
  );

  return (
    <article
      className={"exec-lane" + (lane.overWipCap ? " is-over-cap" : "")}
      aria-label={`Repository lane ${lane.repositoryName}`}
    >
      <header className="exec-lane-head">
        <div>
          <h4>
            <a href={"#repositories/" + lane.repositoryId}>
              {lane.repositoryName}
            </a>
          </h4>
          <div className="exec-pills">
            {lane.tier !== null && (
              <span className="exec-pill">Tier {lane.tier}</span>
            )}
            <span
              className={"exec-pill" + (lane.overWipCap ? " is-warning" : "")}
            >
              {lane.effectiveWipCap !== null
                ? `WIP ${lane.doingCount}/${lane.effectiveWipCap} doing · ${lane.activeWriters}/${lane.effectiveWipCap} writers`
                : `${lane.doingCount} doing · ${lane.activeWriters} writers`}
            </span>
            {lane.freezeState && lane.freezeState.freezeStatus !== "open" && (
              <span
                className={`exec-pill freeze-${lane.freezeState.freezeStatus}`}
              >
                {lane.freezeState.freezeStatus}
                {lane.freezeState.frozenRef
                  ? ` @ ${lane.freezeState.frozenRef}`
                  : ""}
              </span>
            )}
            {pendingRepins > 0 && (
              <span className="exec-pill is-warning">
                {pendingRepins} re-pin{pendingRepins === 1 ? "" : "s"} pending
              </span>
            )}
          </div>
        </div>
        {lane.discussionId && (
          <a
            className="quiet exec-thread-link"
            href={`#discussions/${lane.discussionId}`}
          >
            Repository thread
          </a>
        )}
      </header>

      {lane.participations.length > 0 && (
        <div className="exec-lane-agents">
          <small className="muted">Active agents:</small>
          {lane.participations.map((p) => (
            <a
              key={p.participationId}
              className={"exec-pill" + (p.stale ? " is-stale" : "")}
              href={"#agents/" + p.agentId}
              title={p.description || p.branch}
            >
              {p.agentLabel}
              {p.branch ? ` (${p.branch})` : ""}
            </a>
          ))}
        </div>
      )}

      <ErrorMessage error={action.error} />

      <div className="exec-lane-section">
        <h5>
          Doing{" "}
          {activeWave ? "(Active wave & stabilization)" : ""}{" "}
          <span className="count">{lane.doing.length}</span>
        </h5>
        {lane.doing.length > 0 ? (
          <ul className="exec-step-list">
            {lane.doing.map((step) => renderStepRow(step))}
          </ul>
        ) : (
          <p className="muted exec-empty">No steps currently doing.</p>
        )}
      </div>

      {lane.competingWip.length > 0 && (
        <div className="exec-competing">
          <h5>
            Competing Doing WIP (outside {activeWave?.name}){" "}
            <span className="count">{lane.competingWip.length}</span>
          </h5>
          <ul className="exec-step-list">
            {lane.competingWip.map((step) =>
              renderStepRow(step, { competing: true }),
            )}
          </ul>
        </div>
      )}

      <div className="exec-lane-section">
        <h5>
          Queued <span className="count">{lane.queued.length}</span>
        </h5>
        {lane.queued.length > 0 ? (
          <ul className="exec-step-list">
            {lane.queued.map((step, idx) =>
              renderStepRow(step, { queueIndex: idx }),
            )}
          </ul>
        ) : (
          <p className="muted exec-empty">Queue is clear.</p>
        )}
      </div>

      {lane.blocked.length > 0 && (
        <div className="exec-lane-section">
          <h5>
            Blocked <span className="count">{lane.blocked.length}</span>
          </h5>
          <ul className="exec-step-list">
            {lane.blocked.map((step) => renderStepRow(step))}
          </ul>
        </div>
      )}

      {includeDone && lane.done.length > 0 && (
        <div className="exec-lane-section">
          <h5>
            Done <span className="count">{lane.done.length}</span>
          </h5>
          <ul className="exec-step-list">
            {lane.done.map((step) => renderStepRow(step))}
          </ul>
        </div>
      )}
    </article>
  );
}

function UnassignedLeafRow({
  roadmapId,
  step,
  repoMap,
  archived,
}: {
  roadmapId: string;
  step: DispatchUnassignedStepCard;
  repoMap: Map<string, Repository>;
  archived: boolean;
}) {
  const action = useWrites();
  const [selectedRepos, setSelectedRepos] = useState<string[]>([]);

  return (
    <li className="exec-step-item">
      <div className="exec-step-main">
        <span className={"status-dot " + step.status} aria-hidden="true" />
        <div className="exec-step-text">
          <a className="item-title" href={`#roadmaps/${roadmapId}/${step.id}`}>
            {step.title}
          </a>
          {step.phasePath.length > 0 && (
            <small className="muted">
              {step.phasePath.map((p) => p.title).join(" › ")}
            </small>
          )}
          {step.suggestedRepoIds.length > 0 && (
            <small className="muted">
              Suggested:{" "}
              {step.suggestedRepoIds
                .map((rid) => repoMap.get(rid)?.name ?? rid)
                .join(", ")}
            </small>
          )}
        </div>
      </div>
      {!archived && (
        <div className="exec-unassigned-controls">
          {step.suggestedRepoIds.length > 0 && (
            <button
              type="button"
              className="primary"
              disabled={action.isPending}
              onClick={() =>
                action.mutate(() =>
                  commands.updateStep(step.id, {
                    revision: step.revision,
                    repositoryIds: step.suggestedRepoIds,
                  }),
                )
              }
            >
              Apply suggestion
            </button>
          )}
          <RepositoryPicker
            label={`Assign repositories to ${step.title}`}
            value={selectedRepos}
            onChange={setSelectedRepos}
            folded
          />
          <button
            type="button"
            disabled={selectedRepos.length === 0 || action.isPending}
            onClick={() =>
              action.mutate(
                () =>
                  commands.updateStep(step.id, {
                    revision: step.revision,
                    repositoryIds: selectedRepos,
                  }),
                { onSuccess: () => setSelectedRepos([]) },
              )
            }
          >
            Assign
          </button>
          <ErrorMessage error={action.error} />
        </div>
      )}
    </li>
  );
}
