import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { commands } from "./commands";
import { dataQuery, useData, useWrites } from "./hooks";
import { LinkPicker } from "./LinkPicker";
import type {
  GateSummary,
  RoadmapGatesAnswer,
  Schema,
  Step,
  StepGate,
  StepGatesAnswer,
} from "./apiTypes";
import type { DeliverablesAnswer } from "./deliverableTypes";
import { Markdown } from "./ui/Markdown";
import { Empty, ErrorMessage } from "./ui/Messages";

export const EMPTY_GATE_SUMMARY: GateSummary = {
  total: 0,
  open: 0,
  passed: 0,
  waived: 0,
  unavailable: 0,
  blocking: 0,
  humanOnlyBlocking: 0,
};

type GateKind = StepGate["kind"];
type GateTargetType = NonNullable<StepGate["target"]>["type"];
type TargetChoice = { type: GateTargetType; id: string; title: string };
type SearchPick = { id: string; title?: string };

const KIND_OPTIONS: readonly { value: GateKind; label: string }[] = [
  { value: "manual", label: "Manual check" },
  { value: "approval", label: "Approval" },
  { value: "verification", label: "Verification" },
  { value: "prerequisite", label: "Prerequisite" },
];

const KIND_LABELS: Record<GateKind, string> = {
  manual: "Manual",
  approval: "Approval",
  verification: "Verification",
  prerequisite: "Prerequisite",
};

const STATE_LABELS: Record<StepGate["state"], string> = {
  open: "Open",
  passed: "Passed",
  waived: "Waived",
  unavailable: "Unavailable",
};

const TARGET_TYPE_LABELS: Record<GateTargetType, string> = {
  step: "Step",
  deliverable: "Deliverable",
  ticket: "Ticket",
  question: "Question",
};

const SUMMARY_LIMIT = 8;

export function GateBadge({ summary }: { summary?: GateSummary | null }) {
  if (!summary || summary.total === 0) return null;
  if (summary.blocking > 0) {
    const label = `${summary.blocking} open gate${summary.blocking === 1 ? "" : "s"}`;
    const detail =
      summary.humanOnlyBlocking > 0
        ? `${label} (${summary.humanOnlyBlocking} human-only)`
        : label;
    return (
      <span className="gate-pill is-blocking" title={detail}>
        {label}
        {summary.humanOnlyBlocking > 0 ? " · Human" : ""}
      </span>
    );
  }
  return (
    <span
      className="gate-pill is-satisfied"
      title={`${summary.passed} passed, ${summary.waived} waived`}
    >
      Gates met
    </span>
  );
}

export function RoadmapGatesSummary({ roadmapId }: { roadmapId: string }) {
  const query = useData<RoadmapGatesAnswer>(
    `/roadmaps/${encodeURIComponent(roadmapId)}/gates?archived=false&onlyOpen=true`,
    undefined,
    { conditional: true },
  );
  if (!query.data) {
    return query.error ? <ErrorMessage error={query.error} /> : null;
  }
  const summary = query.data.summary;
  const items = query.data.items ?? [];
  if (!summary || summary.total === 0) return null;
  const shown = items.slice(0, SUMMARY_LIMIT);
  const more = items.length - shown.length;
  return (
    <section className="gate-rollup" aria-label="Roadmap gates summary">
      <div className="gate-rollup-head">
        <div>
          <h3>Gates</h3>
          <p className="muted">
            {summary.blocking} open · {summary.passed} passed · {summary.waived}{" "}
            waived
            {summary.humanOnlyBlocking > 0
              ? ` · ${summary.humanOnlyBlocking} human-only awaiting you`
              : ""}
          </p>
        </div>
        <a className="quiet" href={`#roadmaps/${roadmapId}/outline`}>
          View outline
        </a>
      </div>
      {shown.length > 0 && (
        <ul className="gate-rollup-list">
          {shown.map((gate) => (
            <li key={gate.id} className="gate-rollup-row">
              <div>
                <strong>{gate.title}</strong>
                <span className={`gate-state is-${gate.state}`}>
                  {STATE_LABELS[gate.state]}
                </span>
                {gate.humanOnly && (
                  <span className="gate-tag is-human">Human-only</span>
                )}
                {gate.isParent && (
                  <span className="gate-tag">Phase gate</span>
                )}
              </div>
              <small className="muted">
                On{" "}
                <a href={`#roadmaps/${roadmapId}/${gate.stepId}`}>
                  {gate.stepTitle}
                </a>
                {gate.target && (
                  <>
                    {" "}
                    · Linked {TARGET_TYPE_LABELS[gate.target.type].toLowerCase()}:{" "}
                    {gate.target.href ? (
                      <a href={gate.target.href}>{gate.target.title}</a>
                    ) : (
                      gate.target.title
                    )}{" "}
                    ({gate.target.status})
                  </>
                )}
              </small>
            </li>
          ))}
        </ul>
      )}
      {more > 0 && (
        <p className="muted">
          and {more} more open gate{more === 1 ? "" : "s"} across this roadmap.
        </p>
      )}
    </section>
  );
}

