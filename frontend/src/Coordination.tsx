import { useState } from "react";
import { useData, useWrites } from "./hooks";
import { commands } from "./commands";
import { Empty, ErrorMessage } from "./ui/Messages";
import { Pager } from "./ui/Pager";
import { RepositoryBadges } from "./RepositoryControls";
import type { AgentRun, Answer, CoordinationUpdate } from "./apiTypes";

const PAGE = 50;
const REFRESH = 30_000;
function timestamp(value: string) {
  return <time dateTime={value}>{new Date(value).toLocaleString()}</time>;
}
export function EndRun({ run }: { run: AgentRun }) {
  const action = useWrites();
  const [confirm, setConfirm] = useState(false);
  return (
    <>
      {run.endedAt ? (
        <small>Ended {timestamp(run.endedAt)}</small>
      ) : confirm ? (
        <span>
          End {run.displayName} and release its reports?{" "}
          <button
            disabled={action.isPending}
            onClick={() =>
              action.mutate(
                () => commands.endAgentRun(run.id, { revision: run.revision }),
                { onSuccess: () => setConfirm(false) },
              )
            }
          >
            Confirm end run
          </button>{" "}
          <button onClick={() => setConfirm(false)}>Cancel</button>
        </span>
      ) : (
        <button className="quiet" onClick={() => setConfirm(true)}>
          End run {run.displayName}
        </button>
      )}
      <ErrorMessage error={action.error} />
    </>
  );
}
export function NamedRuns({ chatId }: { chatId: string }) {
  const [offset, setOffset] = useState(0);
  const runs = useData<Answer<"/api/v1/agent-runs">>(
    `/agent-runs?externalChatId=${encodeURIComponent(chatId)}&includeEnded=true&limit=${PAGE}&offset=${offset}`,
    REFRESH,
  );
  return (
    <div className="named-runs">
      <ErrorMessage error={runs.error} />
      {(runs.data?.items ?? []).map((run) => (
        <div key={run.id}>
          <strong>{run.displayName}</strong>{" "}
          <small>Reported run · {run.provider || "Provider unspecified"}</small>{" "}
          <EndRun run={run} />
        </div>
      ))}
      <Pager
        offset={offset}
        total={runs.data?.total ?? 0}
        onChange={setOffset}
        label="chat runs"
      />
    </div>
  );
}
export function OpenRuns() {
  const [offset, setOffset] = useState(0);
  const query = useData<Answer<"/api/v1/agent-runs">>(
    `/agent-runs?limit=${PAGE}&offset=${offset}`,
    REFRESH,
  );
  return (
    <section aria-label="Open agent runs">
      <ErrorMessage error={query.error} />
      {query.isPending ? (
        <p>Loading runs…</p>
      ) : !query.error && !query.data?.items.length ? (
        <Empty>No open agent runs.</Empty>
      ) : null}
      {(query.data?.items ?? []).map((run) => (
        <article className="line" key={run.id}>
          <strong>{run.displayName}</strong>{" "}
          <small>{run.provider || "Provider unspecified"}</small>{" "}
          <EndRun run={run} />
        </article>
      ))}
      <Pager
        offset={offset}
        total={query.data?.total ?? 0}
        onChange={setOffset}
        label="runs"
      />
    </section>
  );
}
export function Coordination({
  itemId,
  repositoryId,
}: {
  itemId?: string;
  repositoryId?: string;
}) {
  const [includeReleased, setIncludeReleased] = useState(false);
  const [offset, setOffset] = useState(0);
  const [feedOffset, setFeedOffset] = useState(0);
  const filter = itemId
    ? `itemId=${encodeURIComponent(itemId)}`
    : repositoryId
      ? `repositoryId=${encodeURIComponent(repositoryId)}`
      : "";
  const participants = useData<Answer<"/api/v1/participations">>(
    `/participations?${filter}&includeReleased=${includeReleased}&limit=${PAGE}&offset=${offset}`,
    REFRESH,
  );
  const feed = useData<Answer<"/api/v1/coordination-updates">>(
    `/coordination-updates?${filter}&limit=${PAGE}&offset=${feedOffset}`,
    REFRESH,
  );
  return (
    <section className="coordination" aria-label="Agent coordination">
      <h2>Participants</h2>
      <p className="muted">
        Reported by agents. Shared participation does not reserve work or
        repositories. Old reports become stale after two hours.
      </p>
      <label>
        <input
          type="checkbox"
          checked={includeReleased}
          onChange={(e) => {
            setIncludeReleased(e.target.checked);
            setOffset(0);
          }}
        />{" "}
        Show released reports
      </label>
      <ErrorMessage error={participants.error} />
      {participants.isPending ? (
        <p>Loading participants…</p>
      ) : !participants.error && !participants.data?.items.length ? (
        <Empty>
          {includeReleased
            ? "No participation reported."
            : "No current participation reported. Older released reports may be available."}
        </Empty>
      ) : null}
      {(participants.data?.items ?? []).map((p) => (
        <article className="panel coordination-report" key={p.id}>
          <div className="panel-head">
            <h3>{p.run.displayName}</h3>
            <strong>
              {p.releasedAt
                ? "Released"
                : p.stale
                  ? "Stale report"
                  : "Reported"}
            </strong>
          </div>
          <div className="panel-body">
            <p className="preserve-lines">
              {p.description || "No activity description reported."}
            </p>
            <p>
              Last reported {timestamp(p.lastSeenAt)}
              {p.releasedAt && (
                <>
                  {" "}
                  · Released {timestamp(p.releasedAt)}
                  {p.releaseReason ? ` (${p.releaseReason})` : ""}
                </>
              )}
            </p>
            {p.itemId ? (
              <p>
                <a href={"#work/" + p.itemId}>Linked work item</a>
              </p>
            ) : (
              <p>Repository activity · No work item attached</p>
            )}
            <h4>Reported repository context</h4>
            {p.contexts.map((c, i) => (
              <div key={i} className="reported-context">
                <RepositoryBadges ids={[c.repositoryId]} />
                {c.branch && (
                  <p>
                    Branch: <code>{c.branch}</code>
                  </p>
                )}
                {c.checkoutPath && (
                  <p>
                    Checkout: <code>{c.checkoutPath}</code>
                  </p>
                )}
                {c.worktreePath && (
                  <p>
                    Worktree: <code>{c.worktreePath}</code>
                  </p>
                )}
              </div>
            ))}
            <ParticipationHistory id={p.id} />
            <EndRun run={p.run} />
          </div>
        </article>
      ))}
      <Pager
        offset={offset}
        total={participants.data?.total ?? 0}
        onChange={setOffset}
        label="participants"
      />
      <h2>Recent updates</h2>
      <p className="muted">
        Updates, blockers and handoffs retain the context reported when posted.
      </p>
      <ErrorMessage error={feed.error} />
      {feed.isPending ? (
        <p>Loading updates…</p>
      ) : !feed.error && !feed.data?.items.length ? (
        <Empty>No updates reported yet.</Empty>
      ) : null}
      {(feed.data?.items ?? []).map((update) => (
        <UpdateCard key={update.id} update={update} />
      ))}
      <Pager
        offset={feedOffset}
        total={feed.data?.total ?? 0}
        onChange={setFeedOffset}
        label="updates"
      />
    </section>
  );
}
function UpdateCard({ update }: { update: CoordinationUpdate }) {
  return (
    <article className="line coordination-update">
      <p>
        <strong>
          {update.kind === "blocker"
            ? "Blocker"
            : update.kind === "handoff"
              ? "Handoff"
              : "Update"}
        </strong>{" "}
        · {update.authorLabel} · {timestamp(update.createdAt)}
      </p>
      <p className="preserve-lines">{update.text}</p>
      {update.contexts.length > 0 && (
        <details>
          <summary>Reported context when posted</summary>
          {update.contexts.map((context, i) => (
            <div className="reported-context" key={i}>
              <a href={"#repositories/" + context.repositoryId}>
                {context.repositoryName || context.repositoryId}
              </a>
              {context.branch && (
                <p>
                  Branch: <code>{context.branch}</code>
                </p>
              )}
              {context.checkoutPath && (
                <p>
                  Checkout: <code>{context.checkoutPath}</code>
                </p>
              )}
              {context.worktreePath && (
                <p>
                  Worktree: <code>{context.worktreePath}</code>
                </p>
              )}
            </div>
          ))}
        </details>
      )}
      {update.itemId && <a href={"#work/" + update.itemId}>Work item</a>}
    </article>
  );
}
function ParticipationHistory({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Participation history</summary>
      {open && <ParticipationUpdates id={id} />}
    </details>
  );
}
function ParticipationUpdates({ id }: { id: string }) {
  const [offset, setOffset] = useState(0);
  const query = useData<Answer<"/api/v1/coordination-updates">>(
    `/coordination-updates?participationId=${encodeURIComponent(id)}&limit=${PAGE}&offset=${offset}`,
    REFRESH,
  );
  return (
    <>
      <ErrorMessage error={query.error} />
      {query.isPending ? (
        <p>Loading participation history…</p>
      ) : !query.error && !query.data?.items.length ? (
        <p>No updates reported for this participation.</p>
      ) : null}
      {(query.data?.items ?? []).map((update) => (
        <UpdateCard key={update.id} update={update} />
      ))}
      <Pager
        offset={offset}
        total={query.data?.total ?? 0}
        onChange={setOffset}
        label="participation history"
      />
    </>
  );
}
