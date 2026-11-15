import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Avatar } from "./Avatar";
import { useData } from "./hooks";
import { Empty, ErrorMessage } from "./ui/Messages";
import type {
  LiveAgentNode,
  LivePulse,
  LiveSnapshot,
  LiveSwimlaneSpan,
} from "./apiTypes";

type WindowMinutes = 15 | 60 | 360 | 1440;
type Mode = "flow" | "lanes";
type Focus = { kind: "work" | "agent" | "repo" | "person" | "pulse"; id: string };
type Point = { x: number; y: number };

const WINDOWS: { value: WindowMinutes; label: string }[] = [
  { value: 15, label: "15m" },
  { value: 60, label: "1h" },
  { value: 360, label: "6h" },
  { value: 1440, label: "24h" },
];
const STATES: Record<LiveAgentNode["state"], string> = {
  working: "Working",
  waiting: "Waiting on you",
  reporting: "Reporting work",
  stale: "Stale reports",
  idle: "Idle",
  revoked: "Credential revoked",
};
const DECLARED: Record<LiveAgentNode["declaredState"], string> = {
  reporting: "reporting",
  stale: "stale report",
  none: "no open report",
  revoked: "credential revoked",
};
const SESSION: Record<NonNullable<LiveAgentNode["sessionState"]>, string> = {
  working: "working",
  waiting: "waiting",
  idle: "idle",
};

function activate(event: KeyboardEvent, action: () => void) {
  if (event.target !== event.currentTarget) return;
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    action();
  }
}

function key(kind: Focus["kind"], id: string) {
  return `${kind}:${id}`;
}