function GateTargetPicker({
  roadmapId,
  stepId,
  value,
  onChange,
}: {
  roadmapId: string;
  stepId: string;
  value: TargetChoice | null;
  onChange: (next: TargetChoice | null) => void;
}) {
  const [kind, setKind] = useState<"" | GateTargetType>(value?.type ?? "");
  const deliverables = useQuery({
    ...dataQuery<DeliverablesAnswer>(
      `/roadmaps/${encodeURIComponent(roadmapId)}/deliverables?scope=tree&archived=false&limit=200&offset=0`,
    ),
    enabled: kind === "deliverable",
  });
  const deliverableItems = deliverables.data?.items ?? [];

  return (
    <div className="gate-target-picker">
      <label>
        <span>Linked record (optional)</span>
        <select
          aria-label="Linked record type"
          value={kind}
          onChange={(event) => {
            const next = event.target.value as "" | GateTargetType;
            setKind(next);
            onChange(null);
          }}
        >
          <option value="">None (manual gate)</option>
          <option value="step">Roadmap step</option>
          <option value="deliverable">Deliverable</option>
          <option value="ticket">Ticket</option>
          <option value="question">Roadmap question</option>
        </select>
      </label>
      {kind !== "" && value && (
        <p className="gate-picked">
          Linked {TARGET_TYPE_LABELS[value.type].toLowerCase()}:{" "}
          <strong>{value.title}</strong>{" "}
          <button
            type="button"
            className="quiet"
            onClick={() => onChange(null)}
          >
            Clear target
          </button>
        </p>
      )}
      {kind === "deliverable" && !value && (
        <label>
          <span>Choose deliverable</span>
          <select
            aria-label="Choose deliverable"
            value=""
            onChange={(event) => {
              const found = deliverableItems.find(
                (item) => item.id === event.target.value,
              );
              if (found) {
                onChange({
                  type: "deliverable",
                  id: found.id,
                  title: found.title,
                });
              }
            }}
          >
            <option value="">
              {deliverableItems.length
                ? "Select a deliverable…"
                : "No active deliverables on this roadmap"}
            </option>
            {deliverableItems.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title} ({item.status.replaceAll("_", " ")})
              </option>
            ))}
          </select>
        </label>
      )}
      {(kind === "step" || kind === "ticket" || kind === "question") &&
        !value && (
          <LinkPicker<SearchPick>
            action={`Find ${TARGET_TYPE_LABELS[kind].toLowerCase()}`}
            label={TARGET_TYPE_LABELS[kind]}
            placeholder={`Search ${TARGET_TYPE_LABELS[kind].toLowerCase()}…`}
            path={(query) =>
              `/search?q=${encodeURIComponent(query)}&archived=false&limit=20&offset=0&type=${kind}`
            }
            linked={(option) => kind === "step" && option.id === stepId}
            option={(option) => (
              <span>{option.title || `${kind} ${option.id.slice(0, 8)}`}</span>
            )}
            none={`No matching ${TARGET_TYPE_LABELS[kind].toLowerCase()} found.`}
            onPick={(option) =>
              onChange({
                type: kind,
                id: option.id,
                title: option.title || `${kind} ${option.id.slice(0, 8)}`,
              })
            }
          />
        )}
    </div>
  );
}

