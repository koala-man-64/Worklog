import type {
  LiveAgentNode,
  LiveCommEdge,
  LivePulse,
  LiveRepoNode,
  LiveRoadmapOption,
  LiveSnapshot,
  LiveWorkNode,
} from "./apiTypes";

export type AgentScope = "active" | "recent" | "all";
export type EdgeMode = "active" | "all" | "focus";
export type KpiFilter =
  | "needsYou"
  | "activeAgents"
  | "overlaps"
  | "overCap"
  | "waitingOnAgents";
export type LiveFocus = {
  kind: "work" | "agent" | "repo" | "person" | "pulse";
  id: string;
};

export type LiveFilterOptions = {
  agentScope: AgentScope;
  showRevoked: boolean;
  hideUnconnectedWork: boolean;
  hideDoneWork: boolean;
  search: string;
  kpiFilter: KpiFilter | null;
  isolateFocus: boolean;
  focus: LiveFocus | null;
  pulse: LivePulse | null;
};

export type HiddenCounts = {
  idleOrStaleAgents: number;
  revokedAgents: number;
  unconnectedWork: number;
  doneWork: number;
};

export type RoadmapTreeIndex = {
  byId: Map<string, LiveRoadmapOption>;
  parentOf: Map<string, string | null>;
  children: Map<string | null, LiveRoadmapOption[]>;
  descendantCount: Map<string, number>;
};

export function nodeKey(kind: LiveFocus["kind"], id: string) {
  return `${kind}:${id}`;
}

export function isAgentActive(agent: LiveAgentNode) {
  return (
    agent.state === "working" ||
    agent.state === "waiting" ||
    agent.state === "reporting"
  );
}

/**
 * Formats an ISO timestamp as a short relative age against the snapshot's generatedAt time so
 * cards show freshness deterministically without calling Date.now() during render.
 */
export function formatRelativeAge(
  iso: string | null | undefined,
  referenceIso: string,
): string | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  const ref = Date.parse(referenceIso);
  if (Number.isNaN(at) || Number.isNaN(ref)) return null;
  const seconds = Math.max(0, Math.round((ref - at) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

/**
 * Groups roadmaps into a forest under their parents (orphans/missing parents sit at top level),
 * plus descendant counts and ancestor lookups for multi-tree filtering.
 */
export function buildRoadmapTree(roadmaps: LiveRoadmapOption[]): RoadmapTreeIndex {
  const byId = new Map(roadmaps.map((roadmap) => [roadmap.id, roadmap]));
  const parentOf = new Map<string, string | null>();
  const children = new Map<string | null, LiveRoadmapOption[]>();
  for (const roadmap of roadmaps) {
    const parent =
      roadmap.parentId && byId.has(roadmap.parentId) ? roadmap.parentId : null;
    parentOf.set(roadmap.id, parent);
    const siblings = children.get(parent);
    if (siblings) siblings.push(roadmap);
    else children.set(parent, [roadmap]);
  }
  const descendantCount = new Map<string, number>();
  const countDescendants = (id: string, visiting: Set<string>): number => {
    const cached = descendantCount.get(id);
    if (cached !== undefined) return cached;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    let total = 0;
    for (const child of children.get(id) ?? []) {
      total += 1 + countDescendants(child.id, visiting);
    }
    visiting.delete(id);
    descendantCount.set(id, total);
    return total;
  };
  for (const roadmap of roadmaps) {
    countDescendants(roadmap.id, new Set());
  }
  return { byId, parentOf, children, descendantCount };
}

export function roadmapAncestors(
  id: string,
  parentOf: Map<string, string | null>,
): string[] {
  const result: string[] = [];
  const seen = new Set<string>([id]);
  let current = parentOf.get(id) ?? null;
  while (current && !seen.has(current)) {
    seen.add(current);
    result.push(current);
    current = parentOf.get(current) ?? null;
  }
  return result;
}

export function roadmapDescendants(
  id: string,
  children: Map<string | null, LiveRoadmapOption[]>,
): Set<string> {
  const result = new Set<string>();
  const queue = [id];
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const child of children.get(current) ?? []) {
      if (!result.has(child.id)) {
        result.add(child.id);
        queue.push(child.id);
      }
    }
  }
  return result;
}

