import { describe, expect, it } from "vitest";
import type { LiveRoadmapOption, LiveSnapshot } from "./apiTypes";
import {
  buildRoadmapTree,
  filterLiveSnapshot,
  formatRelativeAge,
  toggleRoadmapTreeSelection,
} from "./liveFilter";

const roadmaps: LiveRoadmapOption[] = [
  { id: "root-a", title: "Alpha", parentId: null, openQuestions: 1 },
  { id: "sub-a1", title: "Alpha Child", parentId: "root-a", openQuestions: 0 },
  { id: "sub-a2", title: "Alpha Grandchild", parentId: "sub-a1", openQuestions: 2 },
  { id: "root-b", title: "Beta", parentId: null, openQuestions: 0 },
];

const baseSnapshot: LiveSnapshot = {
  generatedAt: "2026-11-28T12:00:00Z",
  windowMinutes: 60,
  roadmapId: null,
  roadmapTitle: null,
  summary: {
    activeAgents: 1,
    workingChats: 1,
    activeWorkItems: 1,
    activeSteps: 1,
    activeRepos: 2,
    repoOverlaps: 1,
    overCapRepos: 1,
    pendingRequests: 1,
    waitingOnAgents: 1,
    waitingProposals: 0,
    openQuestions: 1,
    needsYou: 2,
  },
  roadmaps,
  work: [
    {
      id: "root-a",
      kind: "roadmap",
      title: "Alpha",
      status: "open",
      roadmapId: "root-a",
      roadmapTitle: "Alpha",
      parentId: null,
      stepTitle: null,
      rank: null,
      openQuestions: 0,
      waitingProposals: 0,
      waitingProposalIds: [],
      agentCount: 1,
      needsYou: false,
      href: "#roadmaps/root-a",
    },
    {
      id: "step-active",
      kind: "step",
      title: "Build pipeline",
      status: "doing",
      roadmapId: "root-a",
      roadmapTitle: "Alpha",
      parentId: null,
      stepTitle: "Build pipeline",
      rank: 1,
      openQuestions: 0,
      waitingProposals: 0,
      waitingProposalIds: [],
      agentCount: 1,
      needsYou: false,
      href: "#roadmaps/root-a/step-active",
    },
    {
      id: "step-done",
      kind: "step",
      title: "Finished setup",
      status: "done",
      roadmapId: "root-a",
      roadmapTitle: "Alpha",
      parentId: null,
      stepTitle: "Finished setup",
      rank: 2,
      openQuestions: 0,
      waitingProposals: 0,
      waitingProposalIds: [],
      agentCount: 1,
      needsYou: false,
      href: "#roadmaps/root-a/step-done",
    },
    {
      id: "root-b",
      kind: "roadmap",
      title: "Beta quiet",
      status: "open",
      roadmapId: "root-b",
      roadmapTitle: "Beta quiet",
      parentId: null,
      stepTitle: null,
      rank: null,
      openQuestions: 1,
      waitingProposals: 0,
      waitingProposalIds: [],
      agentCount: 0,
      needsYou: true,
      href: "#roadmaps/root-b",
    },
  ],
  agents: [
    {
      id: "agent-active",
      label: "Active builder",
      avatar: null,
      active: true,
      state: "working",
      declaredState: "reporting",
      sessionState: "working",
      providers: ["claude"],
      model: "opus",
      registeredById: null,
      registeredByLabel: null,
      openRuns: 1,
      openParticipations: 1,
      roles: ["Builder"],
      chats: [],
      lastAt: "2026-11-28T11:55:00Z",
      latestNote: "Shipping feature",
      href: "#agents/agent-active",
    },
    {
      id: "agent-idle",
      label: "Old idle",
      avatar: null,
      active: true,
      state: "idle",
      declaredState: "none",
      sessionState: null,
      providers: ["codex"],
      model: "gpt",
      registeredById: null,
      registeredByLabel: null,
      openRuns: 0,
      openParticipations: 0,
      roles: ["Observer"],
      chats: [],
      lastAt: "2026-11-24T12:00:00Z",
      latestNote: "Done four days ago",
      href: "#agents/agent-idle",
    },
    {
      id: "agent-revoked",
      label: "Retired bot",
      avatar: null,
      active: false,
      state: "revoked",
      declaredState: "revoked",
      sessionState: null,
      providers: ["codex"],
      model: "gpt",
      registeredById: null,
      registeredByLabel: null,
      openRuns: 0,
      openParticipations: 0,
      roles: [],
      chats: [],
      lastAt: "2026-11-28T11:50:00Z",
      latestNote: "Revoked",
      href: "#agents/agent-revoked",
    },
  ],
  untrackedSessions: [],
  repositories: [
    {
      id: "repo-active",
      name: "Worklog",
      url: null,
      unreviewed: false,
      discussionId: null,
      branches: ["feat/live", "main"],
      agentCount: 1,
      overlapCount: 2,
      wipCap: 1,
      effectiveWipCap: 1,
      tier: 1,
      doingStepCount: 1,
      overWipCap: true,
      frozenWaveName: null,
      pendingRepinsCount: 0,
      maxActiveLeases: 2,
      activeLeases: 1,
      queuedLeases: 0,
      dirtySlots: 0,
      rescuedLeases: 0,
      baseReadonly: false,
      lastAt: "2026-11-28T11:55:00Z",
      href: "#repositories/repo-active",
    },
    {
      id: "repo-old",
      name: "OldRepo",
      url: null,
      unreviewed: false,
      discussionId: null,
      branches: ["old-branch"],
      agentCount: 1,
      overlapCount: 0,
      wipCap: null,
      effectiveWipCap: null,
      tier: null,
      doingStepCount: 0,
      overWipCap: false,
      frozenWaveName: null,
      pendingRepinsCount: 0,
      maxActiveLeases: 2,
      activeLeases: 0,
      queuedLeases: 0,
      dirtySlots: 0,
      rescuedLeases: 0,
      baseReadonly: false,
      lastAt: "2026-11-24T12:00:00Z",
      href: "#repositories/repo-old",
    },
  ],
  workEdges: [
    {
      id: "we-1",
      agentId: "agent-active",
      workId: "step-active",
      workKind: "step",
      via: ["participation"],
      active: true,
      stale: false,
      lastAt: "2026-11-28T11:55:00Z",
    },
    {
      id: "we-2",
      agentId: "agent-active",
      workId: "step-done",
      workKind: "step",
      via: ["participation"],
      active: false,
      stale: false,
      lastAt: "2026-11-28T11:40:00Z",
    },
  ],
  repoEdges: [
    {
      id: "re-1",
      agentId: "agent-active",
      repositoryId: "repo-active",
      via: ["participation"],
      branch: "feat/live",
      worktreePath: "C:/w",
      active: true,
      stale: false,
      lastAt: "2026-11-28T11:55:00Z",
    },
    {
      id: "re-2",
      agentId: "agent-idle",
      repositoryId: "repo-old",
      via: ["participation"],
      branch: "old-branch",
      worktreePath: "C:/old",
      active: false,
      stale: true,
      lastAt: "2026-11-24T12:00:00Z",
    },
  ],
  commEdges: [
    {
      id: "ce-1",
      fromKind: "agent",
      fromId: "agent-idle",
      fromLabel: "Old idle",
      toKind: "person",
      toId: "local-user",
      toLabel: "You",
      kind: "ack_request",
      status: "pending",
      label: "Acknowledgement",
      discussionId: "d-1",
      messageId: "m-1",
      lastAt: "2026-11-28T11:00:00Z",
      href: "#discussions/d-1",
    },
  ],
  lanes: [
    {
      id: "lane-active",
      agentId: "agent-active",
      label: "Build pipeline",
      workId: "step-active",
      workKind: "step",
      repositoryId: "repo-active",
      repositoryName: "Worklog",
      branch: "feat/live",
      startedAt: "2026-11-28T11:10:00Z",
      endedAt: null,
      lastSeenAt: "2026-11-28T11:55:00Z",
      stale: false,
      href: "#roadmaps/root-a/step-active",
    },
    {
      id: "lane-idle",
      agentId: "agent-idle",
      label: "Old work",
      workId: null,
      workKind: null,
      repositoryId: "repo-old",
      repositoryName: "OldRepo",
      branch: "old-branch",
      startedAt: "2026-11-28T11:00:00Z",
      endedAt: "2026-11-28T11:05:00Z",
      lastSeenAt: "2026-11-28T11:05:00Z",
      stale: true,
      href: "#agents/agent-idle",
    },
  ],
  pulses: [
    {
      id: "p-active",
      kind: "update",
      at: "2026-11-28T11:55:00Z",
      actorKind: "agent",
      actorId: "agent-active",
      actorLabel: "Active builder",
      targetActorId: null,
      targetActorLabel: null,
      workId: "step-active",
      workTitle: "Build pipeline",
      repositoryId: "repo-active",
      repositoryName: "Worklog",
      discussionId: null,
      proposalId: null,
      summary: "Updated pipeline",
      href: "#roadmaps/root-a/step-active",
    },
    {
      id: "p-revoked",
      kind: "message",
      at: "2026-11-28T11:50:00Z",
      actorKind: "agent",
      actorId: "agent-revoked",
      actorLabel: "Retired bot",
      targetActorId: null,
      targetActorLabel: null,
      workId: null,
      workTitle: null,
      repositoryId: null,
      repositoryName: null,
      discussionId: "d-2",
      proposalId: null,
      summary: "Closing out",
      href: "#discussions/d-2",
    },
  ],
};