function GateRow({
  roadmapId,
  stepId,
  gate,
  disabled,
}: {
  roadmapId: string;
  stepId: string;
  gate: StepGate;
  disabled: boolean;
}) {
  const action = useWrites();
  const [mode, setMode] = useState<null | "pass" | "waive" | "reopen" | "edit">(
    null,
  );
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [editTitle, setEditTitle] = useState(gate.title);
  const [editCriteria, setEditCriteria] = useState(gate.criteria);
  const [editKind, setEditKind] = useState<GateKind>(gate.kind);
  const [editHumanOnly, setEditHumanOnly] = useState(gate.humanOnly);
  const [editTarget, setEditTarget] = useState<TargetChoice | null>(
    gate.target
      ? {
          type: gate.target.type,
          id: gate.target.id,
          title: gate.target.title,
        }
      : null,
  );

  const canPass = !gate.target && gate.storedStatus === "open";
  const canWaive = !gate.satisfied;
  const canReopen =
    gate.storedStatus === "passed" || gate.storedStatus === "waived";
  const canEditTarget = gate.storedStatus === "open";

  function openEdit() {
    setEditTitle(gate.title);
    setEditCriteria(gate.criteria);
    setEditKind(gate.kind);
    setEditHumanOnly(gate.humanOnly);
    setEditTarget(
      gate.target
        ? {
            type: gate.target.type,
            id: gate.target.id,
            title: gate.target.title,
          }
        : null,
    );
    setMode("edit");
  }

  return (
    <li className={`gate-row state-${gate.state}`}>
      <div className="gate-row-head">
        <div className="gate-row-title">
          <strong>{gate.title}</strong>
          <span className={`gate-state is-${gate.state}`}>
            {STATE_LABELS[gate.state]}
          </span>
          <span className="gate-tag">{KIND_LABELS[gate.kind]}</span>
          {gate.humanOnly && (
            <span className="gate-tag is-human">Human-only</span>
          )}
          {gate.autoEvaluated && (
            <span className="gate-tag">Auto-evaluated</span>
          )}
        </div>
        {!disabled && !gate.archived && mode === null && (
          <div className="toolbar">
            {canPass && (
              <button
                type="button"
                className="primary"
                disabled={action.isPending}
                onClick={() => {
                  setNote("");
                  setMode("pass");
                }}
              >
                Pass gate
              </button>
            )}
            {canWaive && (
              <button
                type="button"
                className="quiet"
                disabled={action.isPending}
                onClick={() => {
                  setReason("");
                  setMode("waive");
                }}
              >
                Waive
              </button>
            )}
            {canReopen && (
              <button
                type="button"
                className="quiet"
                disabled={action.isPending}
                onClick={() => {
                  setReason("");
                  setMode("reopen");
                }}
              >
                Reopen
              </button>
            )}
            <button
              type="button"
              className="quiet"
              disabled={action.isPending}
              onClick={openEdit}
            >
              Edit
            </button>
            <button
              type="button"
              className="quiet"
              disabled={action.isPending}
              onClick={() =>
                action.mutate(() =>
                  commands.setGateArchived(gate.id, true, {
                    revision: gate.revision,
                  }),
                )
              }
            >
              Archive
            </button>
          </div>
        )}
        {!disabled && gate.archived && (
          <button
            type="button"
            className="quiet"
            disabled={action.isPending}
            onClick={() =>
              action.mutate(() =>
                commands.setGateArchived(gate.id, false, {
                  revision: gate.revision,
                }),
              )
            }
          >
            Recover
          </button>
        )}
      </div>

      {gate.criteria && mode !== "edit" && <Markdown text={gate.criteria} />}

      {gate.target && mode !== "edit" && (
        <p className="gate-target-line muted">
          Linked {TARGET_TYPE_LABELS[gate.target.type].toLowerCase()}:{" "}
          {gate.target.href ? (
            <a href={gate.target.href}>{gate.target.title}</a>
          ) : (
            <span>{gate.target.title}</span>
          )}{" "}
          · status: <strong>{gate.target.status.replaceAll("_", " ")}</strong>
        </p>
      )}

      {gate.resolution && (
        <p className="gate-resolution muted">
          {gate.storedStatus === "waived" ? "Waived" : "Passed"} by{" "}
          <strong>{gate.resolution.actorLabel}</strong> on{" "}
          {new Date(gate.resolution.resolvedAt).toLocaleString()}
          {gate.resolution.note ? ` — ${gate.resolution.note}` : ""}
        </p>
      )}

      {mode === "pass" && (
        <form
          className="gate-inline-form"
          onSubmit={async (event: FormEvent) => {
            event.preventDefault();
            await action
              .mutateAsync(() =>
                commands.passGate(gate.id, {
                  revision: gate.revision,
                  note: note.trim(),
                }),
              )
              .then(() => {
                setMode(null);
                setNote("");
              })
              .catch(() => undefined);
          }}
        >
          <label>
            <span>Verification note (optional)</span>
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Evidence, build link, or sign-off note"
            />
          </label>
          <div className="toolbar">
            <button className="primary" disabled={action.isPending}>
              Confirm pass
            </button>
            <button
              type="button"
              className="quiet"
              onClick={() => setMode(null)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {mode === "waive" && (
        <form
          className="gate-inline-form"
          onSubmit={async (event: FormEvent) => {
            event.preventDefault();
            if (!reason.trim()) return;
            await action
              .mutateAsync(() =>
                commands.waiveGate(gate.id, {
                  revision: gate.revision,
                  reason: reason.trim(),
                }),
              )
              .then(() => {
                setMode(null);
                setReason("");
              })
              .catch(() => undefined);
          }}
        >
          <label>
            <span>Waive reason (required)</span>
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Why this gate is waived for completion"
              required
            />
          </label>
          <div className="toolbar">
            <button className="primary" disabled={action.isPending}>
              Confirm waive
            </button>
            <button
              type="button"
              className="quiet"
              onClick={() => setMode(null)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {mode === "reopen" && (
        <form
          className="gate-inline-form"
          onSubmit={async (event: FormEvent) => {
            event.preventDefault();
            if (!reason.trim()) return;
            await action
              .mutateAsync(() =>
                commands.reopenGate(gate.id, {
                  revision: gate.revision,
                  reason: reason.trim(),
                }),
              )
              .then(() => {
                setMode(null);
                setReason("");
              })
              .catch(() => undefined);
          }}
        >
          <label>
            <span>Reopen reason (required)</span>
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Why this gate needs to be satisfied again"
              required
            />
          </label>
          <div className="toolbar">
            <button className="primary" disabled={action.isPending}>
              Confirm reopen
            </button>
            <button
              type="button"
              className="quiet"
              onClick={() => setMode(null)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {mode === "edit" && (
        <form
          className="gate-inline-form"
          onSubmit={async (event: FormEvent) => {
            event.preventDefault();
            const body: Schema["GateUpdate"] = {
              revision: gate.revision,
              title: editTitle.trim(),
              criteria: editCriteria,
              kind: editKind,
              humanOnly: editHumanOnly,
              ...(canEditTarget
                ? {
                    target: editTarget
                      ? { type: editTarget.type, id: editTarget.id }
                      : null,
                  }
                : {}),
            };
            await action
              .mutateAsync(() => commands.updateGate(gate.id, body))
              .then(() => setMode(null))
              .catch(() => undefined);
          }}
        >
          <label>
            <span>Gate title</span>
            <input
              value={editTitle}
              onChange={(event) => setEditTitle(event.target.value)}
              required
            />
          </label>
          <label>
            <span>Criteria (Markdown, optional)</span>
            <textarea
              rows={2}
              value={editCriteria}
              onChange={(event) => setEditCriteria(event.target.value)}
            />
          </label>
          <div className="gate-form-row">
            <label>
              <span>Gate kind</span>
              <select
                value={editKind}
                onChange={(event) =>
                  setEditKind(event.target.value as GateKind)
                }
              >
                {KIND_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="gate-check-label">
              <input
                type="checkbox"
                checked={editHumanOnly}
                onChange={(event) => setEditHumanOnly(event.target.checked)}
              />{" "}
              Human-only (agents cannot pass or edit)
            </label>
          </div>
          {canEditTarget && (
            <GateTargetPicker
              roadmapId={roadmapId}
              stepId={stepId}
              value={editTarget}
              onChange={setEditTarget}
            />
          )}
          <div className="toolbar">
            <button className="primary" disabled={action.isPending}>
              Save gate
            </button>
            <button
              type="button"
              className="quiet"
              onClick={() => setMode(null)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
      <ErrorMessage error={action.error} />
    </li>
  );
}

export function StepGatesPanel({
  roadmapId,
  step,
  isParent,
  allChildrenDone,
  disabled,
}: {
  roadmapId: string;
  step: Step;
  isParent: boolean;
  allChildrenDone: boolean;
  disabled: boolean;
}) {
  const [adding, setAdding] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [title, setTitle] = useState("");
  const [criteria, setCriteria] = useState("");
  const [kind, setKind] = useState<GateKind>("manual");
  const [humanOnly, setHumanOnly] = useState(false);
  const [target, setTarget] = useState<TargetChoice | null>(null);
  const action = useWrites();

  const activeQuery = useData<StepGatesAnswer>(
    `/steps/${encodeURIComponent(step.id)}/gates?archived=false`,
  );
  const archivedQuery = useQuery({
    ...dataQuery<StepGatesAnswer>(
      `/steps/${encodeURIComponent(step.id)}/gates?archived=true`,
    ),
    enabled: showArchived,
  });

  const activeGates = activeQuery.data?.items ?? [];
  const archivedGates = archivedQuery.data?.items ?? [];
  const summary =
    activeQuery.data?.summary ?? step.gateSummary ?? EMPTY_GATE_SUMMARY;

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    await action
      .mutateAsync(() =>
        commands.createStepGate(step.id, {
          title: title.trim(),
          criteria,
          kind: target && kind === "manual" ? "prerequisite" : kind,
          humanOnly,
          target: target ? { type: target.type, id: target.id } : null,
        }),
      )
      .then(() => {
        setTitle("");
        setCriteria("");
        setKind("manual");
        setHumanOnly(false);
        setTarget(null);
        setAdding(false);
      })
      .catch(() => undefined);
  }

  return (
    <section className="step-gates" aria-label="Step gates">
      <div className="section-heading">
        <h3>
          Gates <span className="count">{activeGates.length}</span>
        </h3>
        <div className="toolbar">
          <label className="gate-archived-toggle">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(event) => setShowArchived(event.target.checked)}
            />{" "}
            Show archived gates
          </label>
          {!disabled && (
            <button
              type="button"
              className="quiet"
              onClick={() => setAdding((cur) => !cur)}
            >
              {adding ? "Cancel gate" : "Add gate"}
            </button>
          )}
        </div>
      </div>

      <ErrorMessage error={activeQuery.error ?? archivedQuery.error} />

      {!isParent && step.status === "done" && summary.blocking > 0 && (
        <p className="warning">
          This step is marked Done, but {summary.blocking} gate
          {summary.blocking === 1 ? "" : "s"} remain unsatisfied. Satisfy or
          waive {summary.blocking === 1 ? "it" : "them"}, or reopen the step.
        </p>
      )}
      {isParent && allChildrenDone && summary.blocking > 0 && (
        <p className="warning">
          All child steps are done, but this step stays in progress until its{" "}
          {summary.blocking} open gate{summary.blocking === 1 ? "" : "s"} are
          satisfied.
        </p>
      )}

      {adding && !disabled && (
        <form className="gate-create-form" onSubmit={handleCreate}>
          <label>
            <span>Gate title</span>
            <input
              aria-label="New gate title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="What must happen before this step can complete"
              required
            />
          </label>
          <label>
            <span>Criteria (Markdown, optional)</span>
            <textarea
              aria-label="New gate criteria"
              rows={2}
              value={criteria}
              onChange={(event) => setCriteria(event.target.value)}
              placeholder="Verification steps, sign-off criteria, or expected proof"
            />
          </label>
          <div className="gate-form-row">
            <label>
              <span>Gate kind</span>
              <select
                aria-label="New gate kind"
                value={kind}
                onChange={(event) => setKind(event.target.value as GateKind)}
              >
                {KIND_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="gate-check-label">
              <input
                type="checkbox"
                checked={humanOnly}
                onChange={(event) => setHumanOnly(event.target.checked)}
              />{" "}
              Human-only (agents cannot pass or edit)
            </label>
          </div>
          <GateTargetPicker
            roadmapId={roadmapId}
            stepId={step.id}
            value={target}
            onChange={(next) => {
              setTarget(next);
              if (next && kind === "manual") setKind("prerequisite");
            }}
          />
          <ErrorMessage error={action.error} />
          <div className="toolbar">
            <button className="primary" disabled={action.isPending}>
              Create gate
            </button>
            <button
              type="button"
              className="quiet"
              onClick={() => setAdding(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {activeGates.length > 0 ? (
        <ul className="gate-list">
          {activeGates.map((gate) => (
            <GateRow
              key={gate.id}
              roadmapId={roadmapId}
              stepId={step.id}
              gate={gate}
              disabled={disabled}
            />
          ))}
        </ul>
      ) : (
        <Empty>
          {isParent
            ? "No completion gates on this phase yet."
            : "No completion gates on this step yet."}
        </Empty>
      )}

      {showArchived && (
        <div className="gate-archived-section">
          <h4>Archived gates ({archivedGates.length})</h4>
          {archivedGates.length > 0 ? (
            <ul className="gate-list">
              {archivedGates.map((gate) => (
                <GateRow
                  key={gate.id}
                  roadmapId={roadmapId}
                  stepId={step.id}
                  gate={gate}
                  disabled={disabled}
                />
              ))}
            </ul>
          ) : (
            <p className="muted">No archived gates on this step.</p>
          )}
        </div>
      )}
    </section>
  );
}
