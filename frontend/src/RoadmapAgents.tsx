import { useState } from "react";
import { Avatar } from "./Avatar";
import {
  exactDiscussionDate,
  formatDiscussionDate,
} from "./discussionFormatting";
import { REASONS, REASON_ORDER } from "./tieReasons";
import { ErrorMessage } from "./ui/Messages";
import type { RoadmapTie, RoadmapWorker, RosterAgent } from "./apiTypes";

/** A worker with the ties that place it on one part of a roadmap. */
export type Placed = { worker: RoadmapWorker; ties: RoadmapTie[] };

const quiet = (ties: RoadmapTie[]) => ties.every((t) => t.stale);
const latest = (ties: RoadmapTie[]) =>
  ties.reduce((at, t) => (t.lastAt && t.lastAt > at ? t.lastAt : at), "");

/**
 * The workers tied to any of `steps` (null is the roadmap itself), or anywhere
 * without it, each with only those ties: fresh work first, then the most recent.
 */
export function workersOn(
  workers: RoadmapWorker[],
  steps?: ReadonlySet<string | null>,
): Placed[] {
  return workers
    .map((worker) => ({
      worker,
      ties: steps
        ? worker.ties.filter((t) => steps.has(t.stepId))
        : worker.ties,
    }))
    .filter((p) => p.ties.length > 0)
    .sort(
      (a, b) =>
        Number(quiet(a.ties)) - Number(quiet(b.ties)) ||
        latest(b.ties).localeCompare(latest(a.ties)),
    );
}

const newest = (times: (string | null)[]) =>
  times.reduce<string | null>((at, t) => (t && (!at || t > at) ? t : at), null);

/**
 * The agents whose work ties them to any of `steps`, each with only those steps' ties: its reasons
 * there, whether something still open holds it there, and its latest time there. Current first,
 * then the latest.
 */
export function rosterOn(
  roster: RosterAgent[],
  steps: ReadonlySet<string>,
): RosterAgent[] {
  return roster
    .flatMap((agent) => {
      const here = agent.steps.filter((s) => steps.has(s.stepId));
      if (!here.length) return [];
      return [
        {
          ...agent,
          via: REASON_ORDER.filter((v) => here.some((s) => s.via.includes(v))),
          current: here.some((s) => s.current),
          lastAt: newest(here.map((s) => s.lastAt)),
          steps: here,
        },
      ];
    })
    .sort(
      (a, b) =>
        Number(b.current) - Number(a.current) ||
        (b.lastAt ?? "").localeCompare(a.lastAt ?? ""),
    );
}

function rosterRow(agent: RosterAgent) {
  return (
    <div className="line agent-tie-row agent-roster-row" key={agent.id}>
      <span className="agent-tie">
        <Avatar name={agent.avatar} size={18} />
        <a href={"#agents/" + agent.id}>{agent.label}</a>
      </span>
      <span className="person-badges">
        {agent.current && <span className="badge">Current</span>}
        {agent.via.map((via) => (
          <span className="badge" key={via}>
            {REASONS[via]}
          </span>
        ))}
      </span>
      {agent.lastAt && (
        <small>
          <time
            dateTime={agent.lastAt}
            title={exactDiscussionDate(agent.lastAt)}
          >
            {formatDiscussionDate(agent.lastAt)}
          </time>
        </small>
      )}
    </div>
  );
}

/**
 * The agents whose work ties them to a roadmap or a step, now or before, worked out from what
 * they reported rather than tied by hand. Something still open first, then the latest; agents
 * whose credentials are revoked sit folded below the rest.
 */
export function AgentRoster({
  agents,
  total,
  loaded,
  error,
  note,
  empty,
}: {
  agents: RosterAgent[];
  /** Every agent, where `agents` may hold only the first of them. */
  total: number;
  loaded: boolean;
  error: unknown;
  /** Says where the list comes from. */
  note: string;
  empty: string;
}) {
  const live = agents.filter((agent) => agent.active);
  const retired = agents.filter((agent) => !agent.active);
  return (
    <section className="agent-ties-panel" aria-label="Agents">
      <h3>Agents {loaded && <span className="count">{total}</span>}</h3>
      <p className="muted">{note}</p>
      <ErrorMessage error={error} />
      {loaded && !agents.length && <p className="muted">{empty}</p>}
      {live.map(rosterRow)}
      {retired.length > 0 && (
        <details className="retired-ties">
          <summary>Retired agents ({retired.length})</summary>
          {retired.map(rosterRow)}
        </details>
      )}
      {total > agents.length && (
        <p className="muted">
          Showing {agents.length} of {total}.
        </p>
      )}
    </section>
  );
}

/** What a tie says, where unless it is the step the chips sit on, and when a stale report was last made. */
function describeTie(
  worker: RoadmapWorker,
  tie: RoadmapTie,
  stepTitle: (id: string) => string,
  here: string | undefined,
) {
  const what =
    tie.via === "participation"
      ? `${tie.stale ? "Stale report" : "Reporting work"} on “${tie.item?.title}”`
      : tie.via === "role"
        ? `Responsible for “${tie.role?.label}”`
        : (worker.kind === "agent"
            ? `Tracked chat “${tie.chat?.title}”`
            : "Tracked chat") + (tie.item ? ` for “${tie.item.title}”` : "");
  const where =
    tie.stepId === here
      ? ""
      : tie.stepId
        ? " on " + stepTitle(tie.stepId)
        : tie.item
          ? ""
          : " on the roadmap";
  const when =
    tie.stale && tie.lastAt
      ? ", last reported " + new Date(tie.lastAt).toLocaleString()
      : "";
  return what + where + when;
}

function describe(
  { worker, ties }: Placed,
  stepTitle: (id: string) => string,
  here: string | undefined,
) {
  const who =
    worker.kind === "agent"
      ? worker.label
      : [worker.label, worker.provider, worker.model]
          .filter(Boolean)
          .join(" · ");
  return [
    who,
    ...ties.map((t) => describeTie(worker, t, stepTitle, here)),
  ].join("\n");
}

/**
 * Who works on part of a roadmap: each agent, or a tracked chat no agent reported,
 * links to its own page. A worker whose reports there all went stale is muted.
 */
export function AgentChips({
  placed,
  stepTitle,
  here,
  limit = 3,
}: {
  placed: Placed[];
  stepTitle: (id: string) => string;
  // The step these chips sit on; ties elsewhere name their step.
  here?: string;
  limit?: number;
}) {
  const [all, setAll] = useState(false);
  if (!placed.length) return null;
  const shown = all ? placed : placed.slice(0, limit);
  return (
    <span className="agent-chips">
      {shown.map((p) => {
        const stale = quiet(p.ties);
        return (
          <a
            key={p.worker.kind + ":" + p.worker.id}
            className={"agent-chip" + (stale ? " stale" : "")}
            href={
              (p.worker.kind === "agent" ? "#agents/" : "#chats/") + p.worker.id
            }
            aria-label={p.worker.label + (stale ? ", stale report" : "")}
            title={describe(p, stepTitle, here)}
          >
            <Avatar name={p.worker.avatar} size={16} />
            <span>{p.worker.label}</span>
          </a>
        );
      })}
      {shown.length < placed.length && (
        <button
          type="button"
          className="agent-chip more"
          aria-label={`Show ${placed.length - shown.length} more`}
          onClick={() => setAll(true)}
        >
          +{placed.length - shown.length}
        </button>
      )}
    </span>
  );
}