describe("liveFilter", () => {
  it("builds a roadmap forest and subsumes selected descendants when selecting a parent", () => {
    const tree = buildRoadmapTree(roadmaps);
    expect(tree.children.get(null)?.map((r) => r.id)).toEqual(["root-a", "root-b"]);
    expect(tree.descendantCount.get("root-a")).toBe(2);
    expect(tree.descendantCount.get("sub-a1")).toBe(1);
    expect(tree.descendantCount.get("root-b")).toBe(0);

    let selected = toggleRoadmapTreeSelection([], "sub-a2", tree);
    expect(selected).toEqual(["sub-a2"]);
    selected = toggleRoadmapTreeSelection(selected, "root-b", tree);
    expect(selected).toEqual(["sub-a2", "root-b"]);
    // Selecting root-a subsumes sub-a2 while preserving root-b.
    selected = toggleRoadmapTreeSelection(selected, "root-a", tree);
    expect(selected).toEqual(["root-b", "root-a"]);
    // Clicking a descendant already covered by root-a does not add a duplicate.
    expect(toggleRoadmapTreeSelection(selected, "sub-a1", tree)).toEqual(["root-b", "root-a"]);
    // Deselecting root-a leaves root-b.
    expect(toggleRoadmapTreeSelection(selected, "root-a", tree)).toEqual(["root-b"]);
  });

  it("formats deterministic relative ages against snapshot.generatedAt", () => {
    const ref = "2026-11-28T12:00:00Z";
    expect(formatRelativeAge("2026-11-28T11:59:40Z", ref)).toBe("just now");
    expect(formatRelativeAge("2026-11-28T11:55:00Z", ref)).toBe("5m ago");
    expect(formatRelativeAge("2026-11-28T09:00:00Z", ref)).toBe("3h ago");
    expect(formatRelativeAge("2026-11-24T12:00:00Z", ref)).toBe("4d ago");
    expect(formatRelativeAge(null, ref)).toBeNull();
  });

  it("filters out idle, revoked, done, and unconnected noise while pruning orphan repos and pulses", () => {
    const { view, hidden } = filterLiveSnapshot(baseSnapshot, {
      agentScope: "active",
      showRevoked: false,
      hideUnconnectedWork: true,
      hideDoneWork: true,
      search: "",
      kpiFilter: null,
      isolateFocus: false,
      focus: null,
      pulse: null,
    });
    expect(hidden).toEqual({
      idleOrStaleAgents: 1,
      revokedAgents: 1,
      unconnectedWork: 1,
      doneWork: 1,
    });
    expect(view.agents.map((a) => a.id)).toEqual(["agent-active"]);
    expect(view.work.map((w) => w.id)).toEqual(["root-a", "step-active"]);
    expect(view.repositories.map((r) => r.id)).toEqual(["repo-active"]);
    expect(view.lanes.map((l) => l.id)).toEqual(["lane-active"]);
    expect(view.pulses.map((p) => p.id)).toEqual(["p-active"]);
  });

  it("surfaces quiet needs-you work and pending human requests when filtering by Needs you", () => {
    const { view } = filterLiveSnapshot(baseSnapshot, {
      agentScope: "active",
      showRevoked: false,
      hideUnconnectedWork: true,
      hideDoneWork: true,
      search: "",
      kpiFilter: "needsYou",
      isolateFocus: false,
      focus: null,
      pulse: null,
    });
    expect(view.work.map((w) => w.id)).toEqual(["root-b"]);
    expect(view.agents.map((a) => a.id)).toEqual(["agent-idle"]);
    expect(view.commEdges.map((e) => e.id)).toEqual(["ce-1"]);
  });

  it("filters by text search across branches while keeping connected neighbors", () => {
    const { view } = filterLiveSnapshot(baseSnapshot, {
      agentScope: "recent",
      showRevoked: false,
      hideUnconnectedWork: false,
      hideDoneWork: false,
      search: "feat/live",
      kpiFilter: null,
      isolateFocus: false,
      focus: null,
      pulse: null,
    });
    expect(view.repositories.map((r) => r.id)).toEqual(["repo-active"]);
    expect(view.agents.map((a) => a.id)).toEqual(["agent-active"]);
    expect(view.work.map((w) => w.id)).toEqual(["root-a", "step-active", "step-done"]);
  });

  it("isolates only the focused node and its connected subgraph when isolateFocus is enabled", () => {
    const { view } = filterLiveSnapshot(baseSnapshot, {
      agentScope: "recent",
      showRevoked: false,
      hideUnconnectedWork: false,
      hideDoneWork: false,
      search: "",
      kpiFilter: null,
      isolateFocus: true,
      focus: { kind: "repo", id: "repo-old" },
      pulse: null,
    });
    expect(view.repositories.map((r) => r.id)).toEqual(["repo-old"]);
    expect(view.agents.map((a) => a.id)).toEqual(["agent-idle"]);
    expect(view.work).toEqual([]);
  });
});
