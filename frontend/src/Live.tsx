import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { contextStorageKey } from "./api";
import { Avatar } from "./Avatar";
import { LiveDependencyWaits } from "./Dependencies";
import { useData } from "./hooks";
import { Icon } from "./icons";
import {
  buildRoadmapTree,
  filterLiveSnapshot,
  formatRelativeAge,
  liveConnections,
  nodeKey as key,
  roadmapAncestors,
  toggleRoadmapTreeSelection,
  type AgentScope,
  type EdgeMode,
  type HiddenCounts,
  type KpiFilter,
  type LiveFocus as Focus,
} from "./liveFilter";
import { localStore } from "./storage";
import { Empty, ErrorMessage } from "./ui/Messages";
import type {
  LiveAgentNode,
  LivePulse,
  LiveRepoNode,
  LiveRoadmapOption,
  LiveSnapshot,
  LiveSwimlaneSpan,
  LiveWorkNode,
} from "./apiTypes";

// Each poll reads the whole live picture: every 15 seconds is current enough (audit 3.7, P7).
export const LIVE_REFRESH_MS = 15_000;
const MAX_CARD_BRANCHES = 3;

export type WindowMinutes = 15 | 60 | 360 | 1440;
type Mode = "flow" | "lanes";
type Point = { x: number; y: number };

export const WINDOWS: { value: WindowMinutes; label: string }[] = [
  { value: 15, label: "15m" },
  { value: 60, label: "1h" },
  { value: 360, label: "6h" },
  { value: 1440, label: "24h" },
];
const AGENT_SCOPES: { value: AgentScope; label: string; title: string }[] = [
  { value: "active", label: "Active", title: "Currently working, waiting, or reporting" },
  { value: "recent", label: "Recent", title: "Active plus idle and stale agents from this window" },
  { value: "all", label: "All", title: "All live agents, including older idle and stale history" },
];
const EDGE_MODES: { value: EdgeMode; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "all", label: "All" },
  { value: "focus", label: "Focus only" },
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

type LivePreferences = {
  roadmapIds: string[];
  windowMinutes: WindowMinutes;
  agentScope: AgentScope;
  showRevoked: boolean;
  hideUnconnectedWork: boolean;
  hideDoneWork: boolean;
  edgeMode: EdgeMode;
  compact: boolean;
  showPulses: boolean;
  showUntracked: boolean;
};

const DEFAULT_PREFS: LivePreferences = {
  roadmapIds: [],
  windowMinutes: 60,
  agentScope: "active",
  showRevoked: false,
  hideUnconnectedWork: true,
  hideDoneWork: true,
  edgeMode: "active",
  compact: false,
  showPulses: true,
  showUntracked: true,
};

const VALID_WINDOWS = new Set<number>([15, 60, 360, 1440]);
const VALID_AGENT_SCOPES = new Set<string>(["active", "recent", "all"]);
const VALID_EDGE_MODES = new Set<string>(["active", "all", "focus"]);

function prefsStorageKey(embedded: boolean) {
  return contextStorageKey("live-view", embedded ? "embedded" : "page");
}

