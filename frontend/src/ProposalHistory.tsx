import { useState } from "react";
import { useData } from "./hooks";
import { ErrorMessage } from "./components";
import { Pages } from "./Coordination";
import type { List, ProposalEntry } from "./types";

const PAGE = 50;

// An automatic approval was the Settings choice, not the person's decision.
export function statusLabel(p: { autoApproved: boolean; status: string }) {
  return p.autoApproved ? "approved automatically" : p.status;
}
function timestamp(value: string) {
  return <time dateTime={value}>{new Date(value).toLocaleString()}</time>;
}
const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
// When a proposal got where it is now: applied, decided, withdrawn, submitted or drafted.
function reached(entry: ProposalEntry) {
  if (entry.status === "approved")
    return <>Applied {timestamp(entry.appliedAt ?? entry.updatedAt)}</>;
  if (entry.status === "rejected")
    return <>Rejected {timestamp(entry.updatedAt)}</>;
  if (entry.status === "withdrawn")
    return <>Withdrawn {timestamp(entry.updatedAt)}</>;
  if (entry.status === "submitted")
    return <>Submitted {timestamp(entry.submittedAt ?? entry.updatedAt)}</>;
  return <>Drafted {timestamp(entry.createdAt)}</>;
}
// The changes a proposal made to this record, numbered as its page on Review numbers them.
function changesHere(entry: ProposalEntry) {
  const changes = entry.changes ?? [];
  const actions = [...new Set(changes.map((c) => c.action))].join(", ");
  const of =
    entry.operationCount >= changes.length ? ` of ${entry.operationCount}` : "";
  return `${changes.length === 1 ? "Change" : "Changes"} ${changes
    .map((c) => c.number)
    .join(", ")}${of}: ${actions}`;
}
function Entries({
  entries,
  label,
  record,
}: {
  entries: ProposalEntry[];
  label: string;
  // A record's history names who proposed each and what it changed there.
  record: boolean;
}) {
  return (
    <ol className="proposal-list" aria-label={label}>
      {entries.map((entry) => (
        <li className="line proposal-entry" key={entry.id}>
          <span className="proposal-number">{entry.number}.</span>
          <a className="item-title" href={"#review/" + entry.id}>
            {entry.title}
          </a>
          {/* Everything a record's history lists was applied, so only an automatic approval needs saying. */}
          {(!record || entry.autoApproved) && (
            <span className="badge">{statusLabel(entry)}</span>
          )}
          <p>
            {record && (
              <>
                {entry.agentId ? (
                  <a href={"#agents/" + entry.agentId}>{entry.actorLabel}</a>
                ) : (
                  entry.actorLabel
                )}{" "}
                ·{" "}
              </>
            )}
            {reached(entry)} ·{" "}
            {record
              ? changesHere(entry)
              : count(entry.operationCount, "proposed change")}
          </p>
        </li>
      ))}
    </ol>
  );
}
/**
 * The proposals applied to a work item or roadmap, the latest first, each numbered by the
 * order it was applied in. It folds beside the record's activity history.
 */
export function AppliedProposals({
  path,
  noun,
}: {
  // The record's own path, such as /work-items/ID.
  path: string;
  // What the record is, for the empty list: "work item" or "roadmap".
  noun: string;
}) {
  const [offset, setOffset] = useState(0);
  const query = useData<List<ProposalEntry>>(
    `${path}/proposals?limit=${PAGE}&offset=${offset}`,
  );
  const entries = query.data?.items ?? [];
  return (
    <details className="proposal-history">
      <summary>
        Applied proposals
        {query.data ? ` (${query.data.total})` : ""}
      </summary>
      <ErrorMessage error={query.error} />
      {query.isPending ? (
        <p>Loading proposals…</p>
      ) : !query.error && !entries.length ? (
        <p className="muted">No agent proposal has changed this {noun} yet.</p>
      ) : (
        <p className="muted">
          The latest first, numbered in the order they were applied. Each names
          the changes it made here; open one to see them all.
        </p>
      )}
      <Entries entries={entries} label="Applied proposals" record />
      <Pages
        offset={offset}
        total={query.data?.total ?? 0}
        change={setOffset}
        label="proposals"
      />
    </details>
  );
}
/** Everything an agent proposed, under any of its credentials, the latest first. */
export function AgentProposals({ id }: { id: string }) {
  const [offset, setOffset] = useState(0);
  const query = useData<List<ProposalEntry>>(
    `/agents/${encodeURIComponent(id)}/proposals?limit=${PAGE}&offset=${offset}`,
  );
  const entries = query.data?.items ?? [];
  return (
    <section aria-label="Proposals">
      <h2>Proposals</h2>
      <ErrorMessage error={query.error} />
      {query.isPending ? (
        <p>Loading proposals…</p>
      ) : !query.error && !entries.length ? (
        <p className="muted">No proposals yet.</p>
      ) : (
        <p className="muted">
          The latest first, numbered in the order this agent made them.
        </p>
      )}
      <Entries
        entries={entries}
        label="Proposals from this agent"
        record={false}
      />
      <Pages
        offset={offset}
        total={query.data?.total ?? 0}
        change={setOffset}
        label="proposals"
      />
    </section>
  );
}
