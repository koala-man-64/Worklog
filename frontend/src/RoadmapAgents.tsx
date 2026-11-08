import { useState } from "react";
import { Avatar } from "./Avatar";
import type { RoadmapTie, RoadmapWorker } from "./apiTypes";

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
