import { useState, type RefObject } from "react";
import { Avatar } from "./Avatar";
import { LiveDependencyWaits } from "./Dependencies";
import { useData } from "./hooks";
import {
  LIVE_REFRESH_MS,
  RepoChips,
  STATES,
  WINDOWS,
  formatTime,
  liveAddress,
  sessionText,
  workGroups,
  type WindowMinutes,
} from "./Live";
import { Empty, ErrorMessage } from "./ui/Messages";
import type { LiveSnapshot } from "./apiTypes";

// The panel shows this many of the latest pulses; the Live tab has the whole tape.
const PULSES = 8;

/**
 * Live beside a roadmap's other views: the agents, work and repositories moving on this roadmap and
 * the roadmaps nested under it, as a column narrow enough to sit next to the outline. The Live tab
 * keeps the flow map and swimlanes.
 */
export function RoadmapLivePanel({
  id,
  roadmapId,
  panelRef,
  onClose,
}: {
  id?: string;
  roadmapId: string;
  panelRef?: RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const [windowMinutes, setWindowMinutes] = useState<WindowMinutes>(60);
  const [paused, setPaused] = useState(false);
  const query = useData<LiveSnapshot>(
    liveAddress(roadmapId, windowMinutes, false),
    paused ? undefined : LIVE_REFRESH_MS,
    { keepPrevious: true },
  );
  // A kept answer for another roadmap says nothing about this one.
  const snapshot = query.data?.roadmapId === roadmapId ? query.data : undefined;
  return (
    <aside
      id={id}
      ref={panelRef}
      className="panel roadmap-live-panel"
      aria-label="Live activity"
    >
      <div className="panel-head">
        <h2>Live</h2>
        <span className={`live-beacon${paused ? " is-paused" : ""}`}>
          {paused ? "Paused" : "Updating"}
        </span>
        <button
          type="button"
          className="quiet"
          aria-label="Hide Live panel"
          onClick={onClose}
        >
          Hide
        </button>
      </div>
      <div className="roadmap-live-controls">
        <select
          aria-label="Activity window"
          value={windowMinutes}
          onChange={(event) =>
            setWindowMinutes(Number(event.target.value) as WindowMinutes)
          }
        >
          {WINDOWS.map((window) => (
            <option key={window.value} value={window.value}>
              {window.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          aria-pressed={paused}
          onClick={() => setPaused((value) => !value)}
        >
          {paused ? "Resume live updates" : "Pause live updates"}
        </button>
        <a href={`#roadmaps/${roadmapId}/live`}>Open full Live</a>
      </div>
      <div className="panel-body">
        <LiveDependencyWaits
          roadmapId={roadmapId}
          paused={paused}
          refreshMs={LIVE_REFRESH_MS}
        />
        <ErrorMessage error={query.error} />
        {!snapshot && query.isPending && (
          <p className="muted">Reading current activity…</p>
        )}
        {snapshot && <Activity snapshot={snapshot} roadmapId={roadmapId} />}
      </div>
    </aside>
  );
}

function Activity({
  snapshot,
  roadmapId,
}: {
  snapshot: LiveSnapshot;
  roadmapId: string;
}) {
  const summary = snapshot.summary;
  // The page is this roadmap's, and every agent here is tied to it: its own card adds nothing.
  const work = snapshot.work.filter((node) => node.id !== roadmapId);
  const workById = new Map(work.map((node) => [node.id, node]));
  const repoById = new Map(
    snapshot.repositories.map((repo) => [repo.id, repo]),
  );
  const groups = workGroups(work);
  const metrics: { label: string; value: number; warning?: boolean }[] = [
    { label: "Needs you", value: summary.needsYou },
    { label: "Active agents", value: summary.activeAgents },
    {
      label: "Work in flight",
      value: summary.activeWorkItems + summary.activeSteps,
    },
    { label: "Repositories", value: summary.activeRepos },
    { label: "Overlaps", value: summary.repoOverlaps, warning: true },
    { label: "Over-cap repos", value: summary.overCapRepos, warning: true },
    {
      label: "Waiting on agents",
      value: summary.waitingOnAgents,
      warning: true,
    },
  ];
  const pulses = snapshot.pulses.slice(0, PULSES);
  const quiet =
    work.length === 0 &&
    snapshot.agents.length === 0 &&
    snapshot.repositories.length === 0;
  return (
    <>
      <dl className="roadmap-live-summary">
        {metrics
          .filter((metric) => !metric.warning || metric.value > 0)
          .map((metric) => (
            <div
              key={metric.label}
              className={metric.warning ? "is-warning" : undefined}
            >
              <dt>{metric.label}</dt>
              <dd>{metric.value}</dd>
            </div>
          ))}
      </dl>
      {quiet && (
        <Empty>
          <strong>Nothing is moving on this roadmap in this window.</strong>
          <span>
            {" "}
            Choose a longer Activity window, or open full Live to show idle
            agents.
          </span>
        </Empty>
      )}
      {snapshot.agents.length > 0 && (
        <section className="roadmap-live-section">
          <h3>
            Agents <span className="count">{snapshot.agents.length}</span>
          </h3>
          <ul className="roadmap-live-list">
            {snapshot.agents.map((agent) => {
              const ties = snapshot.workEdges.flatMap((edge) => {
                const node =
                  edge.agentId === agent.id
                    ? workById.get(edge.workId)
                    : undefined;
                return node ? [{ edge, node }] : [];
              });
              const repos = snapshot.repoEdges.flatMap((edge) => {
                const repo =
                  edge.agentId === agent.id
                    ? repoById.get(edge.repositoryId)
                    : undefined;
                return repo ? [{ edge, repo }] : [];
              });
              const chat = agent.chats[0];
              return (
                <li key={agent.id} className="roadmap-live-agent">
                  <div className="roadmap-live-row">
                    <Avatar name={agent.avatar} size={24} />
                    <a
                      className="item-title"
                      href={agent.href}
                      title={agent.label}
                    >
                      {agent.label}
                    </a>
                    <span className={`live-state state-${agent.state}`}>
                      {STATES[agent.state]}
                    </span>
                  </div>
                  <p className="roadmap-live-meta">
                    Session: {sessionText(agent)}
                  </p>
                  {(ties.length > 0 || repos.length > 0) && (
                    <ul
                      className="roadmap-live-ties"
                      aria-label={`${agent.label} works on`}
                    >
                      {ties.map(({ edge, node }) => (
                        <li
                          key={edge.id}
                          className={edge.stale ? "is-stale" : undefined}
                        >
                          <span className="roadmap-live-kind">{node.kind}</span>
                          <a href={node.href} title={node.title}>
                            {node.title}
                          </a>
                        </li>
                      ))}
                      {repos.map(({ edge, repo }) => (
                        <li
                          key={edge.id}
                          className={edge.stale ? "is-stale" : undefined}
                        >
                          <span className="roadmap-live-kind">repo</span>
                          <a href={repo.href} title={repo.name}>
                            {repo.name}
                          </a>
                          {edge.branch && (
                            <code title={edge.branch}>{edge.branch}</code>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {chat && (
                    <p className="roadmap-live-meta">
                      Chat: <a href={chat.href}>{chat.title}</a>
                    </p>
                  )}
                  {agent.latestNote && (
                    <p className="live-note" title={agent.latestNote}>
                      {agent.latestNote}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {work.length > 0 && (
        <section className="roadmap-live-section">
          <h3>
            Plans &amp; work <span className="count">{work.length}</span>
          </h3>
          {groups.map((group) => (
            <div key={group.id || "outside"} className="roadmap-live-group">
              {/* A sub-roadmap's own card heads its group; one shown without it is named here. */}
              {group.id !== roadmapId &&
                !group.nodes.some((node) => node.kind === "roadmap") && (
                  <p className="roadmap-live-caption">
                    {group.title ?? "Outside roadmaps"}
                  </p>
                )}
              <ul className="roadmap-live-list">
                {group.nodes.map((node) => (
                  <li
                    key={node.id}
                    className={`roadmap-live-work work-${node.kind}`}
                  >
                    <div className="roadmap-live-row">
                      <span className={`live-status status-${node.status}`}>
                        <i />
                        {node.kind}
                      </span>
                      <a
                        className="item-title"
                        href={node.href}
                        title={node.title}
                      >
                        {node.title}
                      </a>
                      {node.needsYou && (
                        <span className="live-warning">Needs you</span>
                      )}
                    </div>
                    <p className="roadmap-live-meta">
                      {node.agentCount} connected{" "}
                      {node.agentCount === 1 ? "agent" : "agents"}
                      {node.kind === "item" && node.stepTitle && (
                        <> · under {node.stepTitle}</>
                      )}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}
      {snapshot.repositories.length > 0 && (
        <section className="roadmap-live-section">
          <h3>
            Repositories{" "}
            <span className="count">{snapshot.repositories.length}</span>
          </h3>
          <ul className="roadmap-live-list">
            {snapshot.repositories.map((repo) => (
              <li key={repo.id} className="roadmap-live-repo">
                <div className="roadmap-live-row">
                  <a className="item-title" href={repo.href} title={repo.name}>
                    {repo.name}
                  </a>
                  {repo.unreviewed && (
                    <span className="live-neutral">unreviewed</span>
                  )}
                </div>
                <RepoChips repo={repo} />
                {repo.branches.length > 0 && (
                  <div className="live-branches">
                    {repo.branches.map((branch) => (
                      <code key={branch} title={branch}>
                        {branch}
                      </code>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {pulses.length > 0 && (
        <section className="roadmap-live-section">
          <h3>Recent pulses</h3>
          <ol className="roadmap-live-pulses">
            {pulses.map((pulse) => (
              <li key={pulse.id}>
                <a href={pulse.href}>
                  <time dateTime={pulse.at}>{formatTime(pulse.at)}</time>
                  <strong>{pulse.actorLabel}</strong>
                  <span>{pulse.summary}</span>
                </a>
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}