/**
 * Toggles a roadmap tree root in the multi-tree selection: selecting a parent subsumes any already
 * selected descendants; selecting a node whose ancestor is already selected does nothing redundant.
 */
export function toggleRoadmapTreeSelection(
  selected: string[],
  targetId: string,
  tree: RoadmapTreeIndex,
): string[] {
  const next = new Set(selected);
  if (next.has(targetId)) {
    next.delete(targetId);
    return [...next];
  }
  // If an ancestor is already selected, clicking a covered child keeps the selection clean.
  const ancestors = roadmapAncestors(targetId, tree.parentOf);
  if (ancestors.some((ancestor) => next.has(ancestor))) {
    return [...next];
  }
  // Selecting a parent subsumes any selected descendants under it.
  const descendants = roadmapDescendants(targetId, tree.children);
  for (const descendant of descendants) {
    next.delete(descendant);
  }
  next.add(targetId);
  return [...next];
}

export function liveConnections(
  snapshot: LiveSnapshot,
  focus: LiveFocus | null,
  pulse: LivePulse | null,
) {
  const nodes = new Set<string>();
  const edges = new Set<string>();
  if (pulse) {
    nodes.add(
      nodeKey(pulse.actorKind === "person" ? "person" : "agent", pulse.actorId),
    );
    if (pulse.targetActorId) {
      nodes.add(
        nodeKey(
          pulse.targetActorId === "local-user" ? "person" : "agent",
          pulse.targetActorId,
        ),
      );
    }
    if (pulse.workId) nodes.add(nodeKey("work", pulse.workId));
    if (pulse.repositoryId) nodes.add(nodeKey("repo", pulse.repositoryId));
    for (const edge of snapshot.workEdges) {
      if (
        nodes.has(nodeKey("work", edge.workId)) &&
        nodes.has(nodeKey("agent", edge.agentId))
      ) {
        edges.add(edge.id);
      }
    }
    for (const edge of snapshot.repoEdges) {
      if (
        nodes.has(nodeKey("repo", edge.repositoryId)) &&
        nodes.has(nodeKey("agent", edge.agentId))
      ) {
        edges.add(edge.id);
      }
    }
    for (const edge of snapshot.commEdges) {
      const from = nodeKey(
        edge.fromKind === "person" ? "person" : "agent",
        edge.fromId,
      );
      const to = nodeKey(
        edge.toKind === "person" ? "person" : "agent",
        edge.toId,
      );
      if (nodes.has(from) && nodes.has(to)) edges.add(edge.id);
    }
  } else if (focus) {
    nodes.add(nodeKey(focus.kind, focus.id));
    for (const edge of snapshot.workEdges) {
      if (
        (focus.kind === "work" && edge.workId === focus.id) ||
        (focus.kind === "agent" && edge.agentId === focus.id)
      ) {
        nodes.add(nodeKey("work", edge.workId));
        nodes.add(nodeKey("agent", edge.agentId));
        edges.add(edge.id);
      }
    }
    for (const edge of snapshot.repoEdges) {
      if (
        (focus.kind === "repo" && edge.repositoryId === focus.id) ||
        (focus.kind === "agent" && edge.agentId === focus.id)
      ) {
        nodes.add(nodeKey("repo", edge.repositoryId));
        nodes.add(nodeKey("agent", edge.agentId));
        edges.add(edge.id);
      }
    }
    for (const edge of snapshot.commEdges) {
      if (edge.fromId === focus.id || edge.toId === focus.id) {
        nodes.add(
          nodeKey(edge.fromKind === "person" ? "person" : "agent", edge.fromId),
        );
        nodes.add(
          nodeKey(edge.toKind === "person" ? "person" : "agent", edge.toId),
        );
        edges.add(edge.id);
      }
    }
  }
  return { nodes, edges, active: Boolean(focus || pulse) };
}