function readPreferences(embedded: boolean): LivePreferences {
  const raw = localStore.read(prefsStorageKey(embedded)).value;
  if (!raw) return DEFAULT_PREFS;
  try {
    const parsed = JSON.parse(raw) as Partial<Record<keyof LivePreferences, unknown>>;
    return {
      roadmapIds:
        !embedded && Array.isArray(parsed.roadmapIds)
          ? parsed.roadmapIds.filter((id): id is string => typeof id === "string" && id.length > 0)
          : [],
      windowMinutes:
        typeof parsed.windowMinutes === "number" && VALID_WINDOWS.has(parsed.windowMinutes)
          ? (parsed.windowMinutes as WindowMinutes)
          : DEFAULT_PREFS.windowMinutes,
      agentScope:
        typeof parsed.agentScope === "string" && VALID_AGENT_SCOPES.has(parsed.agentScope)
          ? (parsed.agentScope as AgentScope)
          : DEFAULT_PREFS.agentScope,
      showRevoked:
        typeof parsed.showRevoked === "boolean" ? parsed.showRevoked : DEFAULT_PREFS.showRevoked,
      hideUnconnectedWork:
        typeof parsed.hideUnconnectedWork === "boolean"
          ? parsed.hideUnconnectedWork
          : DEFAULT_PREFS.hideUnconnectedWork,
      hideDoneWork:
        typeof parsed.hideDoneWork === "boolean" ? parsed.hideDoneWork : DEFAULT_PREFS.hideDoneWork,
      edgeMode:
        typeof parsed.edgeMode === "string" && VALID_EDGE_MODES.has(parsed.edgeMode)
          ? (parsed.edgeMode as EdgeMode)
          : DEFAULT_PREFS.edgeMode,
      compact: typeof parsed.compact === "boolean" ? parsed.compact : DEFAULT_PREFS.compact,
      showPulses:
        typeof parsed.showPulses === "boolean" ? parsed.showPulses : DEFAULT_PREFS.showPulses,
      showUntracked:
        typeof parsed.showUntracked === "boolean"
          ? parsed.showUntracked
          : DEFAULT_PREFS.showUntracked,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

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
 * The live read for a window, and for one or more roadmap trees when given. Passing a single
 * roadmap ID builds the same address as a roadmap's Live panel so they share one cached answer.
 */
export function liveAddress(
  roadmapId: string | string[] | undefined,
  windowMinutes: number,
  includeIdle: boolean,
  includeRevoked?: boolean,
) {
  const params = new URLSearchParams({
    windowMinutes: String(windowMinutes),
    includeIdle: String(includeIdle),
  });
  if (includeRevoked !== undefined) {
    params.set("includeRevoked", String(includeRevoked));
  }
  if (Array.isArray(roadmapId)) {
    if (roadmapId.length === 1) {
      params.set("roadmapId", roadmapId[0]);
    } else {
      for (const id of roadmapId) {
        if (id) params.append("roadmapIds", id);
      }
    }
  } else if (roadmapId) {
    params.set("roadmapId", roadmapId);
  }
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

function RoadmapTreePicker({
  roadmaps,
  selectedIds,
  onChange,
}: {
  roadmaps: LiveRoadmapOption[];
  selectedIds: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [filterText, setFilterText] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const containerRef = useRef<HTMLDivElement>(null);
  const tree = useMemo(() => buildRoadmapTree(roadmaps), [roadmaps]);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const query = filterText.trim().toLocaleLowerCase();
  const visibleNodeIds = useMemo(() => {
    if (!query) return null;
    const keep = new Set<string>();
    for (const roadmap of roadmaps) {
      if (roadmap.title.toLocaleLowerCase().includes(query)) {
        keep.add(roadmap.id);
        for (const ancestor of roadmapAncestors(roadmap.id, tree.parentOf)) {
          keep.add(ancestor);
        }
      }
    }
    return keep;
  }, [query, roadmaps, tree]);

  const autoExpanded = useMemo(() => {
    const ids = new Set<string>();
    for (const id of selectedIds) {
      for (const ancestor of roadmapAncestors(id, tree.parentOf)) {
        ids.add(ancestor);
      }
    }
    return ids;
  }, [selectedIds, tree]);

  const triggerLabel =
    selectedIds.length === 0
      ? "All roadmaps"
      : selectedIds.length === 1
        ? tree.byId.get(selectedIds[0])?.title ?? "1 roadmap tree"
        : `${selectedIds.length} roadmap trees`;

  const toggleExpand = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const renderBranch = (parentId: string | null, ancestorSelected: boolean): ReactNode => {
    const items = (tree.children.get(parentId) ?? []).filter(
      (roadmap) => !visibleNodeIds || visibleNodeIds.has(roadmap.id),
    );
    if (items.length === 0) return null;
    return (
      <ul className="live-tree-list">
        {items.map((roadmap) => {
          const childItems = (tree.children.get(roadmap.id) ?? []).filter(
            (child) => !visibleNodeIds || visibleNodeIds.has(child.id),
          );
          const totalDescendants = tree.descendantCount.get(roadmap.id) ?? 0;
          const isSelected = selectedSet.has(roadmap.id);
          const covered = ancestorSelected;
          const isExpanded =
            Boolean(query) || expanded.has(roadmap.id) || autoExpanded.has(roadmap.id);
          return (
            <li key={roadmap.id} className="live-tree-item">
              <div className="live-tree-row">
                {childItems.length > 0 ? (
                  <button
                    type="button"
                    className="live-tree-expand"
                    aria-expanded={isExpanded}
                    aria-label={`${isExpanded ? "Collapse" : "Expand"} ${roadmap.title}`}
                    onClick={() => toggleExpand(roadmap.id)}
                  >
                    <Icon name={isExpanded ? "collapse" : "expand"} size={13} />
                  </button>
                ) : (
                  <span className="live-tree-spacer" aria-hidden="true" />
                )}
                <label className={`live-tree-option${covered ? " is-covered" : ""}`}>
                  <input
                    type="checkbox"
                    checked={isSelected || covered}
                    disabled={covered}
                    onChange={() =>
                      onChange(toggleRoadmapTreeSelection(selectedIds, roadmap.id, tree))
                    }
                  />
                  <span className="live-tree-title" title={roadmap.title}>
                    {roadmap.title}
                  </span>
                  {totalDescendants > 0 && (
                    <span className="live-tree-meta">
                      {totalDescendants} sub
                    </span>
                  )}
                  {roadmap.openQuestions > 0 && (
                    <span className="live-tree-meta is-question">
                      {roadmap.openQuestions}Q
                    </span>
                  )}
                </label>
              </div>
              {childItems.length > 0 && isExpanded && renderBranch(roadmap.id, isSelected || covered)}
            </li>
          );
        })}
      </ul>
    );
  };

  return (
    <div className="live-popover-wrap" ref={containerRef}>
      <button
        type="button"
        className={`live-tree-trigger${selectedIds.length > 0 ? " is-filtered" : ""}`}
        aria-label="Filter by roadmap trees"
        aria-expanded={open}
        aria-haspopup="dialog"
        title={triggerLabel}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="roadmaps" size={14} />
        <span className="live-tree-trigger-label">{triggerLabel}</span>
        <Icon name={open ? "collapse" : "expand"} size={13} />
      </button>
      {open && (
        <div
          className="live-popover live-tree-popover"
          role="dialog"
          aria-label="Roadmap trees filter"
        >
          <div className="live-popover-head">
            <strong>Roadmap trees</strong>
            {selectedIds.length > 0 && (
              <button
                type="button"
                className="quiet"
                onClick={() => onChange([])}
              >
                All roadmaps
              </button>
            )}
          </div>
          {roadmaps.length > 5 && (
            <input
              type="search"
              className="live-popover-search"
              aria-label="Search roadmaps"
              placeholder="Find roadmap tree…"
              value={filterText}
              onChange={(event) => setFilterText(event.target.value)}
            />
          )}
          <div className="live-tree-scroll">
            {roadmaps.length === 0 ? (
              <p className="live-muted">No roadmaps yet.</p>
            ) : visibleNodeIds && visibleNodeIds.size === 0 ? (
              <p className="live-muted">No roadmap matches that search.</p>
            ) : (
              renderBranch(null, false)
            )}
          </div>
          <p className="live-popover-hint">
            Selecting a roadmap includes all of its sub-roadmaps.
          </p>
        </div>
      )}
    </div>
  );
}

function DisplayMenu({
  mode,
  hideUnconnectedWork,
  onHideUnconnectedWork,
  hideDoneWork,
  onHideDoneWork,
  edgeMode,
  onEdgeMode,
  showPulses,
  onShowPulses,
  showUntracked,
  onShowUntracked,
  onResetDefaults,
}: {
  mode: Mode;
  hideUnconnectedWork: boolean;
  onHideUnconnectedWork: (value: boolean) => void;
  hideDoneWork: boolean;
  onHideDoneWork: (value: boolean) => void;
  edgeMode: EdgeMode;
  onEdgeMode: (value: EdgeMode) => void;
  showPulses: boolean;
  onShowPulses: (value: boolean) => void;
  showUntracked: boolean;
  onShowUntracked: (value: boolean) => void;
  onResetDefaults: () => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="live-popover-wrap" ref={containerRef}>
      <button
        type="button"
        aria-label="Display cleanup options"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
      >
        Display
      </button>
      {open && (
        <div
          className="live-popover live-display-popover"
          role="dialog"
          aria-label="Display cleanup options"
        >
          <div className="live-popover-head">
            <strong>Clean up view</strong>
            <button type="button" className="quiet" onClick={onResetDefaults}>
              Reset
            </button>
          </div>
          <label className="live-menu-check">
            <input
              type="checkbox"
              checked={hideUnconnectedWork}
              onChange={(event) => onHideUnconnectedWork(event.target.checked)}
            />
            Hide unconnected work
          </label>
          <label className="live-menu-check">
            <input
              type="checkbox"
              checked={hideDoneWork}
              onChange={(event) => onHideDoneWork(event.target.checked)}
            />
            Hide done steps &amp; items
          </label>
          {mode === "flow" && (
            <div className="live-menu-section">
              <span>Connection lines</span>
              <div className="live-control-group" role="group" aria-label="Connection lines">
                {EDGE_MODES.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={edgeMode === option.value}
                    onClick={() => onEdgeMode(option.value)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          <label className="live-menu-check">
            <input
              type="checkbox"
              checked={showPulses}
              onChange={(event) => onShowPulses(event.target.checked)}
            />
            Show recent pulses
          </label>
          <label className="live-menu-check">
            <input
              type="checkbox"
              checked={showUntracked}
              onChange={(event) => onShowUntracked(event.target.checked)}
            />
            Show untracked local sessions
          </label>
        </div>
      )}
    </div>
  );
}

const KPI_LABELS: Record<KpiFilter, string> = {
  needsYou: "Needs you",
  activeAgents: "Active agents",
  overlaps: "Overlaps",
  overCap: "Over-cap & queued repos",
  waitingOnAgents: "Waiting on agents",
};

export function Live({
  roadmapId: pinnedRoadmapId,
  embedded = false,
}: {
  roadmapId?: string;
  embedded?: boolean;
}) {
  const initial = useMemo(() => readPreferences(embedded), [embedded]);
  const [selectedRoadmapIds, setSelectedRoadmapIds] = useState<string[]>(initial.roadmapIds);
  const [windowMinutes, setWindowMinutes] = useState<WindowMinutes>(initial.windowMinutes);
  const [agentScope, setAgentScope] = useState<AgentScope>(initial.agentScope);
  const [showRevoked, setShowRevoked] = useState<boolean>(initial.showRevoked);
  const [hideUnconnectedWork, setHideUnconnectedWork] = useState<boolean>(
    initial.hideUnconnectedWork,
  );
  const [hideDoneWork, setHideDoneWork] = useState<boolean>(initial.hideDoneWork);
  const [edgeMode, setEdgeMode] = useState<EdgeMode>(initial.edgeMode);
  const [compact, setCompact] = useState<boolean>(initial.compact);
  const [showPulses, setShowPulses] = useState<boolean>(initial.showPulses);
  const [showUntracked, setShowUntracked] = useState<boolean>(initial.showUntracked);
  const [search, setSearch] = useState("");
  const [kpiFilter, setKpiFilter] = useState<KpiFilter | null>(null);
  const [paused, setPaused] = useState(false);
  const [mode, setMode] = useState<Mode>("flow");
  const [focus, setFocus] = useState<Focus | null>(null);
  const [isolateFocus, setIsolateFocus] = useState(false);
  const [selectedPulseId, setSelectedPulseId] = useState<string | null>(null);

  const effectiveRoadmaps = pinnedRoadmapId ? [pinnedRoadmapId] : selectedRoadmapIds;
  const includeIdle = agentScope === "all";
  const query = useData<LiveSnapshot>(
    liveAddress(
      effectiveRoadmaps.length > 0 ? effectiveRoadmaps : undefined,
      windowMinutes,
      includeIdle,
      showRevoked,
    ),
    paused ? undefined : LIVE_REFRESH_MS,
    { keepPrevious: true },
  );
  const snapshot = query.data;

  // Persist view choices per Worklog data context.
  useEffect(() => {
    localStore.write(
      prefsStorageKey(embedded),
      JSON.stringify({
        roadmapIds: pinnedRoadmapId ? [] : selectedRoadmapIds,
        windowMinutes,
        agentScope,
        showRevoked,
        hideUnconnectedWork,
        hideDoneWork,
        edgeMode,
        compact,
        showPulses,
        showUntracked,
      } satisfies LivePreferences),
    );
  }, [
    embedded,
    pinnedRoadmapId,
    selectedRoadmapIds,
    windowMinutes,
    agentScope,
    showRevoked,
    hideUnconnectedWork,
    hideDoneWork,
    edgeMode,
    compact,
    showPulses,
    showUntracked,
  ]);

  // If a previously persisted roadmap was archived or deleted, recover instead of staying stuck on 404.
  useEffect(() => {
    if (
      !pinnedRoadmapId &&
      selectedRoadmapIds.length > 0 &&
      query.error &&
      /not found/i.test(query.error.message)
    ) {
      setSelectedRoadmapIds([]);
    }
  }, [pinnedRoadmapId, selectedRoadmapIds, query.error]);

  const scopeKey = effectiveRoadmaps.join(",");
  useEffect(() => {
    setFocus(null);
    setIsolateFocus(false);
    setSelectedPulseId(null);
  }, [scopeKey, windowMinutes, agentScope, showRevoked]);

  const pulse =
    selectedPulseId === null
      ? null
      : snapshot?.pulses.find((event) => event.id === selectedPulseId) ?? null;

  useEffect(() => {
    if (selectedPulseId && snapshot && !snapshot.pulses.some((event) => event.id === selectedPulseId)) {
      setSelectedPulseId(null);
      setFocus(null);
      setIsolateFocus(false);
    }
  }, [selectedPulseId, snapshot]);

  const filtered = useMemo(() => {
    if (!snapshot) return null;
    return filterLiveSnapshot(snapshot, {
      agentScope,
      showRevoked,
      hideUnconnectedWork,
      hideDoneWork,
      search,
      kpiFilter,
      isolateFocus,
      focus,
      pulse,
    });
  }, [
    snapshot,
    agentScope,
    showRevoked,
    hideUnconnectedWork,
    hideDoneWork,
    search,
    kpiFilter,
    isolateFocus,
    focus,
    pulse,
  ]);

  const view = filtered?.view ?? null;
  const hidden: HiddenCounts = filtered?.hidden ?? {
    idleOrStaleAgents: 0,
    revokedAgents: 0,
    unconnectedWork: 0,
    doneWork: 0,
  };

  const selectPulse = (event: LivePulse | null) => {
    setSelectedPulseId(event?.id ?? null);
    setFocus(event ? { kind: "pulse", id: event.id } : null);
    if (!event) setIsolateFocus(false);
  };
  const selectFocus = (next: Focus) => {
    setSelectedPulseId(null);
    setFocus(next);
  };
  const clearFocus = () => {
    setSelectedPulseId(null);
    setFocus(null);
    setIsolateFocus(false);
  };
  const resetDefaults = () => {
    if (!pinnedRoadmapId) setSelectedRoadmapIds([]);
    setWindowMinutes(DEFAULT_PREFS.windowMinutes);
    setAgentScope(DEFAULT_PREFS.agentScope);
    setShowRevoked(DEFAULT_PREFS.showRevoked);
    setHideUnconnectedWork(DEFAULT_PREFS.hideUnconnectedWork);
    setHideDoneWork(DEFAULT_PREFS.hideDoneWork);
    setEdgeMode(DEFAULT_PREFS.edgeMode);
    setCompact(DEFAULT_PREFS.compact);
    setShowPulses(DEFAULT_PREFS.showPulses);
    setShowUntracked(DEFAULT_PREFS.showUntracked);
    setSearch("");
    setKpiFilter(null);
    clearFocus();
  };

  const hasActiveFilters =
    (!pinnedRoadmapId && selectedRoadmapIds.length > 0) ||
    Boolean(search.trim()) ||
    kpiFilter !== null ||
    isolateFocus ||
    agentScope !== DEFAULT_PREFS.agentScope ||
    showRevoked !== DEFAULT_PREFS.showRevoked ||
    hideUnconnectedWork !== DEFAULT_PREFS.hideUnconnectedWork ||
    hideDoneWork !== DEFAULT_PREFS.hideDoneWork ||
    edgeMode !== DEFAULT_PREFS.edgeMode ||
    compact !== DEFAULT_PREFS.compact ||
    !showPulses ||
    !showUntracked;

  const snapshotEmpty =
    snapshot != null &&
    snapshot.work.length === 0 &&
    snapshot.agents.length === 0 &&
    snapshot.repositories.length === 0 &&
    snapshot.untrackedSessions.length === 0;

  const viewEmpty =
    view != null &&
    view.work.length === 0 &&
    view.agents.length === 0 &&
    view.repositories.length === 0 &&
    (!showUntracked || view.untrackedSessions.length === 0);

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
          <RoadmapTreePicker
            roadmaps={snapshot?.roadmaps ?? []}
            selectedIds={selectedRoadmapIds}
            onChange={setSelectedRoadmapIds}
          />
        )}
        <input
          type="search"
          className="live-search"
          aria-label="Filter live view"
          placeholder="Filter agents, work, repos…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
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
        <div className="live-control-group" role="group" aria-label="Agent visibility">
          {AGENT_SCOPES.map((option) => (
            <button
              key={option.value}
              type="button"
              title={option.title}
              aria-pressed={agentScope === option.value}
              onClick={() => setAgentScope(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <label className="live-check">
          <input
            type="checkbox"
            checked={showRevoked}
            onChange={(event) => setShowRevoked(event.target.checked)}
          />
          Show revoked
        </label>
        <div className="live-control-group" role="group" aria-label="Visualization mode">
          <button type="button" aria-pressed={mode === "flow"} onClick={() => setMode("flow")}>
            Flow map
          </button>
          <button type="button" aria-pressed={mode === "lanes"} onClick={() => setMode("lanes")}>
            Swimlanes
          </button>
        </div>
        <button
          type="button"
          className={compact ? "is-pressed" : undefined}
          aria-pressed={compact}
          onClick={() => setCompact((value) => !value)}
        >
          Compact
        </button>
        <DisplayMenu
          mode={mode}
          hideUnconnectedWork={hideUnconnectedWork}
          onHideUnconnectedWork={setHideUnconnectedWork}
          hideDoneWork={hideDoneWork}
          onHideDoneWork={setHideDoneWork}
          edgeMode={edgeMode}
          onEdgeMode={setEdgeMode}
          showPulses={showPulses}
          onShowPulses={setShowPulses}
          showUntracked={showUntracked}
          onShowUntracked={setShowUntracked}
          onResetDefaults={resetDefaults}
        />
        <button type="button" aria-pressed={paused} onClick={() => setPaused((value) => !value)}>
          {paused ? "Resume live updates" : "Pause live updates"}
        </button>
      </div>

      {snapshot && (
        <FilterStrip
          roadmaps={snapshot.roadmaps}
          selectedRoadmapIds={pinnedRoadmapId ? [] : selectedRoadmapIds}
          onRemoveRoadmap={(id) =>
            setSelectedRoadmapIds((current) => current.filter((item) => item !== id))
          }
          search={search}
          onClearSearch={() => setSearch("")}
          kpiFilter={kpiFilter}
          onClearKpi={() => setKpiFilter(null)}
          isolateFocus={isolateFocus}
          onClearIsolate={() => setIsolateFocus(false)}
          hidden={hidden}
          onShowIdle={() => setAgentScope("recent")}
          onShowRevoked={() => setShowRevoked(true)}
          onShowUnconnected={() => setHideUnconnectedWork(false)}
          onShowDone={() => setHideDoneWork(false)}
          hasActiveFilters={hasActiveFilters}
          onReset={resetDefaults}
        />
      )}

      <LiveDependencyWaits
        roadmapId={pinnedRoadmapId}
        roadmapIds={pinnedRoadmapId ? undefined : selectedRoadmapIds}
        paused={paused}
        refreshMs={LIVE_REFRESH_MS}
      />
      <ErrorMessage error={query.error} />
      {!snapshot && query.isPending && <p className="live-loading">Reading current activity...</p>}
      {snapshot && view && (
        <>
          <Summary
            snapshot={snapshot}
            kpiFilter={kpiFilter}
            onToggleKpi={(next) =>
              setKpiFilter((current) => (current === next ? null : next))
            }
          />
          {snapshotEmpty ? (
            <Empty>
              <strong>Nothing is active in this window.</strong>
              <span>
                {" "}
                Widen the Activity window, switch Agents to All, or tick Show revoked.
              </span>
            </Empty>
          ) : viewEmpty ? (
            <Empty>
              <strong>Everything in this window is hidden by your current view filters.</strong>
              <span className="live-empty-actions">
                {hidden.idleOrStaleAgents > 0 && (
                  <button type="button" onClick={() => setAgentScope("recent")}>
                    Show {hidden.idleOrStaleAgents} idle/stale{" "}
                    {hidden.idleOrStaleAgents === 1 ? "agent" : "agents"}
                  </button>
                )}
                {hidden.unconnectedWork > 0 && (
                  <button type="button" onClick={() => setHideUnconnectedWork(false)}>
                    Show {hidden.unconnectedWork} quiet work{" "}
                    {hidden.unconnectedWork === 1 ? "card" : "cards"}
                  </button>
                )}
                {hidden.doneWork > 0 && (
                  <button type="button" onClick={() => setHideDoneWork(false)}>
                    Show {hidden.doneWork} done
                  </button>
                )}
                {!showRevoked && (
                  <button type="button" onClick={() => setShowRevoked(true)}>
                    Show revoked
                  </button>
                )}
                {hasActiveFilters && (
                  <button type="button" onClick={resetDefaults}>
                    Reset filters
                  </button>
                )}
              </span>
            </Empty>
          ) : (
            <>
              {focus && (
                <Inspector
                  snapshot={snapshot}
                  focus={focus}
                  pulse={pulse}
                  isolated={isolateFocus}
                  onToggleIsolate={() => setIsolateFocus((value) => !value)}
                  clear={clearFocus}
                />
              )}
              {mode === "flow" ? (
                <FlowMap
                  snapshot={view}
                  focus={focus}
                  pulse={pulse}
                  edgeMode={edgeMode}
                  compact={compact}
                  onFocus={selectFocus}
                />
              ) : (
                <Swimlanes snapshot={view} focus={focus} pulse={pulse} onFocus={selectFocus} />
              )}
              {showUntracked && <Untracked snapshot={view} />}
            </>
          )}
          {showPulses && (
            <PulseTape
              pulses={view.pulses}
              totalPulses={snapshot.pulses.length}
              selected={selectedPulseId}
              onSelect={selectPulse}
            />
          )}
          <AccessibleTables snapshot={view} />
        </>
      )}
    </section>
  );
}

function FilterStrip({
  roadmaps,
  selectedRoadmapIds,
  onRemoveRoadmap,
  search,
  onClearSearch,
  kpiFilter,
  onClearKpi,
  isolateFocus,
  onClearIsolate,
  hidden,
  onShowIdle,
  onShowRevoked,
  onShowUnconnected,
  onShowDone,
  hasActiveFilters,
  onReset,
}: {
  roadmaps: LiveRoadmapOption[];
  selectedRoadmapIds: string[];
  onRemoveRoadmap: (id: string) => void;
  search: string;
  onClearSearch: () => void;
  kpiFilter: KpiFilter | null;
  onClearKpi: () => void;
  isolateFocus: boolean;
  onClearIsolate: () => void;
  hidden: HiddenCounts;
  onShowIdle: () => void;
  onShowRevoked: () => void;
  onShowUnconnected: () => void;
  onShowDone: () => void;
  hasActiveFilters: boolean;
  onReset: () => void;
}) {
  const byId = useMemo(
    () => new Map(roadmaps.map((roadmap) => [roadmap.id, roadmap.title])),
    [roadmaps],
  );
  const anyHidden =
    hidden.idleOrStaleAgents > 0 ||
    hidden.revokedAgents > 0 ||
    hidden.unconnectedWork > 0 ||
    hidden.doneWork > 0;
  const anyActiveChips =
    selectedRoadmapIds.length > 0 ||
    Boolean(search.trim()) ||
    kpiFilter !== null ||
    isolateFocus;
  if (!anyHidden && !anyActiveChips && !hasActiveFilters) return null;

  return (
    <div className="live-filter-strip" aria-label="Active filters and hidden items">
      {selectedRoadmapIds.map((id) => (
        <button
          key={id}
          type="button"
          className="live-filter-pill is-active"
          onClick={() => onRemoveRoadmap(id)}
          title={`Remove roadmap tree: ${byId.get(id) ?? id}`}
        >
          <span>{byId.get(id) ?? "Roadmap tree"}</span>
          <span aria-hidden="true">×</span>
        </button>
      ))}
      {kpiFilter && (
        <button
          type="button"
          className="live-filter-pill is-active"
          onClick={onClearKpi}
        >
          <span>{KPI_LABELS[kpiFilter]}</span>
          <span aria-hidden="true">×</span>
        </button>
      )}
      {search.trim() && (
        <button
          type="button"
          className="live-filter-pill is-active"
          onClick={onClearSearch}
        >
          <span>Filter: &ldquo;{search.trim()}&rdquo;</span>
          <span aria-hidden="true">×</span>
        </button>
      )}
      {isolateFocus && (
        <button
          type="button"
          className="live-filter-pill is-active"
          onClick={onClearIsolate}
        >
          <span>Isolated selection</span>
          <span aria-hidden="true">×</span>
        </button>
      )}
      {hidden.idleOrStaleAgents > 0 && (
        <button
          type="button"
          className="live-filter-pill is-hidden-hint"
          onClick={onShowIdle}
        >
          {hidden.idleOrStaleAgents} idle/stale hidden · Show
        </button>
      )}
      {hidden.revokedAgents > 0 && (
        <button
          type="button"
          className="live-filter-pill is-hidden-hint"
          onClick={onShowRevoked}
        >
          {hidden.revokedAgents} revoked hidden · Show
        </button>
      )}
      {hidden.unconnectedWork > 0 && (
        <button
          type="button"
          className="live-filter-pill is-hidden-hint"
          onClick={onShowUnconnected}
        >
          {hidden.unconnectedWork} quiet work hidden · Show
        </button>
      )}
      {hidden.doneWork > 0 && (
        <button
          type="button"
          className="live-filter-pill is-hidden-hint"
          onClick={onShowDone}
        >
          {hidden.doneWork} done hidden · Show
        </button>
      )}
      {hasActiveFilters && (
        <button type="button" className="quiet live-reset-filters" onClick={onReset}>
          Reset filters
        </button>
      )}
    </div>
  );
}

function Summary({
  snapshot,
  kpiFilter,
  onToggleKpi,
}: {
  snapshot: LiveSnapshot;
  kpiFilter: KpiFilter | null;
  onToggleKpi: (filter: KpiFilter) => void;
}) {
  const needsBreakdown = `${snapshot.summary.pendingRequests} requests · ${snapshot.summary.waitingProposals} proposals · ${snapshot.summary.openQuestions} open questions`;
  const metrics: {
    label: string;
    value: number;
    filter?: KpiFilter;
    title?: string;
  }[] = [
    { label: "Active agents", value: snapshot.summary.activeAgents, filter: "activeAgents" },
    { label: "Working chats", value: snapshot.summary.workingChats },
    {
      label: "Work in flight",
      value: snapshot.summary.activeWorkItems + snapshot.summary.activeSteps,
    },
    { label: "Repositories", value: snapshot.summary.activeRepos },
    { label: "Overlaps", value: snapshot.summary.repoOverlaps, filter: "overlaps" },
    { label: "Over-cap repos", value: snapshot.summary.overCapRepos, filter: "overCap" },
    {
      label: "Needs you",
      value: snapshot.summary.needsYou,
      filter: "needsYou",
      title: needsBreakdown,
    },
    {
      label: "Waiting on agents",
      value: snapshot.summary.waitingOnAgents,
      filter: "waitingOnAgents",
    },
  ];
  return (
    <dl className="live-summary">
      {metrics.map((metric) => {
        const active = metric.filter !== undefined && kpiFilter === metric.filter;
        return (
          <div
            key={metric.label}
            className={
              [metric.filter ? "is-interactive" : "", active ? "is-filtered" : ""]
                .filter(Boolean)
                .join(" ") || undefined
            }
            title={metric.title}
          >
            <dt>
              {metric.filter ? (
                <button
                  type="button"
                  className="live-summary-trigger"
                  aria-pressed={active}
                  onClick={() => onToggleKpi(metric.filter!)}
                >
                  {metric.label}
                </button>
              ) : (
                metric.label
              )}
            </dt>
            <dd>{metric.value}</dd>
          </div>
        );
      })}
    </dl>
  );
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
  edgeMode,
  compact,
  onFocus,
}: {
  snapshot: LiveSnapshot;
  focus: Focus | null;
  pulse: LivePulse | null;
  edgeMode: EdgeMode;
  compact: boolean;
  onFocus: (focus: Focus) => void;
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const cards = useRef(new Map<string, HTMLElement>());
  const [version, setVersion] = useState(0);
  const related = liveConnections(snapshot, focus, pulse);
  const visibleWorkEdges = snapshot.workEdges.filter((edge) => {
    if (edgeMode === "all") return true;
    if (related.edges.has(edge.id)) return true;
    if (edgeMode === "focus") return false;
    return edge.active && !edge.stale;
  });
  const visibleRepoEdges = snapshot.repoEdges.filter((edge) => {
    if (edgeMode === "all") return true;
    if (related.edges.has(edge.id)) return true;
    if (edgeMode === "focus") return false;
    return edge.active && !edge.stale;
  });
  const visibleCommEdges = snapshot.commEdges.filter((edge) => {
    if (edgeMode === "all") return true;
    if (related.edges.has(edge.id)) return true;
    if (edgeMode === "focus") return false;
    return edge.status === "pending";
  });
  const hasPerson = snapshot.commEdges.some(
    (edge) => edge.fromId === "local-user" || edge.toId === "local-user",
  );

  // Roadmaps nobody works on come first: when the tier wraps, the ones with lines to agents sit
  // on its last row, nearest the agents, so their lines do not pass behind the others.
  const connectedWork = new Set(snapshot.workEdges.map((edge) => edge.workId));
  const allGroups = workGroups(snapshot.work);
  const linked = (group: WorkGroup) => group.nodes.some((node) => connectedWork.has(node.id));
  const groups = [...allGroups.filter((group) => !linked(group)), ...allGroups.filter(linked)];
  const workIndex = new Map(
    groups.flatMap((group) => group.nodes).map((node, index) => [node.id, index]),
  );
  const agents = byBarycenter(snapshot.agents, (id) =>
    snapshot.workEdges
      .filter((edge) => edge.agentId === id)
      .map((edge) => workIndex.get(edge.workId) ?? -1),
  );
  const agentIndex = new Map(agents.map((agent, index) => [agent.id, index + (hasPerson ? 1 : 0)]));
  const repositories = byBarycenter(snapshot.repositories, (id) =>
    snapshot.repoEdges
      .filter((edge) => edge.repositoryId === id)
      .map((edge) => agentIndex.get(edge.agentId) ?? -1),
  );
  const repoIndex = new Map(repositories.map((repo, index) => [repo.id, index]));

  useEffect(() => {
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => setVersion((v) => v + 1));
    if (canvas.current) observer?.observe(canvas.current);
    for (const element of cards.current.values()) observer?.observe(element);
    const resize = () => setVersion((value) => value + 1);
    window.addEventListener("resize", resize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", resize);
    };
  }, [snapshot, compact, edgeMode]);

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
        kind === "work"
          ? workIndex.get(id)
          : kind === "repo"
            ? repoIndex.get(id)
            : kind === "person"
              ? 0
              : agentIndex.get(id);
      const top = 48 + tier * 220;
      boxes.set(nodeKey, { x: 150 + (index ?? 0) * 260, top, bottom: top + 120, width: 240 });
    }
    return nodeKey;
  };
  const lines: Line[] = [
    ...visibleWorkEdges.map((edge) => ({
      id: edge.id,
      from: place("work", edge.workId),
      to: place("agent", edge.agentId),
    })),
    ...visibleRepoEdges.map((edge) => ({
      id: edge.id,
      from: place("agent", edge.agentId),
      to: place("repo", edge.repositoryId),
    })),
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
        ports.set(
          `${side}:${line.id}`,
          own.x + own.width * 0.6 * ((index + 0.5) / list.length - 0.5),
        );
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
    <div className={`live-canvas${compact ? " is-compact" : ""}`} ref={canvas}>
      <svg className="live-wires" aria-label="Active workflow connections">
        <defs>
          <marker
            id="live-arrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>
        {[...visibleWorkEdges, ...visibleRepoEdges].map((edge) => (
          <path
            key={edge.id}
            data-edge-id={edge.id}
            className={edgeClass(
              edge.active,
              edge.stale,
              related.edges.has(edge.id),
              related.active && !related.edges.has(edge.id),
            )}
            d={path(lineById.get(edge.id)!)}
          />
        ))}
        {visibleCommEdges.map((edge, index) => {
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
            <g
              key={edge.id}
              className={`comm-edge comm-${edge.kind} is-${edge.status}${related.edges.has(edge.id) ? " is-highlighted" : ""}${related.active && !related.edges.has(edge.id) ? " is-dimmed" : ""}`}
            >
              <path data-edge-id={edge.id} d={d} markerEnd="url(#live-arrow)" />
              {edge.kind === "role_offer" && (
                <text x={(from.x + to.x) / 2} y={lowest + depth / 2} textAnchor="middle">
                  {edge.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <section className="live-tier live-work-tier" aria-label="Plans and work">
        <h2>Plans &amp; work</h2>
        <div className="live-tier-row">
          {groups.length === 0 && (
            <p className="live-muted">No planned work is moving in this window.</p>
          )}
          {groups.map((group) => (
            <div
              key={group.id || "outside"}
              className="live-work-group"
              role="group"
              aria-label={group.title ?? "Outside roadmaps"}
            >
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
                      <span className={`live-status status-${node.status}`}>
                        <i />
                        {node.kind}
                      </span>
                      {node.needsYou && <span className="live-warning">Needs you</span>}
                    </div>
                    <h3>{node.title}</h3>
                    {/* The group names the roadmap; an item still says which step it sits under. */}
                    {!compact && node.kind === "item" && node.stepTitle && (
                      <p className="live-under">Under {node.stepTitle}</p>
                    )}
                    <footer>
                      <span>
                        {node.agentCount} connected {node.agentCount === 1 ? "agent" : "agents"}
                      </span>
                      {node.needsYou && (
                        <span>
                          {node.openQuestions} questions / {node.waitingProposals} proposals
                        </span>
                      )}
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
          {!hasPerson && agents.length === 0 && (
            <p className="live-muted">No agent matches the current filters.</p>
          )}
          {hasPerson && (
            <Card
              nodeKey={key("person", "local-user")}
              {...nodeState(key("person", "local-user"))}
              setRef={setCard(key("person", "local-user"))}
              onSelect={() => onFocus({ kind: "person", id: "local-user" })}
              className="live-person-card"
            >
              <Avatar kind="human" size={28} />
              <div>
                <h3>You</h3>
                {!compact && <p>Requests, replies and handoffs</p>}
              </div>
            </Card>
          )}
          {agents.map((agent) => {
            const nodeKey = key("agent", agent.id);
            const age = formatRelativeAge(agent.lastAt, snapshot.generatedAt);
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
                  <span className="live-agent-avatar">
                    <Avatar name={agent.avatar} size={compact ? 26 : 32} />
                  </span>
                  <div className="live-agent-title">
                    <h3>{agent.label}</h3>
                    <div className="live-agent-subline">
                      <span className={`live-state state-${agent.state}`}>
                        {STATES[agent.state]}
                      </span>
                      {age && (
                        <span className="live-age" title={agent.lastAt ?? undefined}>
                          {age}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                {!compact && (
                  <>
                    <p className="live-provenance">
                      Declared: <strong>{DECLARED[agent.declaredState]}</strong>
                      <span aria-hidden="true"> / </span>
                      Session: <strong>{sessionText(agent)}</strong>
                    </p>
                    {agent.roles.length > 0 && (
                      <div className="live-chips">
                        {agent.roles.map((role) => (
                          <span key={role}>{role}</span>
                        ))}
                      </div>
                    )}
                    {agent.chats.length > 0 && (
                      <div className="live-chat-links">
                        {agent.chats.map((chat) => (
                          <a
                            key={chat.id}
                            href={chat.href}
                            onClick={(event) => event.stopPropagation()}
                          >
                            {chat.title}
                          </a>
                        ))}
                      </div>
                    )}
                    {agent.latestNote && (
                      <p className="live-note" title={agent.latestNote}>
                        {agent.latestNote}
                      </p>
                    )}
                  </>
                )}
              </Card>
            );
          })}
        </div>
      </section>
      <section className="live-tier live-repo-tier" aria-label="Repositories">
        <h2>Repositories</h2>
        <div className="live-tier-row">
          {repositories.length === 0 && (
            <p className="live-muted">No repository activity in this window.</p>
          )}
          {repositories.map((repo) => {
            const nodeKey = key("repo", repo.id);
            const shownBranches = repo.branches.slice(0, MAX_CARD_BRANCHES);
            const hiddenBranchCount = Math.max(0, repo.branches.length - shownBranches.length);
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
                {!compact && shownBranches.length > 0 && (
                  <div className="live-branches">
                    {shownBranches.map((branch) => (
                      <code key={branch}>{branch}</code>
                    ))}
                    {hiddenBranchCount > 0 && (
                      <span
                        className="live-neutral"
                        title={repo.branches.slice(MAX_CARD_BRANCHES).join(", ")}
                      >
                        +{hiddenBranchCount} more
                      </span>
                    )}
                  </div>
                )}
                {!compact && (
                  <footer className="live-links">
                    <a href={repo.href} onClick={(event) => event.stopPropagation()}>
                      Open repository
                    </a>
                    {repo.discussionId && (
                      <a
                        href={`#discussions/${repo.discussionId}`}
                        onClick={(event) => event.stopPropagation()}
                      >
                        Repository thread
                      </a>
                    )}
                  </footer>
                )}
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
          {repo.doingStepCount > 0 && <span>{repo.doingStepCount} doing</span>}
          {repo.frozenWaveName && (
            <span className="live-warning">Frozen: {repo.frozenWaveName}</span>
          )}
          {repo.pendingRepinsCount > 0 && (
            <span className="live-warning">{repo.pendingRepinsCount} re-pin pending</span>
          )}
        </div>
      )}
      {repo.overlapCount >= 2 && repo.effectiveWipCap == null && (
        <strong className="live-warning">Overlap: {repo.overlapCount} agents</strong>
      )}
    </>
  );
}

function Inspector({
  snapshot,
  focus,
  pulse,
  isolated,
  onToggleIsolate,
  clear,
}: {
  snapshot: LiveSnapshot;
  focus: Focus;
  pulse: LivePulse | null;
  isolated: boolean;
  onToggleIsolate: () => void;
  clear: () => void;
}) {
  const work = focus.kind === "work" ? snapshot.work.find((node) => node.id === focus.id) : null;
  const agent =
    focus.kind === "agent" ? snapshot.agents.find((node) => node.id === focus.id) : null;
  const repo =
    focus.kind === "repo" ? snapshot.repositories.find((node) => node.id === focus.id) : null;
  const title = pulse?.summary ?? work?.title ?? agent?.label ?? repo?.name ?? "You";
  const agentAge = agent ? formatRelativeAge(agent.lastAt, snapshot.generatedAt) : null;
  return (
    <aside className="live-inspector" aria-label="Selection inspector">
      <div className="live-inspector-body">
        <span>Focused</span>
        <strong>{title}</strong>
        {agent && (
          <p className="live-inspector-meta">
            {STATES[agent.state]}
            {agentAge ? ` · ${agentAge}` : ""} · Declared: {DECLARED[agent.declaredState]} ·
            Session: {sessionText(agent)}
            {agent.latestNote ? ` · ${agent.latestNote}` : ""}
          </p>
        )}
        {repo && repo.branches.length > 0 && (
          <div className="live-branches">
            {repo.branches.map((branch) => (
              <code key={branch}>{branch}</code>
            ))}
          </div>
        )}
        {work && (
          <p className="live-inspector-meta">
            {work.kind} · {work.status}
            {work.roadmapTitle && work.kind !== "roadmap" ? ` · ${work.roadmapTitle}` : ""}
            {work.stepTitle && work.kind === "item" ? ` / ${work.stepTitle}` : ""}
          </p>
        )}
      </div>
      <div className="live-inspector-actions">
        <button type="button" aria-pressed={isolated} onClick={onToggleIsolate}>
          {isolated ? "Show all" : "Isolate"}
        </button>
        {work &&
          deepLink(
            work.href,
            work.kind === "roadmap"
              ? "Open roadmap"
              : work.kind === "step"
                ? "Open step"
                : "Open work item",
          )}
        {work?.waitingProposalIds[0] &&
          deepLink(`#review/${work.waitingProposalIds[0]}`, "Review proposal")}
        {agent && deepLink(agent.href, "Open agent")}
        {agent?.chats[0] && deepLink(agent.chats[0].href, "Open chat")}
        {repo && deepLink(repo.href, "Open repository")}
        {repo?.discussionId && deepLink(`#discussions/${repo.discussionId}`, "Open discussion")}
        {pulse &&
          deepLink(
            pulse.href,
            pulse.proposalId
              ? "Review proposal"
              : pulse.discussionId
                ? "Open discussion"
                : "Open record",
          )}
        <button type="button" onClick={clear}>
          Clear selection
        </button>
      </div>
    </aside>
  );
}

function Swimlanes({
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
  const end = new Date(snapshot.generatedAt).getTime();
  const start = end - snapshot.windowMinutes * 60_000;
  const position = (value: string) =>
    Math.max(0, Math.min(100, ((new Date(value).getTime() - start) / (end - start)) * 100));
  const ticks = Array.from({ length: 5 }, (_, index) => start + ((end - start) * index) / 4);
  const communicative = new Set(["ack_requested", "ack_satisfied", "role_handoff", "message"]);
  const markers = [
    ...snapshot.commEdges
      .filter((edge) => edge.lastAt)
      .map((edge) => ({
        id: edge.id,
        at: edge.lastAt!,
        label: edge.label,
        agentIds: [edge.fromId, edge.toId],
      })),
    ...snapshot.pulses
      .filter((event) => communicative.has(event.kind))
      .map((event) => ({
        id: event.id,
        at: event.at,
        label: event.summary,
        agentIds: [event.actorId, event.targetActorId].filter(Boolean) as string[],
      })),
  ].filter(
    (marker) =>
      new Date(marker.at).getTime() >= start && new Date(marker.at).getTime() <= end,
  );
  const related = liveConnections(snapshot, focus, pulse);
  return (
    <section className="live-lanes" aria-label="Agent activity swimlanes">
      <div className="lane-time-axis">
        <span />
        {ticks.map((tick) => (
          <time key={tick}>{formatTime(new Date(tick).toISOString())}</time>
        ))}
      </div>
      {snapshot.agents.length === 0 && (
        <p className="live-muted live-lanes-empty">No agents match the current filters.</p>
      )}
      {snapshot.agents.map((agent) => {
        const nodeKey = key("agent", agent.id);
        const dimmed = related.active && !related.nodes.has(nodeKey);
        return (
          <div
            key={agent.id}
            className={`lane-row${dimmed ? " is-dimmed" : ""}${related.nodes.has(nodeKey) ? " is-highlighted" : ""}`}
          >
            <button
              className="lane-agent"
              type="button"
              onClick={() => onFocus({ kind: "agent", id: agent.id })}
            >
              <Avatar name={agent.avatar} size={28} />
              <span>
                <strong>{agent.label}</strong>
                <small>{STATES[agent.state]}</small>
              </span>
            </button>
            <div className="lane-track">
              {snapshot.lanes
                .filter((span) => span.agentId === agent.id)
                .map((span) => {
                  const highlighted =
                    (focus?.kind === "agent" && focus.id === span.agentId) ||
                    (span.workId !== null && related.nodes.has(key("work", span.workId))) ||
                    (span.repositoryId !== null &&
                      related.nodes.has(key("repo", span.repositoryId)));
                  return (
                    <LaneSpan
                      key={span.id}
                      span={span}
                      start={start}
                      end={end}
                      highlighted={highlighted}
                      dimmed={related.active && !highlighted}
                    />
                  );
                })}
              {markers
                .filter((marker) => marker.agentIds.includes(agent.id))
                .map((marker) => (
                  <a
                    key={marker.id}
                    className="lane-marker"
                    style={{ left: `${position(marker.at)}%` }}
                    href={
                      snapshot.commEdges.find((edge) => edge.id === marker.id)?.href ??
                      snapshot.pulses.find((event) => event.id === marker.id)?.href
                    }
                    title={marker.label}
                  >
                    <span>{marker.label}</span>
                  </a>
                ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function LaneSpan({
  span,
  start,
  end,
  highlighted,
  dimmed,
}: {
  span: LiveSwimlaneSpan;
  start: number;
  end: number;
  highlighted: boolean;
  dimmed: boolean;
}) {
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
      <span>{span.label}</span>
      {span.repositoryName && (
        <small>
          {span.repositoryName}
          {span.branch ? ` / ${span.branch}` : ""}
        </small>
      )}
    </a>
  );
}

function Untracked({ snapshot }: { snapshot: LiveSnapshot }) {
  if (snapshot.untrackedSessions.length === 0) return null;
  return (
    <aside className="live-untracked">
      <div>
        <p className="live-kicker">Local discovery</p>
        <h2>Running on this machine</h2>
      </div>
      <ul>
        {snapshot.untrackedSessions.map((session) => (
          <li key={session.sessionId}>
            <strong>{session.name}</strong>
            <span>
              {session.state} / {session.model}
            </span>
            <code>{session.cwd}</code>
          </li>
        ))}
      </ul>
      <a className="button" href="#chats">
        Open Chats
      </a>
    </aside>
  );
}

function PulseTape({
  pulses,
  totalPulses,
  selected,
  onSelect,
}: {
  pulses: LivePulse[];
  totalPulses: number;
  selected: string | null;
  onSelect: (event: LivePulse | null) => void;
}) {
  const selectedIndex =
    selected === null ? -1 : pulses.findIndex((event) => event.id === selected);
  const previousIndex = selectedIndex < 0 ? 0 : Math.min(pulses.length - 1, selectedIndex + 1);
  const nextIndex = Math.max(0, selectedIndex - 1);
  return (
    <section className="live-pulses">
      <header>
        <div>
          <p className="live-kicker">Event tape</p>
          <h2>
            Recent pulses
            {pulses.length !== totalPulses && (
              <span className="live-pulse-count">
                {" "}
                ({pulses.length} of {totalPulses})
              </span>
            )}
          </h2>
        </div>
        <div className="live-pulse-controls">
          <button
            type="button"
            disabled={!pulses.length || selectedIndex === pulses.length - 1}
            onClick={() => onSelect(pulses[previousIndex])}
          >
            Previous pulse
          </button>
          <button
            type="button"
            disabled={selectedIndex <= 0}
            onClick={() => onSelect(pulses[nextIndex])}
          >
            Next pulse
          </button>
          <button type="button" onClick={() => onSelect(null)}>
            Live now
          </button>
        </div>
      </header>
      {pulses.length ? (
        <ol>
          {pulses.map((event) => (
            <li key={event.id}>
              <button
                type="button"
                aria-pressed={selected === event.id}
                onClick={() => onSelect(event)}
              >
                <time dateTime={event.at}>{formatTime(event.at)}</time>
                <strong>{event.actorLabel}</strong>
                <span>{event.summary}</span>
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="live-muted">No recent pulses match the current filters.</p>
      )}
      {selectedIndex >= 0 && (
        <a className="live-pulse-open" href={pulses[selectedIndex].href}>
          Open record
        </a>
      )}
    </section>
  );
}

function AccessibleTables({ snapshot }: { snapshot: LiveSnapshot }) {
  const connections = [
    ...snapshot.workEdges.map((edge) => ({
      id: edge.id,
      source: snapshot.work.find((node) => node.id === edge.workId)?.title ?? edge.workId,
      target: snapshot.agents.find((node) => node.id === edge.agentId)?.label ?? edge.agentId,
      kind: edge.via.join(", "),
      state: edge.stale ? "stale" : edge.active ? "active" : "inactive",
    })),
    ...snapshot.repoEdges.map((edge) => ({
      id: edge.id,
      source: snapshot.agents.find((node) => node.id === edge.agentId)?.label ?? edge.agentId,
      target:
        snapshot.repositories.find((node) => node.id === edge.repositoryId)?.name ??
        edge.repositoryId,
      kind: edge.via.join(", "),
      state: edge.stale ? "stale" : edge.active ? "active" : "inactive",
    })),
    ...snapshot.commEdges.map((edge) => ({
      id: edge.id,
      source: edge.fromLabel,
      target: edge.toLabel,
      kind: edge.label || edge.kind,
      state: edge.status,
    })),
  ];
  return (
    <details className="chart-table live-table">
      <summary>Show as table</summary>
      <h3>Active connections</h3>
      <table>
        <thead>
          <tr>
            <th scope="col">From</th>
            <th scope="col">To</th>
            <th scope="col">Connection</th>
            <th scope="col">State</th>
          </tr>
        </thead>
        <tbody>
          {connections.map((edge) => (
            <tr key={edge.id}>
              <th scope="row">{edge.source}</th>
              <td>{edge.target}</td>
              <td>{edge.kind}</td>
              <td>{edge.state}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Recent pulses</h3>
      <table>
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Actor</th>
            <th scope="col">Event</th>
            <th scope="col">Record</th>
          </tr>
        </thead>
        <tbody>
          {snapshot.pulses.map((event) => (
            <tr key={event.id}>
              <th scope="row">
                <time dateTime={event.at}>{formatTime(event.at)}</time>
              </th>
              <td>{event.actorLabel}</td>
              <td>{event.summary}</td>
              <td>
                <a href={event.href}>Open record</a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
