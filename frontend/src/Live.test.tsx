import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { LiveSnapshot } from "./apiTypes";

const mocks = vi.hoisted(() => ({ useData: vi.fn() }));
vi.mock("./hooks", () => ({ useData: mocks.useData }));
import { Live } from "./Live";

const lastRead = (prefix: string) =>
  mocks.useData.mock.calls.filter(([path]) => String(path).startsWith(prefix)).at(-1);

const snapshot: LiveSnapshot = {
  generatedAt: "2026-11-14T12:00:00Z",
  windowMinutes: 60,
  roadmapId: null,
  roadmapTitle: null,
  summary: {
    activeAgents: 2,
    workingChats: 1,
    activeWorkItems: 1,
    activeSteps: 1,
    activeRepos: 1,
    repoOverlaps: 1,
    overCapRepos: 0,
    pendingRequests: 1,
    waitingOnAgents: 3,
    waitingProposals: 1,
    openQuestions: 2,
    needsYou: 4,
  },
  roadmaps: [{ id: "road-1", title: "Ship Worklog", parentId: null, openQuestions: 2 }],
  work: [
    {
      id: "step-1",
      kind: "step",
      title: "Visualize agent work",
      status: "doing",
      roadmapId: "road-1",
      roadmapTitle: "Ship Worklog",
      parentId: null,
      stepTitle: null,
      rank: 1,
      openQuestions: 2,
      waitingProposals: 1,
      waitingProposalIds: ["proposal-1"],
      agentCount: 1,
      needsYou: true,
      href: "#roadmaps/road-1/steps/step-1",
    },
    {
      id: "item-1",
      kind: "item",
      title: "Build the live canvas",
      status: "waiting",
      roadmapId: "road-1",
      roadmapTitle: "Ship Worklog",
      parentId: "step-1",
      stepTitle: "Visualize agent work",
      rank: 2,
      openQuestions: 0,
      waitingProposals: 0,
      waitingProposalIds: [],
      agentCount: 1,
      needsYou: false,
      href: "#work/item-1",
    },
  ],
  agents: [
    {
      id: "agent-1",
      label: "Canvas builder",
      avatar: "teal-visor-antenna",
      active: true,
      state: "working",
      declaredState: "reporting",
      sessionState: "working",
      providers: ["codex"],
      model: "gpt",
      registeredById: null,
      registeredByLabel: null,
      openRuns: 1,
      openParticipations: 1,
      roles: ["Frontend owner"],
      chats: [
        {
          id: "chat-1",
          chatId: "session-1",
          title: "Live implementation",
          provider: "codex",
          model: "gpt",
          working: true,
          lastAt: "2026-11-14T11:59:00Z",
          href: "#chats/chat-1",
        },
      ],
      lastAt: "2026-11-14T11:59:00Z",
      latestNote: "Wiring the flow map",
      href: "#agents/agent-1",
    },
    {
      id: "agent-2",
      label: "Reviewer",
      avatar: "amber-dots-bare",
      active: true,
      state: "waiting",
      declaredState: "none",
      sessionState: "waiting",
      providers: ["claude"],
      model: "opus",
      registeredById: "agent-1",
      registeredByLabel: "Canvas builder",
      openRuns: 1,
      openParticipations: 0,
      roles: [],
      chats: [],
      lastAt: "2026-11-14T11:55:00Z",
      latestNote: "Waiting for review",
      href: "#agents/agent-2",
    },
  ],
  untrackedSessions: [
    {
      sessionId: "untracked-1",
      hostSessionId: null,
      name: "Local Claude session",
      state: "working",
      entrypoint: "claude",
      cwd: "C:/Worklog",
      model: "opus",
      startedAt: "2026-11-14T11:30:00Z",
    },
  ],
  repositories: [
    {
      id: "repo-1",
      name: "Worklog",
      url: "https://example.test/worklog",
      unreviewed: true,
      discussionId: "discussion-1",
      branches: ["visualize-live", "review-live"],
      agentCount: 2,
      overlapCount: 2,
      wipCap: null,
      effectiveWipCap: null,
      tier: null,
      doingStepCount: 0,
      overWipCap: false,
      frozenWaveName: null,
      pendingRepinsCount: 0,
      maxActiveLeases: 2,
      activeLeases: 2,
      queuedLeases: 1,
      dirtySlots: 0,
      rescuedLeases: 0,
      baseReadonly: true,
      lastAt: "2026-11-14T11:59:00Z",
      href: "#repositories/repo-1",
    },
  ],
  workEdges: [
    {
      id: "work-edge-1",
      agentId: "agent-1",
      workId: "step-1",
      workKind: "step",
      via: ["participation"],
      active: true,
      stale: false,
      lastAt: "2026-11-14T11:59:00Z",
    },
  ],
  repoEdges: [
    {
      id: "repo-edge-1",
      agentId: "agent-1",
      repositoryId: "repo-1",
      via: ["participation"],
      branch: "visualize-live",
      worktreePath: "C:/Worklog",
      active: true,
      stale: false,
      lastAt: "2026-11-14T11:59:00Z",
    },
  ],
  commEdges: [
    {
      id: "comm-1",
      fromKind: "person",
      fromId: "local-user",
      fromLabel: "You",
      toKind: "agent",
      toId: "agent-1",
      toLabel: "Canvas builder",
      kind: "ack_request",
      status: "pending",
      label: "Review requested",
      discussionId: "discussion-1",
      messageId: "message-1",
      lastAt: "2026-11-14T11:58:00Z",
      href: "#discussions/discussion-1",
    },
    {
      id: "comm-2",
      fromKind: "agent",
      fromId: "agent-1",
      fromLabel: "Canvas builder",
      toKind: "agent",
      toId: "agent-2",
      toLabel: "Reviewer",
      kind: "role_offer",
      status: "active",
      label: "Review handoff",
      discussionId: "discussion-1",
      messageId: "message-2",
      lastAt: "2026-11-14T11:57:00Z",
      href: "#discussions/discussion-1",
    },
  ],
  lanes: [
    {
      id: "lane-1",
      agentId: "agent-1",
      label: "Visualize agent work",
      workId: "step-1",
      workKind: "step",
      repositoryId: "repo-1",
      repositoryName: "Worklog",
      branch: "visualize-live",
      startedAt: "2026-11-14T11:20:00Z",
      endedAt: null,
      lastSeenAt: "2026-11-14T11:59:00Z",
      stale: false,
      href: "#roadmaps/road-1/steps/step-1",
    },
  ],
  pulses: [
    {
      id: "pulse-2",
      kind: "role_handoff",
      at: "2026-11-14T11:55:00Z",
      actorKind: "agent",
      actorId: "agent-2",
      actorLabel: "Reviewer",
      targetActorId: "agent-1",
      targetActorLabel: "Canvas builder",
      workId: "item-1",
      workTitle: "Build the live canvas",
      repositoryId: null,
      repositoryName: null,
      discussionId: "discussion-1",
      proposalId: null,
      summary: "Accepted the review handoff",
      href: "#discussions/discussion-1",
    },
    {
      id: "pulse-1",
      kind: "message",
      at: "2026-11-14T11:50:00Z",
      actorKind: "agent",
      actorId: "agent-1",
      actorLabel: "Canvas builder",
      targetActorId: "local-user",
      targetActorLabel: "You",
      workId: "step-1",
      workTitle: "Visualize agent work",
      repositoryId: "repo-1",
      repositoryName: "Worklog",
      discussionId: "discussion-1",
      proposalId: null,
      summary: "Asked for a review",
      href: "#discussions/discussion-1",
    },
  ],
};

