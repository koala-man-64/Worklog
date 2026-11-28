import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Avatar } from "./Avatar";
import { LiveDependencyWaits } from "./Dependencies";
import { useData } from "./hooks";
import { Empty, ErrorMessage } from "./ui/Messages";
import type {
  LiveAgentNode,
  LivePulse,
  LiveRepoNode,
  LiveWorkNode,
  LiveSnapshot,
  LiveSwimlaneSpan,
} from "./apiTypes";

// Each poll reads the whole live picture: every 15 seconds is current enough (audit 3.7, P7).
export const LIVE_REFRESH_MS = 15_000;

export type WindowMinutes = 15 | 60 | 360 | 1440;
type Mode = "flow" | "lanes";
type Focus = { kind: "work" | "agent" | "repo" | "person" | "pulse"; id: string };
type Point = { x: number; y: number };

export const WINDOWS: { value: WindowMinutes; label: string }[] = [
  { value: 15, label: "15m" },
  { value: 60, label: "1h" },
  { value: 360, label: "6h" },
  { value: 1440, label: "24h" },
];
export const STATES: Record<LiveAgentNode["state"], string> = {
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
const OBSERVABLE = /claude|anthropic|antigravity/i;

// Worklog finds local Claude and Antigravity sessions running on this machine. For an agent of
// another provider, no session found says nothing about whether it runs.
export function sessionText(agent: LiveAgentNode) {
  if (agent.sessionState) return SESSION[agent.sessionState];
  const providers = [...agent.providers, ...agent.chats.map((chat) => chat.provider)].filter(Boolean);
  return providers.length > 0 && !providers.some((provider) => OBSERVABLE.test(provider))
    ? "not observable"
    : "no local session";
}

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

// From the bottom of a card in one tier to the top of a card in the tier below.
function curve(from: Point, to: Point) {
  const bend = Math.max(28, Math.abs(to.y - from.y) * 0.5);
  return `M ${from.x.toFixed(1)} ${from.y.toFixed(1)} C ${from.x.toFixed(1)} ${(from.y + bend).toFixed(1)}, ${to.x.toFixed(1)} ${(to.y - bend).toFixed(1)}, ${to.x.toFixed(1)} ${to.y.toFixed(1)}`;
}

export type WorkGroup = { id: string; title: string | null; nodes: LiveWorkNode[] };

// One group per roadmap, in the order the server sends them: the roadmap, then each step
// followed by its own work items, then the roadmap's items that sit under no step shown.
export function workGroups(work: LiveWorkNode[]): WorkGroup[] {
  const groups = new Map<string, LiveWorkNode[]>();
  for (const node of work) {
    const id = node.roadmapId ?? "";
    groups.set(id, [...(groups.get(id) ?? []), node]);
  }
  return [...groups].map(([id, nodes]) => {
    const steps = nodes.filter((node) => node.kind === "step");
    const items = nodes.filter((node) => node.kind === "item");
    const placed = new Set<string>();
    const ordered = [...nodes.filter((node) => node.kind === "roadmap")];
    for (const step of steps) {
      ordered.push(step);
      for (const item of items) {
        if (item.parentId === step.id) {
          ordered.push(item);
          placed.add(item.id);
        }
      }
    }
    ordered.push(...items.filter((item) => !placed.has(item.id)));
    return { id, title: nodes[0]?.roadmapTitle ?? null, nodes: ordered };
  });
}

// Each node sits under the mean position of what it connects to above it, so the lines between
// tiers cross as little as they can. Nodes with nothing above keep their order, at the end.
function byBarycenter<T extends { id: string }>(nodes: T[], above: (id: string) => number[]) {
  const score = (id: string) => {
    const positions = above(id).filter((position) => position >= 0);
    return positions.length ? positions.reduce((sum, value) => sum + value, 0) / positions.length : null;
  };
  return nodes
    .map((node, index) => ({ node, index, score: score(node.id) }))
    .sort((a, b) => {
      if (a.score === null || b.score === null) {
        return a.score === b.score ? a.index - b.index : a.score === null ? 1 : -1;
      }
      return a.score - b.score || a.index - b.index;
    })
    .map((entry) => entry.node);
}

/**
 * The live read for a window, and for a roadmap with its sub-roadmaps when one is given. The Live
 * page and a roadmap's Live panel build it alike, so they share one cached answer.
 */
export function liveAddress(roadmapId: string | undefined, windowMinutes: number, includeIdle: boolean) {
  const params = new URLSearchParams({
    windowMinutes: String(windowMinutes),
    includeIdle: String(includeIdle),
  });
  if (roadmapId) params.set("roadmapId", roadmapId);
  return `/live?${params.toString()}`;
}

export function formatTime(value: string) {
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
  const query = useData<LiveSnapshot>(
    liveAddress(roadmapId, windowMinutes, includeIdle),
    paused ? undefined : LIVE_REFRESH_MS,
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
            <h1>Live</h1>
            <p className="live-kicker">Coordination, in motion</p>
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
          Show idle, stale and revoked agents
        </label>
      </div>

      <LiveDependencyWaits roadmapId={roadmapId} paused={paused} refreshMs={LIVE_REFRESH_MS} />
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
              <span> Widen the Activity window or tick Show idle, stale and revoked agents.</span>
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
    ["Over-cap repos", snapshot.summary.overCapRepos],
    ["Needs you", snapshot.summary.needsYou],
    ["Waiting on agents", snapshot.summary.waitingOnAgents],
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

type NodeKind = "work" | "agent" | "repo" | "person";
type Box = { x: number; top: number; bottom: number; width: number };
type Line = { id: string; from: string; to: string };

// Top down: what is planned, who works on it, and where the work lands. Lines run from the
// bottom of a card to the top of a card in the tier below; messages between agents bow below
// their tier.
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

  // Roadmaps nobody works on come first: when the tier wraps, the ones with lines to agents sit
  // on its last row, nearest the agents, so their lines do not pass behind the others.
  const connectedWork = new Set(snapshot.workEdges.map((edge) => edge.workId));
  const allGroups = workGroups(snapshot.work);
  const linked = (group: WorkGroup) => group.nodes.some((node) => connectedWork.has(node.id));
  const groups = [...allGroups.filter((group) => !linked(group)), ...allGroups.filter(linked)];
  const workIndex = new Map(groups.flatMap((group) => group.nodes).map((node, index) => [node.id, index]));
  const agents = byBarycenter(snapshot.agents, (id) =>
    snapshot.workEdges.filter((edge) => edge.agentId === id).map((edge) => workIndex.get(edge.workId) ?? -1),
  );
  const agentIndex = new Map(agents.map((agent, index) => [agent.id, index + (hasPerson ? 1 : 0)]));
  const repositories = byBarycenter(snapshot.repositories, (id) =>
    snapshot.repoEdges.filter((edge) => edge.repositoryId === id).map((edge) => agentIndex.get(edge.agentId) ?? -1),
  );
  const repoIndex = new Map(repositories.map((repo, index) => [repo.id, index]));

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

  // Where each card is, measured; before layout (and in tests) from its tier and its place in it.
  void version;
  const root = canvas.current?.getBoundingClientRect();
  const boxes = new Map<string, Box>();
  const place = (kind: NodeKind, id: string) => {
    const nodeKey = key(kind, id);
    const known = boxes.get(nodeKey);
    if (known) return nodeKey;
    const rect = cards.current.get(nodeKey)?.getBoundingClientRect();
    if (root && rect && root.width > 0 && rect.width > 0) {
      boxes.set(nodeKey, {
        x: rect.left - root.left + rect.width / 2,
        top: rect.top - root.top,
        bottom: rect.bottom - root.top,
        width: rect.width,
      });
    } else {
      const tier = kind === "work" ? 0 : kind === "repo" ? 2 : 1;
      const index =
        kind === "work" ? workIndex.get(id) : kind === "repo" ? repoIndex.get(id) : kind === "person" ? 0 : agentIndex.get(id);
      const top = 48 + tier * 220;
      boxes.set(nodeKey, { x: 150 + (index ?? 0) * 260, top, bottom: top + 120, width: 240 });
    }
    return nodeKey;
  };
  const lines: Line[] = [
    ...snapshot.workEdges.map((edge) => ({ id: edge.id, from: place("work", edge.workId), to: place("agent", edge.agentId) })),
    ...snapshot.repoEdges.map((edge) => ({ id: edge.id, from: place("agent", edge.agentId), to: place("repo", edge.repositoryId) })),
  ];
  // Lines meeting one edge of a card spread along it, in the order of their other ends, so they
  // leave side by side instead of on top of each other.
  const ports = new Map<string, number>();
  for (const side of ["from", "to"] as const) {
    const bySide = new Map<string, Line[]>();
    for (const line of lines) bySide.set(line[side], [...(bySide.get(line[side]) ?? []), line]);
    const other = (line: Line) => boxes.get(side === "from" ? line.to : line.from)!.x;
    for (const [nodeKey, list] of bySide) {
      const own = boxes.get(nodeKey)!;
      list.sort((a, b) => other(a) - other(b) || a.id.localeCompare(b.id));
      list.forEach((line, index) => {
        ports.set(`${side}:${line.id}`, own.x + own.width * 0.6 * ((index + 0.5) / list.length - 0.5));
      });
    }
  }
  const path = (line: Line) =>
    curve(
      { x: ports.get(`from:${line.id}`)!, y: boxes.get(line.from)!.bottom },
      { x: ports.get(`to:${line.id}`)!, y: boxes.get(line.to)!.top },
    );
  const lineById = new Map(lines.map((line) => [line.id, line]));

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
        {[...snapshot.workEdges, ...snapshot.repoEdges].map((edge) => (
          <path
            key={edge.id}
            data-edge-id={edge.id}
            className={edgeClass(edge.active, edge.stale, related.edges.has(edge.id), related.active && !related.edges.has(edge.id))}
            d={path(lineById.get(edge.id)!)}
          />
        ))}
        {snapshot.commEdges.map((edge, index) => {
          const a = boxes.get(place(edge.fromKind === "person" ? "person" : "agent", edge.fromId))!;
          const b = boxes.get(place(edge.toKind === "person" ? "person" : "agent", edge.toId))!;
          // From the ends of the two cards that face each other, clear of the lines to repositories.
          const facing = b.x < a.x ? -1 : 1;
          const from = { x: a.x + facing * a.width * 0.4, y: a.bottom };
          const to = { x: b.x - facing * b.width * 0.4, y: b.bottom };
          // Five depths, then over again: a bow that grew with every line would reach the next tier.
          const depth = 34 + (index % 5) * 12;
          const lowest = Math.max(from.y, to.y);
          const d = `M ${from.x.toFixed(1)} ${from.y.toFixed(1)} Q ${((from.x + to.x) / 2).toFixed(1)} ${(lowest + depth).toFixed(1)} ${to.x.toFixed(1)} ${to.y.toFixed(1)}`;
          return (
            <g key={edge.id} className={`comm-edge comm-${edge.kind} is-${edge.status}${related.edges.has(edge.id) ? " is-highlighted" : ""}${related.active && !related.edges.has(edge.id) ? " is-dimmed" : ""}`}>
              <path data-edge-id={edge.id} d={d} markerEnd="url(#live-arrow)" />
              {edge.kind === "role_offer" && <text x={(from.x + to.x) / 2} y={lowest + depth / 2} textAnchor="middle">{edge.label}</text>}
            </g>
          );
        })}
      </svg>
      <section className="live-tier live-work-tier" aria-label="Plans and work">
        <h2>Plans &amp; work</h2>
        <div className="live-tier-row">
          {groups.length === 0 && <p className="live-muted">No planned work is moving in this window.</p>}
          {groups.map((group) => (
            <div key={group.id || "outside"} className="live-work-group" role="group" aria-label={group.title ?? "Outside roadmaps"}>
              {!group.nodes.some((node) => node.kind === "roadmap") && (
                <p className="live-group-caption">{group.title ?? "Outside roadmaps"}</p>
              )}
              {group.nodes.map((node) => {
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
                    {/* The group names the roadmap; an item still says which step it sits under. */}
                    {node.kind === "item" && node.stepTitle && <p className="live-under">Under {node.stepTitle}</p>}
                    <footer>
                      <span>{node.agentCount} connected {node.agentCount === 1 ? "agent" : "agents"}</span>
                      {node.needsYou && <span>{node.openQuestions} questions / {node.waitingProposals} proposals</span>}
                    </footer>
                  </Card>
                );
              })}
            </div>
          ))}
        </div>
      </section>
      <section className="live-tier live-agent-tier" aria-label="Agents and chats">
        <h2>Agents &amp; chats</h2>
        <div className="live-tier-row">
          {!hasPerson && agents.length === 0 && <p className="live-muted">No agent is active in this window.</p>}
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
          {agents.map((agent) => {
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
                  Session: <strong>{sessionText(agent)}</strong>
                </p>
                {agent.roles.length > 0 && <div className="live-chips">{agent.roles.map((role) => <span key={role}>{role}</span>)}</div>}
                {agent.chats.length > 0 && <div className="live-chat-links">{agent.chats.map((chat) => <a key={chat.id} href={chat.href} onClick={(event) => event.stopPropagation()}>{chat.title}</a>)}</div>}
                {agent.latestNote && <p className="live-note" title={agent.latestNote}>{agent.latestNote}</p>}
              </Card>
            );
          })}
        </div>
      </section>
      <section className="live-tier live-repo-tier" aria-label="Repositories">
        <h2>Repositories</h2>
        <div className="live-tier-row">
          {repositories.length === 0 && <p className="live-muted">No repository activity in this window.</p>}
          {repositories.map((repo) => {
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
                <RepoChips repo={repo} />
                {repo.branches.length > 0 && <div className="live-branches">{repo.branches.map((branch) => <code key={branch}>{branch}</code>)}</div>}
                <footer className="live-links">
                  <a href={repo.href} onClick={(event) => event.stopPropagation()}>Open repository</a>
                  {repo.discussionId && <a href={`#discussions/${repo.discussionId}`} onClick={(event) => event.stopPropagation()}>Repository thread</a>}
                </footer>
              </Card>
            );
          })}
        </div>
      </section>
    </div>
  );
}

