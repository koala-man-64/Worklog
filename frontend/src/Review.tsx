import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, request, getDataContext } from "./api";
import { useAction, useData } from "./hooks";
import { Empty, ErrorMessage, Files, Markdown } from "./components";
import type { Day, List, Operation, Proposal } from "./types";
import { Icon } from "./icons";
export function Review({ selectedId }: { selectedId?: string }) {
  const [status, setStatus] = useState("submitted");
  const proposals = useData<List<Proposal>>(
    "/proposals" + (status ? "?status=" + status : ""),
  );
  const selected = selectedId ?? "";
  return (
    <>
      <header>
        <h1>Review</h1>
        <p className="lede">
          Inspect agent suggestions before they change your work.
        </p>
      </header>
      <div className="toolbar">
        <label>
          Proposal status
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              location.hash = "review";
            }}
          >
            {[
              "submitted",
              "draft",
              "approved",
              "rejected",
              "withdrawn",
              "",
            ].map((s) => (
              <option value={s} key={s}>
                {s || "All proposals"}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorMessage error={proposals.error} />
      {selected ? (
        <ProposalDetail
          id={selected}
          onBack={() => {
            location.hash = "review";
          }}
        />
      ) : !proposals.data?.items.length ? (
        <Empty>
          No {status} proposals. Your canonical work stays under your control.
        </Empty>
      ) : (
        proposals.data.items.map((p) => (
          <article className="roadmap-row" key={p.id}>
            <div>
              <a className="text-button item-title" href={"#review/" + p.id}>
                {p.title}
              </a>
              <p>
                {p.actorLabel}, {p.operations.length} proposed changes
              </p>
            </div>
            <span className="badge">{p.status}</span>
          </article>
        ))
      )}
    </>
  );
}
function ProposalDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const query = useData<Proposal>("/proposals/" + id);
  const action = useAction();
  const [decision, setDecision] = useState<"approve" | "reject" | null>(null);
  const p = query.data;
  if (!p)
    return (
      <>
        <ErrorMessage error={query.error} />
        <p>Loading proposal…</p>
      </>
    );
  const declared = proposedSteps(p.operations);
  return (
    <section>
      <button className="quiet" onClick={onBack}>
        <Icon name="back" />
        Proposal list
      </button>
      <h2>{p.title}</h2>
      <p className="muted">
        Proposed by {p.actorLabel}, {p.status}, revision {p.revision}
      </p>
      <Markdown text={p.rationale} />
      <h3>Proposed changes</h3>
      {p.operations.map((op, index) => (
        <OperationDiff
          key={index}
          action={op.action}
          payload={op.payload}
          proposedEarlier={(target) => (declared.get(target) ?? index) < index}
        />
      ))}
      <Files
        ownerType="proposal"
        ownerId={id}
        files={p.attachments ?? []}
        readOnly
      />
      <ErrorMessage error={action.error} />
      {p.status === "submitted" && (
        <div className="decision">
          <p>
            Approval applies all operations together. If any target has changed,
            the server keeps the proposal pending.
          </p>
          {decision ? (
            <>
              <strong>
                {decision === "approve"
                  ? "Apply all of these changes?"
                  : "Reject this proposal?"}
              </strong>
              <div className="toolbar">
                <button
                  className={decision === "approve" ? "primary" : "danger"}
                  disabled={action.isPending}
                  onClick={async () => {
                    await action
                      .mutateAsync({
                        path: `/proposals/${id}/${decision}`,
                        body: { revision: p.revision },
                      })
                      .then(() => setDecision(null))
                      .catch(() => undefined);
                  }}
                >
                  Confirm {decision}
                </button>
                <button onClick={() => setDecision(null)}>
                  Keep reviewing
                </button>
              </div>
            </>
          ) : (
            <div className="toolbar">
              <button
                className="primary"
                onClick={() => setDecision("approve")}
              >
                Approve proposal
              </button>
              <button onClick={() => setDecision("reject")}>
                Reject proposal
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
// Agents choose step IDs in roadmap.create and roadmap.revise, so a later
// operation may target a step that exists only once the proposal is approved.
// Maps each listed step ID to the first operation that lists it.
function proposedSteps(operations: Operation[]) {
  const first = new Map<string, number>();
  operations.forEach(({ action, payload }, index) => {
    if (action !== "roadmap.create" && action !== "roadmap.revise") return;
    if (!Array.isArray(payload.steps)) return;
    for (const step of payload.steps as unknown[]) {
      const id = (step as { id?: unknown } | null)?.id;
      if (typeof id === "string" && !first.has(id)) first.set(id, index);
    }
  });
  return first;
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
// Proposal payloads are agent-controlled, so only well-formed IDs and dates may
// become URL path segments. Returns null when the target cannot be fetched safely.
function currentPath(
  action: string,
  payload: Record<string, unknown>,
  target: string,
): string | null {
  if (!UUID.test(target)) return null;
  const id = encodeURIComponent(target);
  if (action === "entry.upsert") {
    const date = String(payload.date ?? "");
    return DATE.test(date) ? "/days/" + encodeURIComponent(date) : null;
  }
  if (action === "progress.set") return "/steps/" + id;
  if (action.startsWith("repository.")) return "/repositories/" + id;
  const collection = action.startsWith("roadmap.")
    ? "roadmaps"
    : action.startsWith("person.")
      ? "people"
      : action.startsWith("note.")
        ? "notes"
        : action.startsWith("task.")
          ? "tasks"
          : action.startsWith("link.")
            ? "links"
            : action.startsWith("chat.")
              ? "chats"
              : action.startsWith("github.")
                ? "github"
                : action.startsWith("question.")
                  ? "questions"
                  : action.startsWith("decision.")
                    ? "decisions"
                    : "work-items";
  return "/" + collection + "/" + id;
}
export function OperationDiff({
  action,
  payload,
  proposedEarlier,
}: {
  action: string;
  payload: Record<string, unknown>;
  // True when an earlier operation in the same proposal creates the target.
  proposedEarlier?: (target: string) => boolean;
}) {
  const target = String(
    payload.id ?? payload.itemId ?? payload.roadmapId ?? payload.stepId ?? "",
  );
  const lookup = !!target && !action.endsWith(".create");
  const path = lookup ? currentPath(action, payload, target) : null;
  const current = useQuery({
    queryKey: [
      getDataContext(),
      "proposal-current",
      action,
      target,
      payload.date,
    ],
    enabled: path !== null,
    queryFn: async () => {
      if (path === null) return null;
      if (action === "entry.upsert") {
        const day = await request<Day>(path);
        return (
          (day.entries.find((e) => e.itemId === target) as unknown as Record<
            string,
            unknown
          >) ?? null
        );
      }
      return request<Record<string, unknown>>(path);
    },
  });
  const before = current.data;
  // Not found is expected only for a target that this proposal creates first;
  // any other missing target stays an error, since approval would fail on it.
  const created =
    current.error instanceof ApiError &&
    current.error.status === 404 &&
    !!proposedEarlier?.(target);
  return (
    <article className="operation">
      <h4>{action}</h4>
      {created ? (
        <p className="muted">
          An earlier change in this proposal creates this target, so it has no
          current value until approval.
        </p>
      ) : (
        <ErrorMessage error={current.error} />
      )}
      {lookup && path === null && (
        <p className="warning">
          Malformed target: this operation’s ID or date is not valid, so its
          current value was not loaded.
        </p>
      )}
      {before &&
        payload.revision !== undefined &&
        before.revision !== payload.revision && (
          <p className="warning">
            This target changed: expected revision {String(payload.revision)},
            current revision {String(before.revision)}. Approval will be checked
            by the server.
          </p>
        )}
      {Object.entries(payload).map(([key, value]) => (
        <div className="diff-row" key={key}>
          <strong>{key}</strong>
          <div>
            <small>Current</small>
            <pre>
              {before && key in before
                ? display(before[key])
                : current.isFetching
                  ? "Loading…"
                  : action.endsWith(".create") || created
                    ? "New record"
                    : "No current value"}
            </pre>
          </div>
          <div className="diff-after">
            <small>Proposed</small>
            <pre>{display(value)}</pre>
          </div>
        </div>
      ))}
    </article>
  );
}
function display(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