function workMatchesQuery(node: LiveWorkNode, q: string) {
  return (
    node.title.toLocaleLowerCase().includes(q) ||
    (node.roadmapTitle?.toLocaleLowerCase().includes(q) ?? false) ||
    (node.stepTitle?.toLocaleLowerCase().includes(q) ?? false)
  );
}

function agentMatchesQuery(agent: LiveAgentNode, q: string) {
  return (
    agent.label.toLocaleLowerCase().includes(q) ||
    agent.latestNote.toLocaleLowerCase().includes(q) ||
    agent.model.toLocaleLowerCase().includes(q) ||
    agent.roles.some((role) => role.toLocaleLowerCase().includes(q)) ||
    agent.providers.some((provider) => provider.toLocaleLowerCase().includes(q)) ||
    agent.chats.some((chat) => chat.title.toLocaleLowerCase().includes(q))
  );
}

function repoMatchesQuery(repo: LiveRepoNode, q: string) {
  return (
    repo.name.toLocaleLowerCase().includes(q) ||
    repo.branches.some((branch) => branch.toLocaleLowerCase().includes(q))
  );
}

function isRepoContended(repo: LiveRepoNode) {
  return (
    repo.overWipCap ||
    repo.queuedLeases > 0 ||
    repo.dirtySlots > 0 ||
    Boolean(repo.frozenWaveName) ||
    repo.pendingRepinsCount > 0 ||
    repo.maxActiveLeases === 0
  );
}

function isWaitingCommEdge(edge: LiveCommEdge) {
  return (
    edge.status === "pending" &&
    (edge.kind === "ack_request" || edge.kind === "role_offer")
  );
}

function isPersonWaitingCommEdge(edge: LiveCommEdge) {
  return (
    edge.status === "pending" &&
    edge.kind === "ack_request" &&
    edge.toKind === "person"
  );
}

/**
 * Applies client-side visibility and cleanup filters to a LiveSnapshot while preserving graph
 * integrity across work, agents, repositories, edges, swimlanes, and pulses.
 */