function edgeClass(active: boolean, stale: boolean, highlighted: boolean, dimmed: boolean) {
  return [
    "live-edge",
    active && !stale ? "is-active" : "",
    stale ? "is-stale" : "",
    highlighted ? "is-highlighted" : "",
    dimmed ? "is-dimmed" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

function curve(from: Point, to: Point) {
  const bend = Math.max(44, Math.abs(to.x - from.x) * 0.45);
  return `M ${from.x.toFixed(1)} ${from.y.toFixed(1)} C ${(from.x + bend).toFixed(1)} ${from.y.toFixed(1)}, ${(to.x - bend).toFixed(1)} ${to.y.toFixed(1)}, ${to.x.toFixed(1)} ${to.y.toFixed(1)}`;
}

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function deepLink(href: string, label: string) {
  return (
    <a className="button" href={href}>
      {label}
    </a>
  );
}

function Card({
  nodeKey,
  selected,
  dimmed,
  highlighted,
  setRef,
  onSelect,
  className = "",
  children,
}: {
  nodeKey: string;
  selected: boolean;
  dimmed: boolean;
  highlighted: boolean;
  setRef: (element: HTMLElement | null) => void;
  onSelect: () => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <article
      ref={setRef}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      data-node-key={nodeKey}
      className={[
        "live-card",
        className,
        selected ? "is-selected" : "",
        highlighted ? "is-highlighted" : "",
        dimmed ? "is-dimmed" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={onSelect}
      onKeyDown={(event) => activate(event, onSelect)}
    >
      {children}
    </article>
  );
}

export function Live({
  roadmapId: pinnedRoadmapId,
  embedded = false,
}: {
  roadmapId?: string;
  embedded?: boolean;
}) {
  const [selectedRoadmapId, setSelectedRoadmapId] = useState("");
  const [windowMinutes, setWindowMinutes] = useState<WindowMinutes>(60);
  const [includeIdle, setIncludeIdle] = useState(false);
  const [paused, setPaused] = useState(false);
  const [mode, setMode] = useState<Mode>("flow");
  const [focus, setFocus] = useState<Focus | null>(null);
  const [selectedPulseId, setSelectedPulseId] = useState<string | null>(null);
  const roadmapId = pinnedRoadmapId ?? (selectedRoadmapId || undefined);
  const params = new URLSearchParams({
    windowMinutes: String(windowMinutes),
    includeIdle: String(includeIdle),
  });
  if (roadmapId) params.set("roadmapId", roadmapId);
  const query = useData<LiveSnapshot>(
    `/live?${params.toString()}`,
    paused ? undefined : 5_000,
    { keepPrevious: true },
  );
  const snapshot = query.data;
  const pulse = selectedPulseId === null
    ? null
    : snapshot?.pulses.find((event) => event.id === selectedPulseId) ?? null;

  useEffect(() => {
    setFocus(null);
    setSelectedPulseId(null);
  }, [roadmapId, windowMinutes, includeIdle]);

  useEffect(() => {
    if (selectedPulseId && snapshot && !snapshot.pulses.some((event) => event.id === selectedPulseId)) {
      setSelectedPulseId(null);
      setFocus(null);
    }
  }, [selectedPulseId, snapshot]);

  const selectPulse = (event: LivePulse | null) => {
    setSelectedPulseId(event?.id ?? null);
    setFocus(event ? { kind: "pulse", id: event.id } : null);
  };
  const selectFocus = (next: Focus) => {
    setSelectedPulseId(null);
    setFocus(next);
  };

  return (
    <section className={`live-page${embedded ? " is-embedded" : ""}`}>
      {!embedded && (
        <header className="live-heading">
          <div>
            <p className="live-kicker">Coordination, in motion</p>
            <h1>Live</h1>
          </div>
          <span className={`live-beacon${paused ? " is-paused" : ""}`}>
            {paused ? "Paused" : "Updating"}
          </span>
        </header>
      )}
      <div className="live-toolbar" aria-label="Live workflow controls">
        {!pinnedRoadmapId && (
          <label>
            <span>Roadmap</span>
            <select
              aria-label="Filter by roadmap"
              value={selectedRoadmapId}
              onChange={(event) => setSelectedRoadmapId(event.target.value)}
            >
              <option value="">All roadmaps</option>
              {snapshot?.roadmaps.map((roadmap) => (
                <option key={roadmap.id} value={roadmap.id}>
                  {roadmap.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="live-control-group" role="group" aria-label="Activity window">
          {WINDOWS.map((window) => (
            <button
              key={window.value}
              type="button"
              aria-pressed={windowMinutes === window.value}
              onClick={() => setWindowMinutes(window.value)}
            >
              {window.label}
            </button>
          ))}
        </div>
        <div className="live-control-group" role="group" aria-label="Visualization mode">
          <button type="button" aria-pressed={mode === "flow"} onClick={() => setMode("flow")}>
            Flow map
          </button>
          <button type="button" aria-pressed={mode === "lanes"} onClick={() => setMode("lanes")}>
            Swimlanes
          </button>
        </div>
        <button type="button" aria-pressed={paused} onClick={() => setPaused((value) => !value)}>
          {paused ? "Resume live updates" : "Pause live updates"}
        </button>
        <label className="live-check">
          <input
            type="checkbox"
            checked={includeIdle}
            onChange={(event) => setIncludeIdle(event.target.checked)}
          />
          Show idle agents
        </label>
      </div>

      <ErrorMessage error={query.error} />
      {!snapshot && query.isPending && <p className="live-loading">Reading current activity...</p>}
      {snapshot && (
        <>
          <Summary snapshot={snapshot} />
          {snapshot.work.length === 0 &&
          snapshot.agents.length === 0 &&
          snapshot.repositories.length === 0 &&
          snapshot.untrackedSessions.length === 0 ? (
            <Empty>
              <strong>Nothing is active in this window.</strong>
              <span> Widen the Activity window or tick Show idle agents.</span>
            </Empty>
          ) : (
            <>
              {focus && <Inspector snapshot={snapshot} focus={focus} pulse={pulse} clear={() => selectPulse(null)} />}
              {mode === "flow" ? (
                <FlowMap snapshot={snapshot} focus={focus} pulse={pulse} onFocus={selectFocus} />
              ) : (
                <Swimlanes snapshot={snapshot} focus={focus} pulse={pulse} onFocus={selectFocus} />
              )}
              <Untracked snapshot={snapshot} />
            </>
          )}
          <PulseTape
            pulses={snapshot.pulses}
            selected={selectedPulseId}
            onSelect={selectPulse}
          />
          <AccessibleTables snapshot={snapshot} />
        </>
      )}
    </section>
  );
}

function Summary({ snapshot }: { snapshot: LiveSnapshot }) {
  const metrics = [
    ["Active agents", snapshot.summary.activeAgents],
    ["Working chats", snapshot.summary.workingChats],
    ["Work in flight", snapshot.summary.activeWorkItems + snapshot.summary.activeSteps],
    ["Repositories", snapshot.summary.activeRepos],
    ["Overlaps", snapshot.summary.repoOverlaps],
    ["Needs you", snapshot.summary.needsYou],
  ];
  return (
    <dl className="live-summary">
      {metrics.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function connections(snapshot: LiveSnapshot, focus: Focus | null, pulse: LivePulse | null) {
  const nodes = new Set<string>();
  const edges = new Set<string>();
  if (pulse) {
    nodes.add(key(pulse.actorKind === "person" ? "person" : "agent", pulse.actorId));
    if (pulse.targetActorId) nodes.add(key(pulse.targetActorId === "local-user" ? "person" : "agent", pulse.targetActorId));
    if (pulse.workId) nodes.add(key("work", pulse.workId));
    if (pulse.repositoryId) nodes.add(key("repo", pulse.repositoryId));
    for (const edge of snapshot.workEdges) {
      if (nodes.has(key("work", edge.workId)) && nodes.has(key("agent", edge.agentId))) edges.add(edge.id);
    }
    for (const edge of snapshot.repoEdges) {
      if (nodes.has(key("repo", edge.repositoryId)) && nodes.has(key("agent", edge.agentId))) edges.add(edge.id);
    }
    for (const edge of snapshot.commEdges) {
      const from = key(edge.fromKind === "person" ? "person" : "agent", edge.fromId);
      const to = key(edge.toKind === "person" ? "person" : "agent", edge.toId);
      if (nodes.has(from) && nodes.has(to)) edges.add(edge.id);
    }
  } else if (focus) {
    nodes.add(key(focus.kind, focus.id));
    for (const edge of snapshot.workEdges) {
      if ((focus.kind === "work" && edge.workId === focus.id) || (focus.kind === "agent" && edge.agentId === focus.id)) {
        nodes.add(key("work", edge.workId));
        nodes.add(key("agent", edge.agentId));
        edges.add(edge.id);
      }
    }
    for (const edge of snapshot.repoEdges) {
      if ((focus.kind === "repo" && edge.repositoryId === focus.id) || (focus.kind === "agent" && edge.agentId === focus.id)) {
        nodes.add(key("repo", edge.repositoryId));
        nodes.add(key("agent", edge.agentId));
        edges.add(edge.id);
      }
    }
    for (const edge of snapshot.commEdges) {
      if (edge.fromId === focus.id || edge.toId === focus.id) {
        nodes.add(key(edge.fromKind === "person" ? "person" : "agent", edge.fromId));
        nodes.add(key(edge.toKind === "person" ? "person" : "agent", edge.toId));
        edges.add(edge.id);
      }
    }
  }
  return { nodes, edges, active: Boolean(focus || pulse) };
}

function FlowMap({
  snapshot,
  focus,
  pulse,
  onFocus,
}: {
  snapshot: LiveSnapshot;
  focus: Focus | null;
  pulse: LivePulse | null;
  onFocus: (focus: Focus) => void;
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const cards = useRef(new Map<string, HTMLElement>());
  const [version, setVersion] = useState(0);
  const related = connections(snapshot, focus, pulse);
  const hasPerson = snapshot.commEdges.some((edge) => edge.fromId === "local-user" || edge.toId === "local-user");

  useEffect(() => {
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => setVersion((v) => v + 1));
    if (canvas.current) observer?.observe(canvas.current);
    for (const element of cards.current.values()) observer?.observe(element);
    const resize = () => setVersion((value) => value + 1);
    window.addEventListener("resize", resize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", resize);
    };
  }, [snapshot]);

  const setCard = (nodeKey: string) => (element: HTMLElement | null) => {
    if (element) cards.current.set(nodeKey, element);
    else cards.current.delete(nodeKey);
  };
  const indexOf = (kind: "work" | "agent" | "repo" | "person", id: string) => {
    if (kind === "work") return snapshot.work.findIndex((node) => node.id === id);
    if (kind === "repo") return snapshot.repositories.findIndex((node) => node.id === id);
    if (kind === "person") return 0;
    return snapshot.agents.findIndex((node) => node.id === id) + (hasPerson ? 1 : 0);
  };
  const fallback = (kind: "work" | "agent" | "repo" | "person", id: string, side: "left" | "right"): Point => {
    const width = canvas.current?.getBoundingClientRect().width || 1080;
    const column = kind === "work" ? 0 : kind === "repo" ? 2 : 1;
    const left = width * (column / 3) + 18;
    const cardWidth = width / 3 - 36;
    return { x: side === "left" ? left : left + cardWidth, y: 82 + Math.max(0, indexOf(kind, id)) * 132 };
  };
  const anchor = (kind: "work" | "agent" | "repo" | "person", id: string, side: "left" | "right") => {
    void version;
    const root = canvas.current?.getBoundingClientRect();
    const rect = cards.current.get(key(kind, id))?.getBoundingClientRect();
    if (!root || !rect || root.width === 0 || rect.width === 0) return fallback(kind, id, side);
    return { x: (side === "left" ? rect.left : rect.right) - root.left, y: rect.top - root.top + rect.height / 2 };
  };

  const nodeState = (nodeKey: string) => ({
    selected: Boolean(focus && key(focus.kind, focus.id) === nodeKey),
    highlighted: related.active && related.nodes.has(nodeKey),
    dimmed: related.active && !related.nodes.has(nodeKey),
  });
  return (
    <div className="live-canvas" ref={canvas}>
      <svg className="live-wires" aria-label="Active workflow connections">
        <defs>
          <marker id="live-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>
        {snapshot.workEdges.map((edge) => (
          <path
            key={edge.id}
            data-edge-id={edge.id}
            className={edgeClass(edge.active, edge.stale, related.edges.has(edge.id), related.active && !related.edges.has(edge.id))}
            d={curve(anchor("work", edge.workId, "right"), anchor("agent", edge.agentId, "left"))}
          />
        ))}
        {snapshot.repoEdges.map((edge) => (
          <path
            key={edge.id}
            data-edge-id={edge.id}
            className={edgeClass(edge.active, edge.stale, related.edges.has(edge.id), related.active && !related.edges.has(edge.id))}
            d={curve(anchor("agent", edge.agentId, "right"), anchor("repo", edge.repositoryId, "left"))}
          />
        ))}
        {snapshot.commEdges.map((edge, index) => {
          const fromKind = edge.fromKind === "person" ? "person" : "agent";
          const toKind = edge.toKind === "person" ? "person" : "agent";
          const from = anchor(fromKind, edge.fromId, index % 2 ? "left" : "right");
          const to = anchor(toKind, edge.toId, index % 2 ? "left" : "right");
          const bow = 34 + index * 9;
          const direction = index % 2 ? -1 : 1;
          const d = `M ${from.x.toFixed(1)} ${from.y.toFixed(1)} Q ${(from.x + direction * bow).toFixed(1)} ${((from.y + to.y) / 2).toFixed(1)} ${to.x.toFixed(1)} ${to.y.toFixed(1)}`;
          return (
            <g key={edge.id} className={`comm-edge comm-${edge.kind} is-${edge.status}${related.edges.has(edge.id) ? " is-highlighted" : ""}${related.active && !related.edges.has(edge.id) ? " is-dimmed" : ""}`}>
              <path data-edge-id={edge.id} d={d} markerEnd="url(#live-arrow)" />
              {edge.kind === "role_offer" && <text x={(from.x + to.x) / 2 + direction * bow * 0.45} y={(from.y + to.y) / 2}>{edge.label}</text>}
            </g>
          );
        })}
      </svg>
      <section className="live-column live-work-column">
        <h2>Plans &amp; work</h2>
        {snapshot.work.map((node) => {
          const nodeKey = key("work", node.id);
          return (
            <Card
              key={node.id}
              nodeKey={nodeKey}
              {...nodeState(nodeKey)}
              setRef={setCard(nodeKey)}
              onSelect={() => onFocus({ kind: "work", id: node.id })}
              className={`work-${node.kind}`}
            >
              <div className="live-card-topline">
                <span className={`live-status status-${node.status}`}><i />{node.kind}</span>
                {node.needsYou && <span className="live-warning">Needs you</span>}
              </div>
              <h3>{node.title}</h3>
              {(node.roadmapTitle || node.stepTitle) && <p>{[node.roadmapTitle, node.stepTitle].filter(Boolean).join(" / ")}</p>}
              <footer>
                <span>{node.agentCount} connected {node.agentCount === 1 ? "agent" : "agents"}</span>
                {node.needsYou && <span>{node.openQuestions} questions / {node.waitingProposals} proposals</span>}
              </footer>
            </Card>
          );
        })}
      </section>
      <section className="live-column live-agent-column">
        <h2>Agents &amp; chats</h2>
        {hasPerson && (
          <Card
            nodeKey={key("person", "local-user")}
            {...nodeState(key("person", "local-user"))}
            setRef={setCard(key("person", "local-user"))}
            onSelect={() => onFocus({ kind: "person", id: "local-user" })}
            className="live-person-card"
          >
            <Avatar kind="human" size={28} />
            <div><h3>You</h3><p>Requests, replies and handoffs</p></div>
          </Card>
        )}
        {snapshot.agents.map((agent) => {
          const nodeKey = key("agent", agent.id);
          return (
            <Card
              key={agent.id}
              nodeKey={nodeKey}
              {...nodeState(nodeKey)}
              setRef={setCard(nodeKey)}
              onSelect={() => onFocus({ kind: "agent", id: agent.id })}
              className={`agent-${agent.state}`}
            >
              <div className="live-agent-head">
                <span className="live-agent-avatar"><Avatar name={agent.avatar} size={32} /></span>
                <div><h3>{agent.label}</h3><span className={`live-state state-${agent.state}`}>{STATES[agent.state]}</span></div>
              </div>
              <p className="live-provenance">
                Declared: <strong>{DECLARED[agent.declaredState]}</strong>
                <span aria-hidden="true"> / </span>
                Session: <strong>{agent.sessionState ? SESSION[agent.sessionState] : "no local session"}</strong>
              </p>
              {agent.roles.length > 0 && <div className="live-chips">{agent.roles.map((role) => <span key={role}>{role}</span>)}</div>}
              {agent.chats.length > 0 && <div className="live-chat-links">{agent.chats.map((chat) => <a key={chat.id} href={chat.href} onClick={(event) => event.stopPropagation()}>{chat.title}</a>)}</div>}
              {agent.latestNote && <p className="live-note">{agent.latestNote}</p>}
            </Card>
          );
        })}
      </section>
      <section className="live-column live-repo-column">
        <h2>Repositories</h2>
        {snapshot.repositories.map((repo) => {
          const nodeKey = key("repo", repo.id);
          return (
            <Card
              key={repo.id}
              nodeKey={nodeKey}
              {...nodeState(nodeKey)}
              setRef={setCard(nodeKey)}
              onSelect={() => onFocus({ kind: "repo", id: repo.id })}
            >
              <div className="live-card-topline">
                <h3>{repo.name}</h3>
                {repo.unreviewed && <span className="live-neutral">unreviewed</span>}
              </div>
              {repo.overlapCount >= 2 && <strong className="live-warning">Overlap: {repo.overlapCount} agents</strong>}
              {repo.branches.length > 0 && <div className="live-branches">{repo.branches.map((branch) => <code key={branch}>{branch}</code>)}</div>}
              <footer className="live-links">
                <a href={repo.href} onClick={(event) => event.stopPropagation()}>Open repository</a>
                {repo.discussionId && <a href={`#discussions/${repo.discussionId}`} onClick={(event) => event.stopPropagation()}>Repository thread</a>}
              </footer>
            </Card>
          );
        })}
      </section>
    </div>
  );
}

function Inspector({
  snapshot,
  focus,
  pulse,
  clear,
}: {
  snapshot: LiveSnapshot;
  focus: Focus;
  pulse: LivePulse | null;
  clear: () => void;
}) {
  const work = focus.kind === "work" ? snapshot.work.find((node) => node.id === focus.id) : null;
  const agent = focus.kind === "agent" ? snapshot.agents.find((node) => node.id === focus.id) : null;
  const repo = focus.kind === "repo" ? snapshot.repositories.find((node) => node.id === focus.id) : null;
  const title = pulse?.summary ?? work?.title ?? agent?.label ?? repo?.name ?? "You";
  return (
    <aside className="live-inspector" aria-label="Selection inspector">
      <div><span>Focused</span><strong>{title}</strong></div>
      <div className="live-inspector-actions">
        {work && deepLink(work.href, work.kind === "roadmap" ? "Open roadmap" : work.kind === "step" ? "Open step" : "Open work item")}
        {work?.waitingProposalIds[0] && deepLink(`#review/${work.waitingProposalIds[0]}`, "Review proposal")}
        {agent && deepLink(agent.href, "Open agent")}
        {agent?.chats[0] && deepLink(agent.chats[0].href, "Open chat")}
        {repo && deepLink(repo.href, "Open repository")}
        {repo?.discussionId && deepLink(`#discussions/${repo.discussionId}`, "Open discussion")}
        {pulse && deepLink(pulse.href, pulse.proposalId ? "Review proposal" : pulse.discussionId ? "Open discussion" : "Open record")}
        <button type="button" onClick={clear}>Clear selection</button>
      </div>
    </aside>
  );
}

function Swimlanes({ snapshot, focus, pulse, onFocus }: { snapshot: LiveSnapshot; focus: Focus | null; pulse: LivePulse | null; onFocus: (focus: Focus) => void }) {
  const end = new Date(snapshot.generatedAt).getTime();
  const start = end - snapshot.windowMinutes * 60_000;
  const position = (value: string) => Math.max(0, Math.min(100, ((new Date(value).getTime() - start) / (end - start)) * 100));
  const ticks = Array.from({ length: 5 }, (_, index) => start + ((end - start) * index) / 4);
  const communicative = new Set(["ack_requested", "ack_satisfied", "role_handoff", "message"]);
  const markers = [
    ...snapshot.commEdges.filter((edge) => edge.lastAt).map((edge) => ({ id: edge.id, at: edge.lastAt!, label: edge.label, agentIds: [edge.fromId, edge.toId] })),
    ...snapshot.pulses.filter((event) => communicative.has(event.kind)).map((event) => ({ id: event.id, at: event.at, label: event.summary, agentIds: [event.actorId, event.targetActorId].filter(Boolean) as string[] })),
  ].filter((marker) => new Date(marker.at).getTime() >= start && new Date(marker.at).getTime() <= end);
  const related = connections(snapshot, focus, pulse);
  return (
    <section className="live-lanes" aria-label="Agent activity swimlanes">
      <div className="lane-time-axis"><span />{ticks.map((tick) => <time key={tick}>{formatTime(new Date(tick).toISOString())}</time>)}</div>
      {snapshot.agents.map((agent) => {
        const nodeKey = key("agent", agent.id);
        const dimmed = related.active && !related.nodes.has(nodeKey);
        return (
          <div key={agent.id} className={`lane-row${dimmed ? " is-dimmed" : ""}${related.nodes.has(nodeKey) ? " is-highlighted" : ""}`}>
            <button className="lane-agent" type="button" onClick={() => onFocus({ kind: "agent", id: agent.id })}>
              <Avatar name={agent.avatar} size={28} /><span><strong>{agent.label}</strong><small>{STATES[agent.state]}</small></span>
            </button>
            <div className="lane-track">
              {snapshot.lanes.filter((span) => span.agentId === agent.id).map((span) => {
                const highlighted =
                  (focus?.kind === "agent" && focus.id === span.agentId) ||
                  (span.workId !== null && related.nodes.has(key("work", span.workId))) ||
                  (span.repositoryId !== null && related.nodes.has(key("repo", span.repositoryId)));
                return <LaneSpan key={span.id} span={span} start={start} end={end} highlighted={highlighted} dimmed={related.active && !highlighted} />;
              })}
              {markers.filter((marker) => marker.agentIds.includes(agent.id)).map((marker) => (
                <a key={marker.id} className="lane-marker" style={{ left: `${position(marker.at)}%` }} href={snapshot.commEdges.find((edge) => edge.id === marker.id)?.href ?? snapshot.pulses.find((event) => event.id === marker.id)?.href} title={marker.label}><span>{marker.label}</span></a>
              ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function LaneSpan({ span, start, end, highlighted, dimmed }: { span: LiveSwimlaneSpan; start: number; end: number; highlighted: boolean; dimmed: boolean }) {
  const left = Math.max(0, ((new Date(span.startedAt).getTime() - start) / (end - start)) * 100);
  const finish = span.endedAt
    ? new Date(span.endedAt).getTime()
    : span.stale
      ? new Date(span.lastSeenAt).getTime()
      : end;
  const right = Math.min(100, ((finish - start) / (end - start)) * 100);
  return (
    <a
      href={span.href}
      className={`lane-span${span.stale ? " is-stale" : ""}${highlighted ? " is-highlighted" : ""}${dimmed ? " is-dimmed" : ""}`}
      style={{ left: `${left}%`, width: `${Math.max(1.5, right - left)}%` }}
      title={[span.label, span.repositoryName, span.branch].filter(Boolean).join(" / ")}
    >
      <span>{span.label}</span>{span.repositoryName && <small>{span.repositoryName}{span.branch ? ` / ${span.branch}` : ""}</small>}
    </a>
  );
}

function Untracked({ snapshot }: { snapshot: LiveSnapshot }) {
  if (snapshot.untrackedSessions.length === 0) return null;
  return (
    <aside className="live-untracked">
      <div><p className="live-kicker">Local discovery</p><h2>Running on this machine</h2></div>
      <ul>{snapshot.untrackedSessions.map((session) => <li key={session.sessionId}><strong>{session.name}</strong><span>{session.state} / {session.model}</span><code>{session.cwd}</code></li>)}</ul>
      <a className="button" href="#chats">Open Chats</a>
    </aside>
  );
}

function PulseTape({ pulses, selected, onSelect }: { pulses: LivePulse[]; selected: string | null; onSelect: (event: LivePulse | null) => void }) {
  const selectedIndex = selected === null ? -1 : pulses.findIndex((event) => event.id === selected);
  const previousIndex = selectedIndex < 0 ? 0 : Math.min(pulses.length - 1, selectedIndex + 1);
  const nextIndex = Math.max(0, selectedIndex - 1);
  return (
    <section className="live-pulses">
      <header><div><p className="live-kicker">Event tape</p><h2>Recent pulses</h2></div><div className="live-pulse-controls">
        <button type="button" disabled={!pulses.length || selectedIndex === pulses.length - 1} onClick={() => onSelect(pulses[previousIndex])}>Previous pulse</button>
        <button type="button" disabled={selectedIndex <= 0} onClick={() => onSelect(pulses[nextIndex])}>Next pulse</button>
        <button type="button" onClick={() => onSelect(null)}>Live now</button>
      </div></header>
      {pulses.length ? <ol>{pulses.map((event) => (
        <li key={event.id}><button type="button" aria-pressed={selected === event.id} onClick={() => onSelect(event)}><time dateTime={event.at}>{formatTime(event.at)}</time><strong>{event.actorLabel}</strong><span>{event.summary}</span></button></li>
      ))}</ol> : <p className="live-muted">No recent pulses in this window.</p>}
      {selectedIndex >= 0 && <a className="live-pulse-open" href={pulses[selectedIndex].href}>Open record</a>}
    </section>
  );
}

function AccessibleTables({ snapshot }: { snapshot: LiveSnapshot }) {
  const connections = [
    ...snapshot.workEdges.map((edge) => ({ id: edge.id, source: snapshot.work.find((node) => node.id === edge.workId)?.title ?? edge.workId, target: snapshot.agents.find((node) => node.id === edge.agentId)?.label ?? edge.agentId, kind: edge.via.join(", "), state: edge.stale ? "stale" : edge.active ? "active" : "inactive" })),
    ...snapshot.repoEdges.map((edge) => ({ id: edge.id, source: snapshot.agents.find((node) => node.id === edge.agentId)?.label ?? edge.agentId, target: snapshot.repositories.find((node) => node.id === edge.repositoryId)?.name ?? edge.repositoryId, kind: edge.via.join(", "), state: edge.stale ? "stale" : edge.active ? "active" : "inactive" })),
    ...snapshot.commEdges.map((edge) => ({ id: edge.id, source: edge.fromLabel, target: edge.toLabel, kind: edge.label || edge.kind, state: edge.status })),
  ];
  return (
    <details className="chart-table live-table">
      <summary>Show as table</summary>
      <h3>Active connections</h3>
      <table><thead><tr><th scope="col">From</th><th scope="col">To</th><th scope="col">Connection</th><th scope="col">State</th></tr></thead><tbody>
        {connections.map((edge) => <tr key={edge.id}><th scope="row">{edge.source}</th><td>{edge.target}</td><td>{edge.kind}</td><td>{edge.state}</td></tr>)}
      </tbody></table>
      <h3>Recent pulses</h3>
      <table><thead><tr><th scope="col">Time</th><th scope="col">Actor</th><th scope="col">Event</th><th scope="col">Record</th></tr></thead><tbody>
        {snapshot.pulses.map((event) => <tr key={event.id}><th scope="row"><time dateTime={event.at}>{formatTime(event.at)}</time></th><td>{event.actorLabel}</td><td>{event.summary}</td><td><a href={event.href}>Open record</a></td></tr>)}
      </tbody></table>
    </details>
  );
}
