import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request, getDataContext } from "./api";
import { useAction, useData } from "./hooks";
import { Empty, ErrorMessage, Files, Markdown } from "./components";
import type { Day, List, Proposal } from "./types";
export function Review({ selectedId }: { selectedId?: string }) {
  const [status, setStatus] = useState("submitted");
  const proposals = useData<List<Proposal>>(
    "/proposals" + (status ? "?status=" + status : ""),
  );
  const selected = selectedId ?? "";
  return (
    <>
      <header>
        <p className="eyebrow">Human judgment, recorded</p>
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
                {p.actorLabel} · {p.operations.length} proposed changes
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
  return (
    <section>
      <button className="quiet" onClick={onBack}>
        ← Proposal list
      </button>
      <h2>{p.title}</h2>
      <p className="muted">
        Proposed by {p.actorLabel} · {p.status} · revision {p.revision}
      </p>
      <Markdown text={p.rationale} />
      <h3>Proposed changes</h3>
      {p.operations.map((op, index) => (
        <OperationDiff key={index} action={op.action} payload={op.payload} />
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
function OperationDiff({
  action,
  payload,
}: {
  action: string;
  payload: Record<string, unknown>;
}) {
  const target = String(
    payload.id ?? payload.itemId ?? payload.roadmapId ?? payload.stepId ?? "",
  );
  const current = useQuery({
    queryKey: [
      getDataContext(),
      "proposal-current",
      action,
      target,
      payload.date,
    ],
    enabled: !!target && !action.endsWith(".create"),
    queryFn: async () => {
      if (action === "entry.upsert") {
        const day = await request<Day>("/days/" + payload.date);
        return (
          (day.entries.find((e) => e.itemId === target) as unknown as Record<
            string,
            unknown
          >) ?? null
        );
      }
      if (action === "progress.set") {
        return request<Record<string, unknown>>("/steps/" + target);
      }
      const collection = action.startsWith("roadmap.")
        ? "roadmaps"
        : action.startsWith("note.")
          ? "notes"
          : action.startsWith("task.")
            ? "tasks"
            : "work-items";
      return request<Record<string, unknown>>("/" + collection + "/" + target);
    },
  });
  const before = current.data;
  return (
    <article className="operation">
      <h4>{action}</h4>
      <ErrorMessage error={current.error} />
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
                  : action.endsWith(".create")
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