beforeEach(() => {
  mocks.useData.mockReset();
  mocks.useData.mockReturnValue({ data: snapshot, error: null, isPending: false });
});

describe("Live", () => {
  it("renders the flow top down with provenance, overlap, connections and pulses", () => {
    const { container } = render(<Live />);
    expect(screen.getByRole("button", { name: "Flow map" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Plans & work")).toBeInTheDocument();
    expect(screen.getByText("Visualize agent work", { selector: ".live-card h3" })).toBeInTheDocument();
    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.getAllByText(/Declared:/, { selector: ".live-provenance" })[0]).toHaveTextContent("reporting");
    expect(screen.getByText("Worklog", { selector: ".live-repo-tier h3" })).toBeInTheDocument();
    expect(screen.getByText("Slots 2/2")).toBeInTheDocument();
    expect(screen.getByText("+1 queued")).toBeInTheDocument();
    expect(screen.getByText("Base locked")).toBeInTheDocument();
    expect(screen.getByText("Overlap: 2 agents")).toBeInTheDocument();
    expect(screen.getByText("visualize-live")).toBeInTheDocument();
    expect(screen.getByText("Recent pulses", { selector: ".live-pulses h2" })).toBeInTheDocument();
    const paths = [...container.querySelectorAll("path[data-edge-id]")];
    expect(paths).toHaveLength(4);
    for (const path of paths) expect(path.getAttribute("d")).toMatch(/^M .+ [CQ] /);
    expect(container.querySelector('[data-edge-id="work-edge-1"]')).toHaveAttribute(
      "d",
      "M 150.0 168.0 C 150.0 218.0, 410.0 218.0, 410.0 268.0",
    );
  });

  it("isolates connected nodes, opens deep links, and clears the selection", async () => {
    const user = userEvent.setup();
    const { container } = render(<Live />);
    const chatLink = screen.getByRole("link", { name: "Live implementation" });
    fireEvent.keyDown(chatLink, { key: "Enter" });
    expect(container.querySelector('[data-node-key="agent:agent-1"]')).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("complementary", { name: "Selection inspector" })).not.toBeInTheDocument();
    await user.click(container.querySelector('[data-node-key="work:step-1"]')!);
    expect(screen.getByRole("complementary", { name: "Selection inspector" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open step" })).toHaveAttribute("href", "#roadmaps/road-1/steps/step-1");
    expect(screen.getByRole("link", { name: "Review proposal" })).toHaveAttribute("href", "#review/proposal-1");
    expect(container.querySelector('[data-node-key="agent:agent-2"]')).toHaveClass("is-dimmed");
    expect(container.querySelector('[data-node-key="agent:agent-1"]')).toHaveClass("is-highlighted");
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByRole("complementary", { name: "Selection inspector" })).not.toBeInTheDocument();
    expect(container.querySelector(".live-card.is-dimmed")).toBeNull();
  });

  it("steps through recent pulses and returns to live", async () => {
    const user = userEvent.setup();
    const { container, rerender } = render(<Live />);
    await user.click(screen.getByRole("button", { name: "Previous pulse" }));
    expect(screen.getByText("Accepted the review handoff", { selector: ".live-inspector strong" })).toBeInTheDocument();
    expect(container.querySelector('[data-node-key="agent:agent-2"]')).toHaveClass("is-highlighted");
    await user.click(screen.getByRole("button", { name: "Previous pulse" }));
    expect(screen.getByText("Asked for a review", { selector: ".live-inspector strong" })).toBeInTheDocument();
    expect(container.querySelector('[data-node-key="agent:agent-1"]')).toHaveClass("is-highlighted");
    expect(container.querySelector('[data-edge-id="work-edge-1"]')).toHaveClass("is-highlighted");
    expect(container.querySelector('[data-edge-id="comm-1"]')?.parentElement).toHaveClass("is-highlighted");
    mocks.useData.mockReturnValue({
      data: {
        ...snapshot,
        pulses: [
          { ...snapshot.pulses[0], id: "pulse-3", at: "2026-11-14T11:59:00Z", summary: "A newer pulse arrived" },
          ...snapshot.pulses,
        ],
      },
      error: null,
      isPending: false,
    });
    rerender(<Live />);
    expect(screen.getByText("Asked for a review", { selector: ".live-inspector strong" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next pulse" }));
    expect(screen.getByText("Accepted the review handoff", { selector: ".live-inspector strong" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Live now" }));
    expect(screen.queryByRole("complementary", { name: "Selection inspector" })).not.toBeInTheDocument();
  });

  it("switches to time-scaled agent swimlanes", async () => {
    const user = userEvent.setup();
    render(<Live />);
    await user.click(screen.getByRole("button", { name: "Previous pulse" }));
    await user.click(screen.getByRole("button", { name: "Previous pulse" }));
    await user.click(screen.getByRole("button", { name: "Swimlanes" }));
    const lanes = screen.getByRole("region", { name: "Agent activity swimlanes" });
    expect(within(lanes).getByText("Canvas builder")).toBeInTheDocument();
    expect(within(lanes).getByTitle("Visualize agent work / Worklog / visualize-live")).toHaveAttribute("href", "#roadmaps/road-1/steps/step-1");
    expect(within(lanes).getByTitle("Visualize agent work / Worklog / visualize-live")).toHaveClass("is-highlighted");
    expect(within(lanes).getAllByTitle("Review requested").length).toBeGreaterThan(0);
  });

  it("changes activity filters and polling state", async () => {
    // Every 15 seconds, not 5 (audit 3.7, P7): each poll reads the whole live picture.
    const user = userEvent.setup();
    render(<Live />);
    expect(lastRead("/live?")).toEqual([
      "/live?windowMinutes=60&includeIdle=false",
      15_000,
      { keepPrevious: true },
    ]);
    expect(lastRead("/dependencies/waits")?.[1]).toBe(15_000);
    await user.click(screen.getByRole("button", { name: "15m" }));
    expect(lastRead("/live?")?.[0]).toBe("/live?windowMinutes=15&includeIdle=false");
    await user.click(screen.getByRole("button", { name: "6h" }));
    expect(lastRead("/live?")?.[0]).toBe("/live?windowMinutes=360&includeIdle=false");
    await user.click(screen.getByRole("checkbox", { name: "Show idle, stale and revoked agents" }));
    expect(lastRead("/live?")?.[0]).toContain("includeIdle=true");
    await user.click(screen.getByRole("button", { name: "Pause live updates" }));
    expect(lastRead("/live?")?.[1]).toBeUndefined();
    expect(lastRead("/dependencies/waits")?.[1]).toBeUndefined();
    await user.click(screen.getByRole("button", { name: "Resume live updates" }));
    expect(lastRead("/live?")?.[1]).toBe(15_000);
    expect(lastRead("/dependencies/waits")?.[1]).toBe(15_000);
  });

  it("pins embedded mode to its roadmap without a selector", () => {
    render(<Live roadmapId="road-pinned" embedded />);
    expect(screen.queryByRole("combobox", { name: "Filter by roadmap" })).not.toBeInTheDocument();
    expect(lastRead("/live?")?.[0]).toBe(
      "/live?windowMinutes=60&includeIdle=false&roadmapId=road-pinned",
    );
    expect(lastRead("/dependencies/waits")?.[0]).toBe("/dependencies/waits?roadmapId=road-pinned");
    expect(screen.queryByRole("heading", { name: "Live", level: 1 })).not.toBeInTheDocument();
  });

  it("exposes active connections and pulses as tables", async () => {
    const user = userEvent.setup();
    render(<Live />);
    await user.click(screen.getByText("Show as table"));
    const detail = screen.getByText("Show as table").closest("details")!;
    expect(within(detail).getByRole("heading", { name: "Active connections" })).toBeInTheDocument();
    expect(within(detail).getByRole("columnheader", { name: "Connection" })).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "Recent pulses" })).toBeInTheDocument();
    expect(within(detail).getAllByRole("link", { name: "Open record" })).toHaveLength(2);
  });

  it("keeps what waits on the person apart from what waits on agents", () => {
    const { container } = render(<Live />);
    const tiles = Object.fromEntries(
      [...container.querySelectorAll(".live-summary > div")].map((tile) => [
        tile.querySelector("dt")?.textContent,
        tile.querySelector("dd")?.textContent,
      ]),
    );
    expect(tiles["Needs you"]).toBe("4");
    expect(tiles["Waiting on agents"]).toBe("3");
  });

  it("says a session is not observable only for an agent Worklog cannot find sessions for", () => {
    mocks.useData.mockReturnValue({
      data: {
        ...snapshot,
        agents: [
          { ...snapshot.agents[0], sessionState: null, state: "reporting" },
          { ...snapshot.agents[1], sessionState: null, state: "idle" },
          { ...snapshot.agents[1], id: "agent-3", label: "Unknown", providers: [], sessionState: null, state: "idle" },
        ],
      },
      error: null,
      isPending: false,
    });
    const { container } = render(<Live />);
    const session = (id: string) =>
      container.querySelector(`[data-node-key="agent:${id}"] .live-provenance`)?.textContent;
    expect(session("agent-1")).toContain("Session: not observable");
    expect(session("agent-2")).toContain("Session: no local session");
    expect(session("agent-3")).toContain("Session: no local session");
  });

  it("keeps the bow of communication lines bounded however many there are", () => {
    mocks.useData.mockReturnValue({
      data: {
        ...snapshot,
        commEdges: Array.from({ length: 30 }, (_, index) => ({
          ...snapshot.commEdges[1],
          id: `comm-many-${index}`,
        })),
      },
      error: null,
      isPending: false,
    });
    const { container } = render(<Live />);
    const bows = [...container.querySelectorAll(".comm-edge path")].map((path) => {
      const [, fromY, , controlY] = path.getAttribute("d")!.match(/-?\d+(\.\d+)?/g)!.map(Number);
      return controlY - fromY;
    });
    expect(bows).toHaveLength(30);
    // Below the agents, and never as deep as the gap down to the repositories.
    expect(Math.min(...bows)).toBeGreaterThan(0);
    expect(Math.max(...bows)).toBeLessThanOrEqual(82);
  });

  it("groups each step with its items, puts worked-on roadmaps nearest the agents, and orders agents and repositories under what they connect to", () => {
    const work = (id: string, kind: "roadmap" | "step" | "item", parentId: string | null) => ({
      ...snapshot.work[1],
      id,
      kind,
      title: `Work ${id}`,
      roadmapId: "road-1",
      parentId,
      stepTitle: kind === "item" ? `Work ${parentId}` : null,
      href: `#work/${id}`,
    });
    const workEdge = (agentId: string, workId: string) => ({ ...snapshot.workEdges[0], id: `${agentId}-${workId}`, agentId, workId });
    const repoEdge = (agentId: string, repositoryId: string) => ({ ...snapshot.repoEdges[0], id: `${agentId}-${repositoryId}`, agentId, repositoryId });
    mocks.useData.mockReturnValue({
      data: {
        ...snapshot,
        // The server sends a roadmap's steps, then its items.
        work: [
          work("road-1", "roadmap", null),
          work("step-a", "step", null),
          work("step-b", "step", null),
          work("item-b", "item", "step-b"),
          work("item-a", "item", "step-a"),
          // A roadmap nobody works on moves ahead, away from the agents.
          { ...work("road-2", "roadmap", null), roadmapId: "road-2" },
        ],
        agents: [{ ...snapshot.agents[0], id: "agent-x" }, { ...snapshot.agents[1], id: "agent-y" }],
        repositories: [{ ...snapshot.repositories[0], id: "repo-p" }, { ...snapshot.repositories[0], id: "repo-q", name: "Other" }],
        workEdges: [workEdge("agent-x", "item-b"), workEdge("agent-y", "step-a"), workEdge("agent-y", "item-a")],
        repoEdges: [repoEdge("agent-x", "repo-p"), repoEdge("agent-y", "repo-q")],
        commEdges: [],
      },
      error: null,
      isPending: false,
    });
    const { container } = render(<Live />);
    const order = (tier: string) =>
      [...container.querySelectorAll(`.${tier} [data-node-key]`)].map((node) => node.getAttribute("data-node-key"));
    expect(order("live-work-tier")).toEqual(["work:road-2", "work:road-1", "work:step-a", "work:item-a", "work:step-b", "work:item-b"]);
    expect(order("live-agent-tier")).toEqual(["agent:agent-y", "agent:agent-x"]);
    expect(order("live-repo-tier")).toEqual(["repo:repo-q", "repo:repo-p"]);
    expect(screen.getByText("Under Work step-a")).toBeInTheDocument();
    // Two lines into one card arrive side by side, not on one point.
    const end = (id: string) => container.querySelector(`[data-edge-id="${id}"]`)!.getAttribute("d")!.split(" ").slice(-2).join(" ");
    expect(end("agent-y-step-a")).not.toBe(end("agent-y-item-a"));
  });
});