/** A repository's slots, execution policy and overlap, as its Live card and a roadmap's Live panel show them. */
export function RepoChips({ repo }: { repo: LiveRepoNode }) {
  return (
    <>
      {typeof repo.maxActiveLeases === "number" && (
        <div className="live-chips live-slot-chips">
          <span className={repo.maxActiveLeases === 0 ? "live-warning" : undefined}>
            {repo.maxActiveLeases === 0
              ? "Frozen (0/0)"
              : `Slots ${repo.activeLeases}/${repo.maxActiveLeases}`}
          </span>
          {repo.queuedLeases > 0 && (
            <span className="live-warning">+{repo.queuedLeases} queued</span>
          )}
          {repo.baseReadonly && <span>Base locked</span>}
          {repo.dirtySlots > 0 && (
            <span className="live-warning">{repo.dirtySlots} dirty</span>
          )}
          {repo.rescuedLeases > 0 && (
            <span>{repo.rescuedLeases} rescued</span>
          )}
        </div>
      )}
      {(repo.tier != null ||
        repo.effectiveWipCap != null ||
        repo.frozenWaveName ||
        repo.pendingRepinsCount > 0) && (
        <div className="live-chips">
          {repo.tier != null && <span>Tier {repo.tier}</span>}
          {repo.effectiveWipCap != null && (
            <span className={repo.overWipCap ? "live-warning" : undefined}>
              {repo.overWipCap ? "Over cap: " : "WIP "}
              {repo.agentCount}/{repo.effectiveWipCap}
            </span>
          )}
          {repo.doingStepCount > 0 && (
            <span>{repo.doingStepCount} doing</span>
          )}
          {repo.frozenWaveName && (
            <span className="live-warning">
              Frozen: {repo.frozenWaveName}
            </span>
          )}
          {repo.pendingRepinsCount > 0 && (
            <span className="live-warning">
              {repo.pendingRepinsCount} re-pin pending
            </span>
          )}
        </div>
      )}
      {repo.overlapCount >= 2 && repo.effectiveWipCap == null && (
        <strong className="live-warning">
          Overlap: {repo.overlapCount} agents
        </strong>
      )}
    </>
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