export function filterLiveSnapshot(
  snapshot: LiveSnapshot,
  options: LiveFilterOptions,
): { view: LiveSnapshot; hidden: HiddenCounts } {
  let idleOrStaleAgents = 0;
  let revokedAgents = 0;

  // 1. Agent visibility filtering (revoked + active-only vs idle/stale).
  const agents = snapshot.agents.filter((agent) => {
    if (agent.state === "revoked" || !agent.active) {
      if (!options.showRevoked) {
        revokedAgents += 1;
        return false;
      }
      return true;
    }
    if (
      options.agentScope === "active" &&
      !isAgentActive(agent) &&
      options.kpiFilter !== "needsYou" &&
      options.kpiFilter !== "waitingOnAgents"
    ) {
      idleOrStaleAgents += 1;
      return false;
    }
    return true;
  });
  const agentIds = new Set(agents.map((agent) => agent.id));
  const rawAgentIds = new Set(snapshot.agents.map((agent) => agent.id));
  const hiddenAgentIds = new Set(
    [...rawAgentIds].filter((id) => !agentIds.has(id)),
  );

  // 2. Work cleanup: done work and unconnected work.
  const rawWorkEdgesForVisibleAgents = snapshot.workEdges.filter((edge) =>
    agentIds.has(edge.agentId),
  );
  let doneWork = 0;
  let work = snapshot.work.filter((node) => {
    if (
      options.hideDoneWork &&
      node.kind !== "roadmap" &&
      node.status === "done" &&
      !(options.kpiFilter === "needsYou" && node.needsYou)
    ) {
      doneWork += 1;
      return false;
    }
    return true;
  });
  let workIdSet = new Set(work.map((node) => node.id));
  const workEdgesAfterDone = rawWorkEdgesForVisibleAgents.filter((edge) =>
    workIdSet.has(edge.workId),
  );

  let unconnectedWork = 0;
  const allowUnconnectedForKpi = options.kpiFilter === "needsYou";
  if (options.hideUnconnectedWork && !allowUnconnectedForKpi) {
    const connected = new Set<string>(
      workEdgesAfterDone.map((edge) => edge.workId),
    );
    // Keep child items of a connected step that still claim connected agents, and keep parent
    // steps/roadmaps that frame any connected work node.
    for (const node of work) {
      if (
        node.kind === "item" &&
        node.parentId &&
        connected.has(node.parentId) &&
        node.agentCount > 0
      ) {
        connected.add(node.id);
      }
    }
    for (const node of work) {
      if (node.kind === "item" && connected.has(node.id) && node.parentId) {
        connected.add(node.parentId);
      }
    }
    for (const node of work) {
      if (
        node.kind !== "roadmap" &&
        connected.has(node.id) &&
        node.roadmapId &&
        workIdSet.has(node.roadmapId)
      ) {
        connected.add(node.roadmapId);
      }
    }
    work = work.filter((node) => {
      if (connected.has(node.id)) return true;
      unconnectedWork += 1;
      return false;
    });
    workIdSet = new Set(work.map((node) => node.id));
  }

  let workEdges = workEdgesAfterDone.filter((edge) => workIdSet.has(edge.workId));

  // 3. Prune repositories that were only present via agents we hid.
  const rawReposWithAgentEdges = new Set(
    snapshot.repoEdges.map((edge) => edge.repositoryId),
  );
  let repoEdges = snapshot.repoEdges.filter((edge) => agentIds.has(edge.agentId));
  const visibleReposFromAgents = new Set(
    repoEdges.map((edge) => edge.repositoryId),
  );
  let repositories = snapshot.repositories.filter(
    (repo) =>
      visibleReposFromAgents.has(repo.id) ||
      (!rawReposWithAgentEdges.has(repo.id) && work.length > 0),
  );
  let repoIdSet = new Set(repositories.map((repo) => repo.id));
  repoEdges = repoEdges.filter((edge) => repoIdSet.has(edge.repositoryId));

  let commEdges = snapshot.commEdges.filter(
    (edge) =>
      (edge.fromKind === "person" || agentIds.has(edge.fromId)) &&
      (edge.toKind === "person" || agentIds.has(edge.toId)) &&
      (agentIds.has(edge.fromId) || agentIds.has(edge.toId)),
  );

  // 4. Interactive KPI filter (narrows to attention/contention/active subgraph).
  let filteredAgents = agents;
  if (options.kpiFilter) {
    const keepWork = new Set<string>();
    const keepAgents = new Set<string>();
    const keepRepos = new Set<string>();
    if (options.kpiFilter === "needsYou") {
      for (const node of work) {
        if (node.needsYou) keepWork.add(node.id);
      }
      for (const edge of commEdges) {
        if (isPersonWaitingCommEdge(edge)) {
          if (edge.fromKind === "agent") keepAgents.add(edge.fromId);
          if (edge.toKind === "agent") keepAgents.add(edge.toId);
        }
      }
      for (const edge of workEdges) {
        if (keepWork.has(edge.workId)) keepAgents.add(edge.agentId);
        if (keepAgents.has(edge.agentId)) keepWork.add(edge.workId);
      }
      for (const edge of repoEdges) {
        if (keepAgents.has(edge.agentId)) keepRepos.add(edge.repositoryId);
      }
    } else if (options.kpiFilter === "activeAgents") {
      for (const agent of filteredAgents) {
        if (isAgentActive(agent)) keepAgents.add(agent.id);
      }
      for (const edge of workEdges) {
        if (keepAgents.has(edge.agentId)) keepWork.add(edge.workId);
      }
      for (const edge of repoEdges) {
        if (keepAgents.has(edge.agentId)) keepRepos.add(edge.repositoryId);
      }
    } else if (options.kpiFilter === "overlaps") {
      for (const repo of repositories) {
        if (repo.overlapCount > 1) keepRepos.add(repo.id);
      }
      for (const edge of repoEdges) {
        if (keepRepos.has(edge.repositoryId)) keepAgents.add(edge.agentId);
      }
      for (const edge of workEdges) {
        if (keepAgents.has(edge.agentId)) keepWork.add(edge.workId);
      }
    } else if (options.kpiFilter === "overCap") {
      for (const repo of repositories) {
        if (isRepoContended(repo)) keepRepos.add(repo.id);
      }
      for (const edge of repoEdges) {
        if (keepRepos.has(edge.repositoryId)) keepAgents.add(edge.agentId);
      }
      for (const edge of workEdges) {
        if (keepAgents.has(edge.agentId)) keepWork.add(edge.workId);
      }
    } else if (options.kpiFilter === "waitingOnAgents") {
      for (const edge of commEdges) {
        if (isWaitingCommEdge(edge)) {
          if (edge.fromKind === "agent") keepAgents.add(edge.fromId);
          if (edge.toKind === "agent") keepAgents.add(edge.toId);
        }
      }
      for (const edge of workEdges) {
        if (keepAgents.has(edge.agentId)) keepWork.add(edge.workId);
      }
      for (const edge of repoEdges) {
        if (keepAgents.has(edge.agentId)) keepRepos.add(edge.repositoryId);
      }
    }
    // Keep framing parent steps/roadmaps for kept work.
    for (const node of work) {
      if (node.kind === "item" && keepWork.has(node.id) && node.parentId) {
        keepWork.add(node.parentId);
      }
    }
    for (const node of work) {
      if (
        node.kind !== "roadmap" &&
        keepWork.has(node.id) &&
        node.roadmapId &&
        workIdSet.has(node.roadmapId)
      ) {
        keepWork.add(node.roadmapId);
      }
    }
    work = work.filter((node) => keepWork.has(node.id));
    filteredAgents = filteredAgents.filter((agent) => keepAgents.has(agent.id));
    repositories = repositories.filter((repo) => keepRepos.has(repo.id));
    workIdSet = new Set(work.map((node) => node.id));
    const keptAgentSet = new Set(filteredAgents.map((agent) => agent.id));
    repoIdSet = new Set(repositories.map((repo) => repo.id));
    workEdges = workEdges.filter(
      (edge) => keptAgentSet.has(edge.agentId) && workIdSet.has(edge.workId),
    );
    repoEdges = repoEdges.filter(
      (edge) => keptAgentSet.has(edge.agentId) && repoIdSet.has(edge.repositoryId),
    );
    commEdges = commEdges.filter(
      (edge) =>
        (edge.fromKind === "person" || keptAgentSet.has(edge.fromId)) &&
        (edge.toKind === "person" || keptAgentSet.has(edge.toId)) &&
        (keptAgentSet.has(edge.fromId) || keptAgentSet.has(edge.toId)),
    );
  }

  // 5. Quick text search (keeps matching nodes plus 1-hop connected neighbors across tiers).
  const query = options.search.trim().toLocaleLowerCase();
  if (query) {
    const matchedWork = new Set(
      work.filter((node) => workMatchesQuery(node, query)).map((node) => node.id),
    );
    const matchedAgents = new Set(
      filteredAgents
        .filter((agent) => agentMatchesQuery(agent, query))
        .map((agent) => agent.id),
    );
    const matchedRepos = new Set(
      repositories
        .filter((repo) => repoMatchesQuery(repo, query))
        .map((repo) => repo.id),
    );
    const keepWork = new Set(matchedWork);
    const keepAgents = new Set(matchedAgents);
    const keepRepos = new Set(matchedRepos);
    for (const edge of workEdges) {
      if (matchedWork.has(edge.workId)) keepAgents.add(edge.agentId);
      if (matchedAgents.has(edge.agentId)) keepWork.add(edge.workId);
    }
    for (const edge of repoEdges) {
      if (matchedRepos.has(edge.repositoryId)) keepAgents.add(edge.agentId);
      if (matchedAgents.has(edge.agentId)) keepRepos.add(edge.repositoryId);
    }
    // Pull connected work for agents brought in by a repo match, and repos for work matches.
    for (const edge of workEdges) {
      if (keepAgents.has(edge.agentId) && matchedRepos.size > 0) {
        keepWork.add(edge.workId);
      }
    }
    for (const edge of repoEdges) {
      if (keepAgents.has(edge.agentId) && matchedWork.size > 0) {
        keepRepos.add(edge.repositoryId);
      }
    }
    for (const node of work) {
      if (node.kind === "item" && keepWork.has(node.id) && node.parentId) {
        keepWork.add(node.parentId);
      }
    }
    for (const node of work) {
      if (
        node.kind !== "roadmap" &&
        keepWork.has(node.id) &&
        node.roadmapId &&
        workIdSet.has(node.roadmapId)
      ) {
        keepWork.add(node.roadmapId);
      }
    }
    work = work.filter((node) => keepWork.has(node.id));
    filteredAgents = filteredAgents.filter((agent) => keepAgents.has(agent.id));
    repositories = repositories.filter((repo) => keepRepos.has(repo.id));
    workIdSet = new Set(work.map((node) => node.id));
    const keptAgentSet = new Set(filteredAgents.map((agent) => agent.id));
    repoIdSet = new Set(repositories.map((repo) => repo.id));
    workEdges = workEdges.filter(
      (edge) => keptAgentSet.has(edge.agentId) && workIdSet.has(edge.workId),
    );
    repoEdges = repoEdges.filter(
      (edge) => keptAgentSet.has(edge.agentId) && repoIdSet.has(edge.repositoryId),
    );
    commEdges = commEdges.filter(
      (edge) =>
        (edge.fromKind === "person" || keptAgentSet.has(edge.fromId)) &&
        (edge.toKind === "person" || keptAgentSet.has(edge.toId)) &&
        (keptAgentSet.has(edge.fromId) || keptAgentSet.has(edge.toId)),
    );
  }

  let finalAgentSet = new Set(filteredAgents.map((agent) => agent.id));
  let lanes = snapshot.lanes.filter((lane) => finalAgentSet.has(lane.agentId));
  const pulses = snapshot.pulses.filter((pulse) => {
    if (pulse.actorKind === "agent" && hiddenAgentIds.has(pulse.actorId)) {
      return false;
    }
    if (query) {
      const hay = [
        pulse.actorLabel,
        pulse.targetActorLabel ?? "",
        pulse.workTitle ?? "",
        pulse.repositoryName ?? "",
        pulse.summary,
      ]
        .join(" ")
        .toLocaleLowerCase();
      return hay.includes(query);
    }
    return true;
  });

  let view: LiveSnapshot = {
    ...snapshot,
    work,
    agents: filteredAgents,
    repositories,
    workEdges,
    repoEdges,
    commEdges,
    lanes,
    pulses,
  };

  // 6. Optional Isolate selection: hide unrelated nodes instead of dimming them.
  if (options.isolateFocus && (options.focus || options.pulse)) {
    const related = liveConnections(view, options.focus, options.pulse);
    if (related.active) {
      work = view.work.filter((node) => related.nodes.has(nodeKey("work", node.id)));
      filteredAgents = view.agents.filter((agent) =>
        related.nodes.has(nodeKey("agent", agent.id)),
      );
      repositories = view.repositories.filter((repo) =>
        related.nodes.has(nodeKey("repo", repo.id)),
      );
      workIdSet = new Set(work.map((node) => node.id));
      finalAgentSet = new Set(filteredAgents.map((agent) => agent.id));
      repoIdSet = new Set(repositories.map((repo) => repo.id));
      workEdges = view.workEdges.filter(
        (edge) => finalAgentSet.has(edge.agentId) && workIdSet.has(edge.workId),
      );
      repoEdges = view.repoEdges.filter(
        (edge) => finalAgentSet.has(edge.agentId) && repoIdSet.has(edge.repositoryId),
      );
      commEdges = view.commEdges.filter(
        (edge) =>
          related.edges.has(edge.id) &&
          (edge.fromKind === "person" || finalAgentSet.has(edge.fromId)) &&
          (edge.toKind === "person" || finalAgentSet.has(edge.toId)),
      );
      lanes = view.lanes.filter((lane) => finalAgentSet.has(lane.agentId));
      view = {
        ...view,
        work,
        agents: filteredAgents,
        repositories,
        workEdges,
        repoEdges,
        commEdges,
        lanes,
      };
    }
  }

  return {
    view,
    hidden: {
      idleOrStaleAgents,
      revokedAgents,
      unconnectedWork,
      doneWork,
    },
  };
}
