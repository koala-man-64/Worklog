import { useState, type ChangeEvent, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { getDataContext, request } from "./api";
import { commands } from "./commands";
import { useData, useWrites } from "./hooks";
import { Markdown } from "./ui/Markdown";
import { Empty, ErrorMessage } from "./ui/Messages";
import { Pager } from "./ui/Pager";
import type { Attachment } from "./apiTypes";
import type {
  DeliverableBacklinks,
  DeliverableCounts,
  DeliverableDetail,
  DeliverableExpectedBody,
  DeliverableHistoryAnswer,
  DeliverableHistoryEvent,
  DeliverableHistoryItem,
  DeliverableOption,
  DeliverableOptionsAnswer,
  DeliverableReference,
  DeliverableReferenceInput,
  DeliverableRequirement,
  DeliverableRequirementInput,
  DeliverableStatus,
  DeliverableStep,
  DeliverableSummary,
  DeliverableUpdateBody,
  DeliverablesAnswer,
} from "./deliverableTypes";

const PAGE = 50;
const LIST_SCOPE = "tree";
type WritableReferenceKind = "url" | "repository" | "resource" | "attachment";
type EditableReference = {
  draftId: string;
  kind: DeliverableReference["kind"];
  id?: string | null;
  url?: string | null;
  label: string;
  refresh?: boolean;
  availability?: DeliverableReference["availability"];
  href?: string | null;
};
type EditableRequirement = {
  id?: string | null;
  draftId: string;
  text: string;
  checked: boolean;
  verificationNote: string;
  evidence: EditableReference[];
};

export const roadmapDeliverablesPath = (roadmapId: string, deliverableId?: string) =>
  `#roadmaps/${roadmapId}/deliverables${deliverableId ? `/${deliverableId}` : ""}`;

const listPath = (roadmapId: string, scope: "tree" | "own", archived: boolean, offset = 0) =>
  `/roadmaps/${encodeURIComponent(roadmapId)}/deliverables?scope=${scope}&archived=${archived}&limit=${PAGE}&offset=${offset}`;

const statusText: Record<DeliverableStatus, string> = {
  planned: "Planned",
  ready_for_review: "Ready for review",
  accepted: "Accepted",
};

function deliverableStatusClass(status: DeliverableStatus) {
  return `deliverable-status status-${status}`;
}

function countsText(counts: DeliverableCounts) {
  return `${counts.planned} planned · ${counts.readyForReview} ready for review · ${counts.accepted} accepted`;
}

export function RoadmapDeliverablesSummary({
  roadmapId,
}: {
  roadmapId: string;
}) {
  const query = useData<DeliverablesAnswer>(
    listPath(roadmapId, LIST_SCOPE, false),
    undefined,
    { conditional: true },
  );
  if (!query.data) {
    return query.error ? <ErrorMessage error={query.error} /> : null;
  }
  return (
    <section className="deliverable-rollup">
      <div>
        <h3>Deliverables</h3>
        <p className="muted">
          {query.data.total
            ? countsText(query.data.counts)
            : "No deliverables yet. Add what this roadmap should produce."}
        </p>
      </div>
      <a className="quiet" href={roadmapDeliverablesPath(roadmapId)}>
        {query.data.total ? "View deliverables" : "Add deliverables"}
      </a>
    </section>
  );
}

export function StepDeliverables({
  roadmapId,
  stepId,
}: {
  roadmapId: string;
  stepId: string;
}) {
  const query = useData<DeliverableBacklinks>(
    `/steps/${encodeURIComponent(stepId)}/deliverables`,
  );
  return (
    <section className="step-deliverables">
      <div className="section-heading">
        <h3>Deliverables</h3>
        <a href={roadmapDeliverablesPath(roadmapId)}>View all</a>
      </div>
      {query.error ? (
        <ErrorMessage error={query.error} />
      ) : query.data?.items.length ? (
        <ul className="deliverable-step-links">
          {query.data.items.map((item) => (
            <li key={item.id}>
              <a href={roadmapDeliverablesPath(item.roadmapId, item.id)}>
                {item.title}
              </a>
              {item.roadmapId !== roadmapId && (
                <small className="muted">Owned by {item.roadmapTitle}</small>
              )}
              <span className={deliverableStatusClass(item.status)}>
                {statusText[item.status]}
              </span>
            </li>
          ))}
        </ul>
      ) : query.isLoading ? (
        <p className="muted">Loading deliverables…</p>
      ) : (
        <p className="muted">
          No deliverables linked to this step yet. Use Deliverables to link it.
        </p>
      )}
    </section>
  );
}

export function RoadmapDeliverables({
  roadmapId,
  selectedId,
  archivedRoadmap = false,
}: {
  /** The roadmap whose tab is open; it also scopes descendant rollup. */
  roadmapId: string;
  selectedId?: string;
  archivedRoadmap?: boolean;
}) {
  return selectedId ? (
    <DeliverableDetailView
      key={selectedId}
      roadmapId={roadmapId}
      deliverableId={selectedId}
      archivedRoadmap={archivedRoadmap}
    />
  ) : (
    <DeliverableListView roadmapId={roadmapId} archivedRoadmap={archivedRoadmap} />
  );
}

function DeliverableListView({
  roadmapId,
  archivedRoadmap,
}: {
  roadmapId: string;
  archivedRoadmap: boolean;
}) {
  const [scope, setScope] = useState<"tree" | "own">("tree");
  const [showArchived, setShowArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [title, setTitle] = useState("");
  const query = useData<DeliverablesAnswer>(
    listPath(roadmapId, scope, showArchived, offset),
  );
  const create = useWrites();
  const items = query.data?.items ?? [];
  const groups = new Map<string, { path: string; items: DeliverableSummary[] }>();
  for (const item of items) {
    const group = groups.get(item.roadmapId) ?? {
      path: item.roadmapPath.map((part) => part.title).join(" › "),
      items: [],
    };
    group.items.push(item);
    groups.set(item.roadmapId, group);
  }
  return (
    <div className="pane roadmap-view deliverables-view" role="region" aria-label="Roadmap deliverables">
      <header className="deliverables-header">
        <div>
          <h2>Deliverables</h2>
          <p className="muted">
            End products, checkable requirements, and the steps that produce them.
          </p>
        </div>
        {!showArchived && !archivedRoadmap && (
          <form
            className="quick-add deliverable-add"
            onSubmit={async (event) => {
              event.preventDefault();
              const value = title.trim();
              if (!value) return;
              await create
                .mutateAsync(() =>
              commands.createDeliverable(roadmapId, {
                title: value,
                description: "",
              }),
                )
                .then((detail) => {
                  setTitle("");
                  window.location.hash = roadmapDeliverablesPath(
                    roadmapId,
                    detail.id,
                  ).slice(1);
                })
                .catch(() => undefined);
            }}
          >
            <input
              aria-label="New deliverable title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="What should this roadmap produce?"
              maxLength={500}
              disabled={create.isPending}
              required
            />
            <button
              className="primary"
              disabled={create.isPending || !title.trim()}
            >
              Add deliverable
            </button>
          </form>
        )}
      </header>
      <ErrorMessage error={query.error || create.error} />
      {!showArchived && query.data && query.data.total > 0 && (
        <p className="deliverable-counts">{countsText(query.data.counts)}</p>
      )}
      <div className="toolbar deliverable-list-controls">
        <label>
          Show
          <select
            aria-label="Deliverable scope"
            value={scope}
            onChange={(event) => {
              setScope(event.target.value as "tree" | "own");
              setOffset(0);
            }}
          >
            <option value="tree">This roadmap and sub-roadmaps</option>
            <option value="own">This roadmap only</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => {
              setShowArchived(event.target.checked);
              setOffset(0);
            }}
          />{" "}
          Show archived
        </label>
      </div>
      {!query.data?.items.length ? (
        <Empty>
          {query.isLoading
            ? "Loading deliverables…"
            : showArchived
              ? "No archived deliverables in this scope."
              : "No deliverables yet. Add the end products and link the steps that produce them."}
        </Empty>
      ) : (
        <div className="deliverable-list">
          {[...groups.entries()].map(([ownerId, group]) => (
            <section className="deliverable-owner-group" key={ownerId}>
              {scope === "tree" && <h3>{group.path || "This roadmap"}</h3>}
              {group.items.map((item) => (
                <DeliverableCard key={item.id} item={item} />
              ))}
            </section>
          ))}
        </div>
      )}
      <Pager
        offset={offset}
        total={query.data?.total ?? 0}
        onChange={setOffset}
        label="deliverables"
      />
    </div>
  );
}

function DeliverableCard({ item }: { item: DeliverableSummary }) {
  return (
    <article className="deliverable-card">
      <div className="deliverable-card-main">
        <div className="deliverable-card-title">
          <a
            className="item-title"
            href={roadmapDeliverablesPath(item.roadmapId, item.id)}
          >
            {item.title}
          </a>
          <span className={deliverableStatusClass(item.status)}>
            {statusText[item.status]}
          </span>
          {item.archived && <span className="exec-pill">Archived</span>}
        </div>
        <small className="muted">
          {item.checkedCount} of {item.requirementCount} requirements checked
        </small>
      </div>
    </article>
  );
}

function DeliverableDetailView({
  roadmapId,
  deliverableId,
  archivedRoadmap,
}: {
  roadmapId: string;
  deliverableId: string;
  archivedRoadmap: boolean;
}) {
  const path = `/deliverables/${encodeURIComponent(deliverableId)}`;
  const query = useData<DeliverableDetail>(path);
  const action = useWrites();
  const [intent, setIntent] = useState<"accept" | "reopen" | null>(null);
  const [editing, setEditing] = useState(false);
  const [acceptanceNote, setAcceptanceNote] = useState("");
  const [reopenReason, setReopenReason] = useState("");
  const detail = query.data;
  const back = () => {
    window.location.hash = roadmapDeliverablesPath(roadmapId).slice(1);
  };
  const runAction = (
    verb: "submit" | "archive" | "recover",
    body: DeliverableExpectedBody,
  ) =>
    action.mutate(() =>
      verb === "submit"
        ? commands.submitDeliverable(deliverableId, body)
        : commands.setDeliverableArchived(deliverableId, verb === "archive", body),
    );

  if (!detail) {
    return (
      <div className="pane roadmap-view deliverables-view">
        <button className="quiet back" onClick={back}>All deliverables</button>
        <ErrorMessage error={query.error} />
        {query.isLoading && <p>Loading deliverable…</p>}
      </div>
    );
  }
  const readOnly = archivedRoadmap || detail.archived;
  const canEdit = !readOnly && detail.status === "planned";
  const ownerPath = detail.roadmapPath.map((part) => part.title).join(" › ");
  const checkedCount = detail.requirements.filter((requirement) => requirement.checked).length;
  const readyToSubmit =
    detail.requirements.length > 0 &&
    detail.requirements.every(
      (requirement) =>
        requirement.checked &&
        (requirement.verificationNote.trim() ||
          requirement.evidence.some((reference) => reference.availability === "available")),
    );

  return (
    <div className="pane roadmap-view deliverables-view" role="region" aria-label="Deliverable details">
      <button className="quiet back" onClick={back}>All deliverables</button>
      <ErrorMessage error={action.error} />
      <header className="deliverable-detail-header">
        <div>
          <div className="deliverable-card-title">
            <h2>{detail.title}</h2>
            <span className={deliverableStatusClass(detail.status)}>
              {statusText[detail.status]}
            </span>
            {detail.archived && <span className="exec-pill">Archived</span>}
          </div>
          {ownerPath && <p className="muted">Roadmap · {ownerPath}</p>}
          <p className="muted">
            {checkedCount} of {detail.requirements.length} requirements checked
          </p>
        </div>
        <div className="toolbar">
          {canEdit && !editing && (
            <button className="quiet" onClick={() => setEditing(true)}>
              Edit deliverable
            </button>
          )}
          {editing && (
            <button className="quiet" onClick={() => setEditing(false)}>
              Cancel edit
            </button>
          )}
          {detail.status === "planned" && !readOnly && !editing && (
            <button
              className="primary"
              disabled={action.isPending || !readyToSubmit}
              onClick={() => runAction("submit", { revision: detail.revision })}
            >
              Submit for review
            </button>
          )}
          {detail.status === "ready_for_review" && !readOnly && !editing && (
            <button
              className="primary"
              disabled={action.isPending || !readyToSubmit}
              onClick={() => setIntent("accept")}
            >
              Accept deliverable
            </button>
          )}
          {detail.status === "accepted" && !readOnly && !editing && (
            <button
              className="quiet"
              disabled={action.isPending}
              onClick={() => setIntent("reopen")}
            >
              Reopen deliverable
            </button>
          )}
          {detail.status === "ready_for_review" && !readOnly && !editing && (
            <button
              className="quiet"
              disabled={action.isPending}
              onClick={() => setIntent("reopen")}
            >
              Return to planned
            </button>
          )}
          {!readOnly && !editing ? (
            <button
              className="quiet"
              disabled={action.isPending}
              onClick={() => runAction("archive", { revision: detail.revision })}
            >
              Archive deliverable
            </button>
          ) : detail.archived && !archivedRoadmap ? (
            <button
              className="quiet"
              disabled={action.isPending}
              onClick={() => runAction("recover", { revision: detail.revision })}
            >
              Recover deliverable
            </button>
          ) : null}
        </div>
      </header>
      {(detail.status === "planned" || detail.status === "ready_for_review") &&
        !readOnly && !editing && !readyToSubmit && (
        <p className="muted deliverable-readiness-note">
          Check every requirement and add an available evidence reference or verification note before {detail.status === "planned" ? "submitting" : "accepting"}.
        </p>
      )}
      {intent === "accept" && (
        <form
          className="deliverable-action-form"
          onSubmit={(event) => {
            event.preventDefault();
            action
              .mutateAsync(() =>
                commands.acceptDeliverable(deliverableId, {
                  revision: detail.revision,
                  note: acceptanceNote.trim(),
                }),
              )
              .then(() => {
                setAcceptanceNote("");
                setIntent(null);
              })
              .catch(() => undefined);
          }}
        >
          <label>
            Acceptance note (optional)
            <textarea
              aria-label="Acceptance note"
              value={acceptanceNote}
              onChange={(event) => setAcceptanceNote(event.target.value)}
              maxLength={4000}
            />
          </label>
          <button className="primary" disabled={action.isPending}>
            Confirm acceptance
          </button>
          <button type="button" className="quiet" onClick={() => setIntent(null)}>
            Cancel
          </button>
        </form>
      )}
      {intent === "reopen" && (
        <form
          className="deliverable-action-form"
          onSubmit={(event) => {
            event.preventDefault();
            const reason = reopenReason.trim();
            if (!reason) return;
            action
              .mutateAsync(() =>
                commands.reopenDeliverable(deliverableId, {
                  revision: detail.revision,
                  reason,
                }),
              )
              .then(() => {
                setReopenReason("");
                setIntent(null);
              })
              .catch(() => undefined);
          }}
        >
          <label>
            {detail.status === "ready_for_review"
              ? "Reason for returning to planned"
              : "Reason for reopening"}
            <textarea
              aria-label={
                detail.status === "ready_for_review"
                  ? "Reason for returning to planned"
                  : "Reason for reopening"
              }
              value={reopenReason}
              onChange={(event) => setReopenReason(event.target.value)}
              maxLength={4000}
              required
            />
          </label>
          <button
            className="primary"
            disabled={action.isPending || !reopenReason.trim()}
          >
            {detail.status === "ready_for_review"
              ? "Confirm return to planned"
              : "Confirm reopening"}
          </button>
          <button type="button" className="quiet" onClick={() => setIntent(null)}>
            Cancel
          </button>
        </form>
      )}
      {editing ? (
        <DeliverableEditor
          key={`${detail.id}:${detail.revision}`}
          detail={detail}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <>
          {detail.description && (
            <section className="deliverable-section">
              <h3>Description</h3>
              <Markdown text={detail.description} />
            </section>
          )}
          <section className="deliverable-section">
            <div className="section-heading">
              <h3>Requirements</h3>
              <span className="count">{checkedCount}/{detail.requirements.length}</span>
            </div>
            {detail.requirements.length ? (
              <ul className="deliverable-requirements">
                {detail.requirements.map((requirement, index) => (
                  <li key={requirement.id}>
                    <label>
                      <input
                        type="checkbox"
                        aria-label={`Requirement ${index + 1} complete`}
                        checked={requirement.checked}
                        disabled={!canEdit || action.isPending}
                        onChange={() => {
                          const requirements = detail.requirements.map((current) =>
                            current.id === requirement.id
                              ? { ...current, checked: !current.checked }
                              : current,
                          );
                          action.mutate(() =>
                            commands.updateDeliverable(detail.id, {
                              revision: detail.revision,
                              requirements: requirements.map(requirementInput),
                            }),
                          );
                        }}
                      />
                      <span>{requirement.text}</span>
                    </label>
                    {requirement.verificationNote && (
                      <p className="muted">{requirement.verificationNote}</p>
                    )}
                    {requirement.evidence.length > 0 && (
                      <ReferenceLinks references={requirement.evidence} />
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No requirements have been added.</p>
            )}
          </section>
          <section className="deliverable-section">
            <h3>Delivery steps</h3>
            {detail.steps.length ? (
              <ul className="deliverable-step-links">
                {detail.steps.map((step) => (
                  <li key={step.id}>
                    {step.available ? (
                      <a href={`#roadmaps/${step.roadmapId}/${step.id}`}>{step.title}</a>
                    ) : (
                      <span>{step.title} <small className="muted">(unavailable)</small></span>
                    )}
                    <small className="muted">{step.roadmapTitle}</small>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No steps are linked to this deliverable.</p>
            )}
          </section>
          <ReferenceSection title="Inputs" references={detail.inputs} />
          <ReferenceSection title="Outputs" references={detail.outputs} />
          <DeliverableHistory detail={detail} />
        </>
      )}
    </div>
  );
}

function DeliverableEditor({
  detail,
  onCancel,
}: {
  detail: DeliverableDetail;
  onCancel: () => void;
}) {
  const action = useWrites();
  const [title, setTitle] = useState(detail.title);
  const [description, setDescription] = useState(detail.description);
  const [stepIds, setStepIds] = useState(detail.stepIds);
  const [steps, setSteps] = useState(detail.steps);
  const [requirements, setRequirements] = useState(() =>
    detail.requirements.map(editableRequirement),
  );
  const [inputs, setInputs] = useState(() => detail.inputs.map(editableReference));
  const [outputs, setOutputs] = useState(() => detail.outputs.map(editableReference));

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const body: DeliverableUpdateBody = {
      revision: detail.revision,
      title: title.trim(),
      description,
      stepIds,
      requirements: requirements.map(requirementInput),
      inputs: inputs.map(referenceInput),
      outputs: outputs.map(referenceInput),
    };
    await action
      .mutateAsync(() => commands.updateDeliverable(detail.id, body))
      .then(onCancel)
      .catch(() => undefined);
  };

  const addRequirement = () => {
    setRequirements((current) => [
      ...current,
      {
        draftId: `new-${crypto.randomUUID()}`,
        text: "",
        checked: false,
        verificationNote: "",
        evidence: [],
      },
    ]);
  };

  return (
    <form className="deliverable-editor" onSubmit={save}>
      <h3>Edit deliverable</h3>
      <ErrorMessage error={action.error} />
      <label>
        Title
        <input
          aria-label="Deliverable title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          required
          maxLength={500}
        />
      </label>
      <label>
        Description (Markdown)
        <textarea
          aria-label="Deliverable description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={4}
          maxLength={10000}
        />
      </label>
      <section className="deliverable-editor-section">
        <div className="section-heading">
          <h4>Requirements</h4>
          <button type="button" className="quiet" onClick={addRequirement}>
            Add requirement
          </button>
        </div>
        {requirements.map((requirement, index) => (
          <div className="deliverable-requirement-editor" key={requirement.draftId}>
            <label>
              Requirement {index + 1}
              <input
                aria-label={`Requirement ${index + 1}`}
                value={requirement.text}
                onChange={(event) =>
                  setRequirements((current) =>
                    current.map((row) =>
                      row.draftId === requirement.draftId
                        ? { ...row, text: event.target.value }
                        : row,
                    ),
                  )
                }
                required
                maxLength={2000}
              />
            </label>
            <label className="deliverable-check-label">
              <input
                type="checkbox"
                aria-label={`Requirement ${index + 1} complete`}
                checked={requirement.checked ?? false}
                onChange={(event) =>
                  setRequirements((current) =>
                    current.map((row) =>
                      row.draftId === requirement.draftId
                        ? { ...row, checked: event.target.checked }
                        : row,
                    ),
                  )
                }
              />
              Complete
            </label>
            <label>
              Verification note
              <textarea
                aria-label={`Requirement ${index + 1} verification note`}
                value={requirement.verificationNote}
                onChange={(event) =>
                  setRequirements((current) =>
                    current.map((row) =>
                      row.draftId === requirement.draftId
                        ? { ...row, verificationNote: event.target.value }
                        : row,
                    ),
                  )
                }
                rows={2}
                maxLength={4000}
              />
            </label>
            <ReferenceListEditor
              roadmapId={detail.roadmapId}
              title={`Evidence for requirement ${index + 1}`}
              addLabel="Add evidence"
              values={requirement.evidence}
              onChange={(evidence) =>
                setRequirements((current) =>
                  current.map((row) =>
                    row.draftId === requirement.draftId
                      ? { ...row, evidence }
                      : row,
                  ),
                )
              }
            />
            <button
              type="button"
              className="quiet"
              onClick={() =>
                setRequirements((current) =>
                  current.filter((row) => row.draftId !== requirement.draftId),
                )
              }
            >
              Remove requirement {index + 1}
            </button>
          </div>
        ))}
      </section>
      <section className="deliverable-editor-section">
        <h4>Delivery steps</h4>
        {steps.map((step) => (
          <div className="deliverable-step-choice" key={step.id}>
            <span>
              {step.title} <small className="muted">· {step.roadmapTitle}{!step.available ? " · unavailable" : ""}</small>
            </span>
            <button
              type="button"
              className="quiet"
              aria-label={`Unlink step ${step.title}`}
              onClick={() => {
                setStepIds((current) => current.filter((id) => id !== step.id));
                setSteps((current) => current.filter((currentStep) => currentStep.id !== step.id));
              }}
            >
              Unlink
            </button>
          </div>
        ))}
        <StepPicker
          roadmapId={detail.roadmapId}
          selectedIds={stepIds}
          onAdd={(step) => {
            setStepIds((current) => [...current, step.id]);
            setSteps((current) => [...current, step]);
          }}
        />
      </section>
      <section className="deliverable-editor-section">
        <h4>Inputs</h4>
        <ReferenceListEditor
          roadmapId={detail.roadmapId}
          title="Inputs"
          addLabel="Add input"
          values={inputs}
          onChange={setInputs}
        />
      </section>
      <section className="deliverable-editor-section">
        <h4>Outputs</h4>
        <ReferenceListEditor
          roadmapId={detail.roadmapId}
          title="Outputs"
          addLabel="Add output"
          values={outputs}
          onChange={setOutputs}
        />
      </section>
      <div className="toolbar">
        <button className="primary" disabled={action.isPending || !title.trim()}>
          Save deliverable
        </button>
        <button type="button" className="quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function StepPicker({
  roadmapId,
  selectedIds,
  onAdd,
}: {
  roadmapId: string;
  selectedIds: string[];
  onAdd: (step: DeliverableStep) => void;
}) {
  const [query, setQuery] = useState("");
  const options = useQuery({
    queryKey: [getDataContext(), "deliverable-step-options", roadmapId, query],
    queryFn: () =>
      request<DeliverableOptionsAnswer>(
        `/roadmaps/${encodeURIComponent(roadmapId)}/deliverable-options?kind=step&q=${encodeURIComponent(query)}&limit=${PAGE}&offset=0`,
      ),
  });
  return (
    <div className="deliverable-picker">
      <label>
        Find steps to link
        <input
          aria-label="Find steps to link"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search this roadmap and its sub-roadmaps"
        />
      </label>
      <ErrorMessage error={options.error} />
      {options.data?.items.map((option) => (
        <div className="deliverable-option" key={option.id}>
          <span>{option.title}<small className="muted">{option.detail}</small></span>
          <button
            type="button"
            className="quiet"
            disabled={selectedIds.includes(option.id)}
            onClick={() =>
              onAdd({
                id: option.id,
                title: option.title,
                roadmapId,
                roadmapTitle: option.detail,
                available: true,
              })
            }
          >
            {selectedIds.includes(option.id) ? "Linked" : "Link step"}
          </button>
        </div>
      ))}
      {options.data?.total === 0 && <p className="muted">No matching steps.</p>}
    </div>
  );
}

function ReferenceListEditor({
  roadmapId,
  title,
  addLabel,
  values,
  onChange,
}: {
  roadmapId: string;
  title: string;
  addLabel: string;
  values: EditableReference[];
  onChange: (next: EditableReference[]) => void;
}) {
  const [kind, setKind] = useState<WritableReferenceKind>("url");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [fileError, setFileError] = useState("");
  const upload = useWrites();
  const options = useQuery({
    queryKey: [getDataContext(), "deliverable-reference-options", roadmapId, kind, query],
    enabled: kind !== "url",
    queryFn: () =>
      request<DeliverableOptionsAnswer>(
        `/roadmaps/${encodeURIComponent(roadmapId)}/deliverable-options?kind=${kind}&q=${encodeURIComponent(query)}&limit=${PAGE}&offset=0`,
      ),
  });
  const selected = options.data?.items.find((option) => option.id === selectedId);
  const writableLabel = label.trim() || selected?.title || url.trim();

  function addReference() {
    if (kind === "url") {
      if (!validHttpUrl(url)) return;
      onChange([
        ...values,
        { draftId: `new-${crypto.randomUUID()}`, kind, url: url.trim(), label: writableLabel },
      ]);
      setUrl("");
      setLabel("");
      return;
    }
    if (!selected) return;
    onChange([
      ...values,
      {
        draftId: `new-${crypto.randomUUID()}`,
        kind,
        id: selected.id,
        label: writableLabel,
      },
    ]);
    setSelectedId("");
    setQuery("");
    setLabel("");
  }

  async function uploadFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 25000000) {
      setFileError("Files must be 25 MB or smaller.");
      event.target.value = "";
      return;
    }
    setFileError("");
    await upload
      .mutateAsync(() => commands.uploadAttachment("roadmap", roadmapId, file))
      .then((attachment) => {
        const fileRecord = attachment as Attachment;
        onChange([
          ...values,
          {
            draftId: `new-${crypto.randomUUID()}`,
            kind: "attachment",
            id: fileRecord.id,
            label: fileRecord.name,
          },
        ]);
      })
      .catch(() => undefined);
    event.target.value = "";
  }

  return (
    <div className="deliverable-reference-editor" role="group" aria-label={title}>
      <div className="deliverable-reference-list">
        {values.map((reference, index) => (
          <div className="deliverable-reference-row" key={reference.draftId}>
            <div>
              <strong>{reference.label || reference.url || "Reference"}</strong>
              <small className="muted">{reference.kind}</small>
              {reference.availability === "changed" && (
                <label className="deliverable-refresh-reference">
                  <input
                    type="checkbox"
                    aria-label={`Refresh changed reference ${index + 1}`}
                    checked={!!reference.refresh}
                    onChange={(event) =>
                      onChange(
                        values.map((item) =>
                          item.draftId === reference.draftId
                            ? { ...item, refresh: event.target.checked }
                            : item,
                        ),
                      )
                    }
                  />
                  Refresh to the current target on save
                </label>
              )}
            </div>
            <button
              type="button"
              className="quiet"
              aria-label={`Remove ${title.toLowerCase()} reference ${index + 1}`}
              onClick={() =>
                onChange(values.filter((item) => item.draftId !== reference.draftId))
              }
            >
              Remove
            </button>
          </div>
        ))}
      </div>
      <div className="deliverable-reference-add">
        <label>
          Reference type
          <select
            aria-label={`${title} reference type`}
            value={kind}
            onChange={(event) => {
              setKind(event.target.value as WritableReferenceKind);
              setSelectedId("");
              setLabel("");
            }}
          >
            <option value="url">External URL</option>
            <option value="repository">Repository</option>
            <option value="resource">Resource page</option>
            <option value="attachment">File</option>
          </select>
        </label>
        {kind === "url" ? (
          <>
            <label>
              URL
              <input
                aria-label={`${title} URL`}
                type="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://…"
              />
            </label>
            <label>
              Label (optional)
              <input
                aria-label={`${title} label`}
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                maxLength={500}
              />
            </label>
          </>
        ) : (
          <>
            <label>
              Find {kind === "resource" ? "resource pages" : kind === "repository" ? "repositories" : "files"}
              <input
                aria-label={`Find ${kind === "resource" ? "resource pages" : kind === "repository" ? "repositories" : "files"}`}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search…"
              />
            </label>
            <label>
              Choose reference
              <select
                aria-label={`Choose ${kind} reference`}
                value={selectedId}
                onChange={(event) => setSelectedId(event.target.value)}
              >
                <option value="">Choose a {kind === "resource" ? "resource page" : kind}</option>
                {options.data?.items.map((option: DeliverableOption) => (
                  <option key={option.id} value={option.id}>
                    {option.title}{option.detail ? ` · ${option.detail}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Label (optional)
              <input
                aria-label={`${title} label`}
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                maxLength={500}
              />
            </label>
          </>
        )}
        <button
          type="button"
          onClick={addReference}
          disabled={kind === "url" ? !validHttpUrl(url) : !selected}
        >
          {addLabel}
        </button>
        {kind === "attachment" && (
          <label className="file-upload deliverable-file-upload">
            Attach a file
            <input type="file" onChange={uploadFile} disabled={upload.isPending} />
          </label>
        )}
        <ErrorMessage error={fileError || upload.error || options.error} />
      </div>
    </div>
  );
}

function ReferenceSection({
  title,
  references,
}: {
  title: string;
  references: DeliverableReference[];
}) {
  return (
    <section className="deliverable-section">
      <h3>{title}</h3>
      {references.length ? (
        <ReferenceLinks references={references} />
      ) : (
        <p className="muted">No {title.toLowerCase()} linked.</p>
      )}
    </section>
  );
}

function ReferenceLinks({ references }: { references: DeliverableReference[] }) {
  return (
    <ul className="deliverable-reference-links">
      {references.map((reference, index) => (
        <li key={`${reference.kind}:${reference.id ?? reference.url ?? index}`}>
          {reference.href ? (
            <a href={reference.href}>{reference.label || reference.url || "Open reference"}</a>
          ) : (
            <span>{reference.label || reference.url || "Reference unavailable"}</span>
          )}
          <small className={`muted reference-${reference.availability}`}>
            {[
              reference.availability === "available"
                ? reference.kind
                : reference.availability === "changed"
                  ? "Changed since linked"
                  : "Unavailable",
              reference.sourceRevision != null
                ? `source revision ${reference.sourceRevision}`
                : null,
              reference.sha256 ? `SHA-256 ${reference.sha256}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </small>
        </li>
      ))}
    </ul>
  );
}

function DeliverableHistory({
  detail,
}: {
  detail: DeliverableDetail;
}) {
  const [offset, setOffset] = useState(0);
  const historyQuery = useData<DeliverableHistoryAnswer>(
    `/deliverables/${encodeURIComponent(detail.id)}/history?limit=${PAGE}&offset=${offset}`,
  );
  const history = historyQuery.data;
  return (
    <section className="deliverable-section">
      <h3>History</h3>
      <ErrorMessage error={historyQuery.error} />
      {!history?.items.length && !history?.events.length ? (
        <p className="muted">No history yet.</p>
      ) : (
        <>
          <ol className="deliverable-history">
            {history.items.map((item) => (
              <DeliverableHistoryRow key={item.revision} item={item} />
            ))}
          </ol>
          {history.events.length > 0 && (
            <div className="deliverable-history-events">
              <h4>Acceptance and lifecycle events</h4>
              {history.events.map((event, index) => (
                <HistoryEvent key={`${event.createdAt}:${index}`} event={event} />
              ))}
            </div>
          )}
        </>
      )}
      <Pager
        offset={offset}
        total={history?.total ?? 0}
        onChange={setOffset}
        label="acceptance snapshots"
      />
      <small className="muted">Current revision {detail.revision}</small>
    </section>
  );
}

function DeliverableHistoryRow({ item }: { item: DeliverableHistoryItem }) {
  const snapshot = item.snapshot;
  return (
    <li className="deliverable-history-row">
      <details>
        <summary>
          {item.acceptedAt ? "Acceptance snapshot" : "Saved snapshot"} · revision {item.revision}
          {item.acceptedAt && ` · ${formatWhen(item.acceptedAt)}`}
        </summary>
        <p className="muted">
          {item.actor.label} ({item.actor.kind}) · {item.note || "No note"}
          {` · ${formatWhen(item.acceptedAt)}`}
          {item.proposalId && <> · <a href={`#review/${item.proposalId}`}>Proposal</a></>}
        </p>
        <h4>{snapshot.title}</h4>
        {snapshot.description && <Markdown text={snapshot.description} />}
        <h5>Requirements at revision {item.revision}</h5>
        {snapshot.requirements.length ? (
          <ul>
            {snapshot.requirements.map((requirement) => (
              <li key={requirement.id}>
                {requirement.checked ? "✓" : "○"} {requirement.text}
                {requirement.verificationNote && ` — ${requirement.verificationNote}`}
                {requirement.evidence.length > 0 && (
                  <div>
                    <small>Evidence</small>
                    <ReferenceLinks references={requirement.evidence} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No requirements.</p>
        )}
        <h5>Linked steps</h5>
        {snapshot.steps.length ? (
          <ul>
            {snapshot.steps.map((step) => (
              <li key={step.id}>
                {step.available ? (
                  <a href={`#roadmaps/${step.roadmapId}/${step.id}`}>{step.title}</a>
                ) : (
                  <span>{step.title} (unavailable)</span>
                )}
                <small className="muted"> · {step.roadmapTitle}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No linked steps.</p>
        )}
        <h5>Inputs</h5>
        {snapshot.inputs.length ? (
          <ReferenceLinks references={snapshot.inputs} />
        ) : (
          <p className="muted">No inputs.</p>
        )}
        <h5>Outputs</h5>
        {snapshot.outputs.length ? (
          <ReferenceLinks references={snapshot.outputs} />
        ) : (
          <p className="muted">No outputs.</p>
        )}
      </details>
    </li>
  );
}

function HistoryEvent({ event }: { event: DeliverableHistoryEvent }) {
  const labels: Record<string, string> = {
    "deliverable.create": "Created",
    "deliverable.update": "Edited",
    "deliverable.submit": "Submitted for review",
    "deliverable.accept": "Accepted",
    "deliverable.reopen": "Reopened",
    "deliverable.archive": "Archived",
    "deliverable.recover": "Recovered",
  };
  return (
    <p className="muted">
      <strong>{labels[event.kind] ?? event.kind.replaceAll("_", " ")}</strong> · {event.actorLabel} · {formatWhen(event.createdAt)}
      {event.reason && ` · ${event.reason}`}
    </p>
  );
}

function editableReference(reference: DeliverableReference): EditableReference {
  return {
    draftId: `ref-${reference.id ?? reference.url ?? crypto.randomUUID()}`,
    kind: reference.kind,
    ...(reference.id ? { id: reference.id } : {}),
    ...(reference.url ? { url: reference.url } : {}),
    label: reference.label,
    availability: reference.availability,
    href: reference.href,
  };
}

function editableRequirement(requirement: DeliverableRequirement): EditableRequirement {
  return {
    id: requirement.id,
    draftId: requirement.id,
    text: requirement.text,
    checked: requirement.checked,
    verificationNote: requirement.verificationNote,
    evidence: requirement.evidence.map(editableReference),
  };
}

function referenceInput(reference: EditableReference): DeliverableReferenceInput {
  if (reference.kind === "url") {
    if (!reference.url) throw new Error("A URL reference needs a URL.");
    return {
      kind: "url",
      url: reference.url,
      label: reference.label || reference.url,
    };
  }
  if (!reference.id) throw new Error("A record reference needs its record ID.");
  return {
    kind: reference.kind,
    id: reference.id,
    label: reference.label || reference.id,
    refresh: !!reference.refresh,
  };
}

function requirementInput(requirement: DeliverableRequirement | EditableRequirement): DeliverableRequirementInput {
  return {
    ...(requirement.id ? { id: requirement.id } : {}),
    text: requirement.text,
    checked: requirement.checked,
    verificationNote: requirement.verificationNote ?? "",
    evidence: requirement.evidence.map((reference) =>
      referenceInput("draftId" in reference ? reference : editableReference(reference)),
    ),
  };
}

function validHttpUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

function formatWhen(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
