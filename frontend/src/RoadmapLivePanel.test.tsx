import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  LiveAgentNode,
  LivePulse,
  LiveRepoNode,
  LiveSnapshot,
  LiveWorkNode,
} from "./apiTypes";

const mocks = vi.hoisted(() => ({ useData: vi.fn() }));
vi.mock("./hooks", () => ({ useData: mocks.useData }));
import { RoadmapLivePanel } from "./RoadmapLivePanel";

const ROAD = "road-1";

function work(
  fields: Partial<LiveWorkNode> & Pick<LiveWorkNode, "id" | "kind" | "title">,
): LiveWorkNode {
  return {
    status: "doing",
    roadmapId: ROAD,
    roadmapTitle: "Ship Worklog",
    parentId: null,
    stepTitle: null,
    rank: null,
    openQuestions: 0,
    waitingProposals: 0,
    waitingProposalIds: [],
    agentCount: 0,
    needsYou: false,
    href: `#roadmaps/${ROAD}/${fields.id}`,
    ...fields,
  };
}

function agent(
  fields: Partial<LiveAgentNode> & Pick<LiveAgentNode, "id" | "label">,
): LiveAgentNode {
  return {
    avatar: null,
    active: true,
    state: "working",
    declaredState: "reporting",
    sessionState: null,
    providers: ["claude"],
    model: "opus",
    registeredById: null,
    registeredByLabel: null,
    openRuns: 1,
    openParticipations: 1,
    roles: [],
    chats: [],
    lastAt: "2026-11-27T11:59:00Z",
    latestNote: "",
    href: `#agents/${fields.id}`,
    ...fields,
  };
}

const repo: LiveRepoNode = {
  id: "repo-1",
  name: "Worklog",
  url: null,
  unreviewed: false,
  discussionId: null,
  branches: ["claude/live-panel"],
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
  baseReadonly: false,
  lastAt: null,
  href: "#repositories/repo-1",
};

const pulse = (n: number): LivePulse => ({
  id: `pulse-${n}`,
  kind: "update",
  at: `2026-11-27T11:${String(59 - n).padStart(2, "0")}:00Z`,
  actorKind: "agent",
  actorId: "agent-1",
  actorLabel: "Panel builder",
  targetActorId: null,
  targetActorLabel: null,
  workId: null,
  workTitle: null,
  repositoryId: null,
  repositoryName: null,
  discussionId: null,
  proposalId: null,
  summary: `Update ${n}`,
  href: `#discussions/d-${n}`,
});

function snapshot(fields: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    generatedAt: "2026-11-27T12:00:00Z",
    windowMinutes: 60,
    roadmapId: ROAD,
    roadmapTitle: "Ship Worklog",
    summary: {
      activeAgents: 2,
      workingChats: 1,
      activeWorkItems: 1,
      activeSteps: 2,
      activeRepos: 1,
      repoOverlaps: 1,
      overCapRepos: 0,
      pendingRequests: 0,
      waitingOnAgents: 0,
      waitingProposals: 1,
      openQuestions: 3,
      needsYou: 4,
    },
    roadmaps: [],
    work: [
      work({
        id: ROAD,
        kind: "roadmap",
        title: "Ship Worklog",
        href: `#roadmaps/${ROAD}`,
      }),
      work({
        id: "step-1",
        kind: "step",
        title: "Show Live beside the plan",
        agentCount: 1,
        needsYou: true,
      }),
      work({
        id: "item-1",
        kind: "item",
        title: "Build the panel",
        parentId: "step-1",
        stepTitle: "Show Live beside the plan",
        status: "waiting",
        agentCount: 1,
        href: "#work/item-1",
      }),
      work({
        id: "road-2",
        kind: "roadmap",
        title: "Sub plan",
        roadmapId: "road-2",
        roadmapTitle: "Sub plan",
        href: "#roadmaps/road-2",
      }),
      work({
        id: "step-2",
        kind: "step",
        title: "Sub step",
        roadmapId: "road-2",
        roadmapTitle: "Sub plan",
        agentCount: 1,
        href: "#roadmaps/road-2/step-2",
      }),
    ],
    agents: [
      agent({
        id: "agent-1",
        label: "Panel builder",
        sessionState: "working",
        latestNote: "Wiring the panel",
        chats: [
          {
            id: "chat-1",
            chatId: "session-1",
            title: "Live panel chat",
            provider: "claude",
            model: "opus",
            working: true,
            lastAt: null,
            href: "#chats/chat-1",
          },
        ],
      }),
      agent({
        id: "agent-2",
        label: "Reviewer",
        state: "waiting",
        providers: ["codex"],
      }),
    ],
    untrackedSessions: [],
    repositories: [repo],
    workEdges: [
      {
        id: "we-0",
        agentId: "agent-1",
        workId: ROAD,
        workKind: "roadmap",
        via: ["participation"],
        active: true,
        stale: false,
        lastAt: null,
      },
      {
        id: "we-1",
        agentId: "agent-1",
        workId: "step-1",
        workKind: "step",
        via: ["participation"],
        active: true,
        stale: false,
        lastAt: null,
      },
      {
        id: "we-2",
        agentId: "agent-2",
        workId: "step-2",
        workKind: "step",
        via: ["participation"],
        active: true,
        stale: true,
        lastAt: null,
      },
    ],
    repoEdges: [
      {
        id: "re-1",
        agentId: "agent-1",
        repositoryId: "repo-1",
        via: ["participation"],
        branch: "claude/live-panel",
        worktreePath: "C:/w",
        active: true,
        stale: false,
        lastAt: null,
      },
    ],
    commEdges: [],
    lanes: [],
    pulses: Array.from({ length: 10 }, (_, n) => pulse(n)),
    ...fields,
  };
}

let answer: {
  data: LiveSnapshot | undefined;
  error: Error | null;
  isPending: boolean;
};
beforeEach(() => {
  answer = { data: snapshot(), error: null, isPending: false };
  mocks.useData.mockReset();
  mocks.useData.mockImplementation((path: string) =>
    path.startsWith("/live")
      ? answer
      : { data: { items: [], total: 0 }, error: null, isPending: false },
  );
});

const liveReads = () =>
  mocks.useData.mock.calls.filter(([path]) => String(path).startsWith("/live"));
const panel = () =>
  screen.getByRole("complementary", { name: "Live activity" });

it("reads Live for its roadmap over the last hour, every 15 seconds, until paused", async () => {
  render(<RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />);
  expect(liveReads().at(-1)).toEqual([
    `/live?windowMinutes=60&includeIdle=false&roadmapId=${ROAD}`,
    15_000,
    { keepPrevious: true },
  ]);
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Activity window" }),
    "6h",
  );
  expect(liveReads().at(-1)?.[0]).toBe(
    `/live?windowMinutes=360&includeIdle=false&roadmapId=${ROAD}`,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Pause live updates" }),
  );
  expect(liveReads().at(-1)?.[1]).toBeUndefined();
  expect(within(panel()).getByText("Paused")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Resume live updates" }),
  ).toHaveAttribute("aria-pressed", "true");
  // The roadmap's open agent waits pause with it.
  const waits = mocks.useData.mock.calls.filter(([path]) =>
    String(path).startsWith("/dependencies/waits"),
  );
  expect(waits.at(-1)).toEqual([
    `/dependencies/waits?roadmapId=${ROAD}`,
    undefined,
  ]);
});

it("sums up what moves, naming a warning only when there is one", () => {
  render(<RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />);
  const summary = panel().querySelector(
    "dl.roadmap-live-summary",
  ) as HTMLElement;
  const terms = within(summary)
    .getAllByRole("term")
    .map((term) => [term.textContent, term.nextSibling?.textContent]);
  expect(terms).toEqual([
    ["Needs you", "4"],
    ["Active agents", "2"],
    ["Work in flight", "3"],
    ["Repositories", "1"],
    ["Overlaps", "1"],
  ]);
});

it("lists each agent with the work and repositories it is tied to, leaving out this roadmap itself", () => {
  render(<RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />);
  const builder = within(panel()).getByRole("list", {
    name: "Panel builder works on",
  });
  expect(
    within(builder)
      .getAllByRole("link")
      .map((a) => [a.textContent, a.getAttribute("href")]),
  ).toEqual([
    ["Show Live beside the plan", `#roadmaps/${ROAD}/step-1`],
    ["Worklog", "#repositories/repo-1"],
  ]);
  expect(within(builder).getByText("claude/live-panel")).toBeInTheDocument();
  const builderRow = builder.closest("li.roadmap-live-agent") as HTMLElement;
  expect(within(builderRow).getByText("Working")).toBeInTheDocument();
  expect(within(builderRow).getByText("Session: working")).toBeInTheDocument();
  expect(
    within(builderRow).getByRole("link", { name: "Live panel chat" }),
  ).toHaveAttribute("href", "#chats/chat-1");
  expect(within(builderRow).getByText("Wiring the panel")).toBeInTheDocument();
  // A tie whose report went stale shows, muted.
  const reviewer = within(panel()).getByRole("list", {
    name: "Reviewer works on",
  });
  expect(
    within(reviewer).getByRole("link", { name: "Sub step" }).closest("li"),
  ).toHaveClass("is-stale");
  const reviewerRow = reviewer.closest("li.roadmap-live-agent") as HTMLElement;
  expect(within(reviewerRow).getByText("Waiting on you")).toBeInTheDocument();
  expect(
    within(reviewerRow).getByText("Session: not observable"),
  ).toBeInTheDocument();
});

it("shows the work moving under its roadmap, sub-roadmaps by their own card", () => {
  render(<RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />);
  const heading = within(panel()).getByRole("heading", {
    name: /^Plans & work/,
  });
  const section = heading.parentElement as HTMLElement;
  const titles = within(section)
    .getAllByRole("link")
    .map((a) => a.textContent);
  // This roadmap's own card is the page itself; the sub-roadmap's heads its steps.
  expect(titles).toEqual([
    "Show Live beside the plan",
    "Build the panel",
    "Sub plan",
    "Sub step",
  ]);
  expect(heading).toHaveTextContent("Plans & work 4");
  const step = within(section)
    .getByRole("link", { name: "Show Live beside the plan" })
    .closest("li") as HTMLElement;
  expect(within(step).getByText("Needs you")).toBeInTheDocument();
  expect(within(step).getByText("1 connected agent")).toBeInTheDocument();
  const item = within(section)
    .getByRole("link", { name: "Build the panel" })
    .closest("li") as HTMLElement;
  expect(item).toHaveTextContent("under Show Live beside the plan");
});

it("names a sub-roadmap whose own card does not show", () => {
  answer.data = snapshot({
    work: snapshot().work.filter((node) => node.id !== "road-2"),
  });
  render(<RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />);
  expect(
    within(panel()).getByText("Sub plan", {
      selector: ".roadmap-live-caption",
    }),
  ).toBeInTheDocument();
  expect(
    within(panel()).queryByText("Ship Worklog", {
      selector: ".roadmap-live-caption",
    }),
  ).toBeNull();
});

it("shows repositories with their slots and overlap, and the eight latest pulses", () => {
  render(<RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />);
  const repoSection = within(panel()).getByRole("heading", {
    name: /^Repositories/,
  }).parentElement as HTMLElement;
  expect(within(repoSection).getByText("Slots 2/2")).toBeInTheDocument();
  expect(within(repoSection).getByText("+1 queued")).toBeInTheDocument();
  expect(
    within(repoSection).getByText("Overlap: 2 agents"),
  ).toBeInTheDocument();
  const pulses = within(panel()).getByRole("heading", { name: "Recent pulses" })
    .parentElement as HTMLElement;
  const links = within(pulses).getAllByRole("link");
  expect(links).toHaveLength(8);
  expect(links[0]).toHaveTextContent("Update 0");
  expect(links[0]).toHaveAttribute("href", "#discussions/d-0");
});

it("says when nothing moves on the roadmap in the window", () => {
  answer.data = snapshot({
    work: [snapshot().work[0]],
    agents: [],
    repositories: [],
    workEdges: [],
    repoEdges: [],
    pulses: [],
  });
  render(<RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />);
  expect(
    within(panel()).getByText(
      "Nothing is moving on this roadmap in this window.",
    ),
  ).toBeInTheDocument();
  expect(within(panel()).queryByRole("heading", { level: 3 })).toBeNull();
});

it("reads, fails and ignores another roadmap's answer kept while this one loads", () => {
  answer = { data: undefined, error: null, isPending: true };
  const { rerender } = render(
    <RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />,
  );
  expect(
    within(panel()).getByText("Reading current activity…"),
  ).toBeInTheDocument();
  answer = {
    data: undefined,
    error: new Error("Live is unavailable"),
    isPending: false,
  };
  rerender(<RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />);
  expect(within(panel()).getByText("Live is unavailable")).toBeInTheDocument();
  answer = {
    data: snapshot({ roadmapId: "road-9" }),
    error: null,
    isPending: false,
  };
  rerender(<RoadmapLivePanel roadmapId={ROAD} onClose={() => undefined} />);
  expect(within(panel()).queryByText("Panel builder")).toBeNull();
  expect(within(panel()).queryByRole("term")).toBeNull();
});

it("links the full view and hides on request", async () => {
  const onClose = vi.fn();
  render(
    <RoadmapLivePanel id="live-panel" roadmapId={ROAD} onClose={onClose} />,
  );
  expect(panel()).toHaveAttribute("id", "live-panel");
  expect(
    within(panel()).getByRole("link", { name: "Open full Live" }),
  ).toHaveAttribute("href", `#roadmaps/${ROAD}/live`);
  await userEvent.click(
    within(panel()).getByRole("button", { name: "Hide Live panel" }),
  );
  expect(onClose).toHaveBeenCalledOnce();
});

it("shows a detail tab beside Live, supports keyboard switching, and only polls Live while active", async () => {
  const onTabChange = vi.fn();
  const { rerender } = render(
    <RoadmapLivePanel
      roadmapId={ROAD}
      onClose={() => undefined}
      activeTab="details"
      onTabChange={onTabChange}
      detailTab={{
        label: "Step details",
        render: () => <p>Selected step body</p>,
      }}
    />,
  );
  const tablist = within(panel()).getByRole("tablist", {
    name: "Live panel views",
  });
  const liveTab = within(tablist).getByRole("tab", { name: "Live" });
  const detailTab = within(tablist).getByRole("tab", { name: "Step details" });
  expect(liveTab).toHaveAttribute("aria-selected", "false");
  expect(detailTab).toHaveAttribute("aria-selected", "true");
  expect(within(panel()).getByText("Selected step body")).toBeVisible();
  // Opened directly on Step details, Live does not read until opened.
  expect(liveReads()).toEqual([]);

  // ArrowLeft from Step details requests the Live tab.
  detailTab.focus();
  await userEvent.keyboard("{ArrowLeft}");
  expect(onTabChange).toHaveBeenLastCalledWith("live");

  rerender(
    <RoadmapLivePanel
      roadmapId={ROAD}
      onClose={() => undefined}
      activeTab="live"
      onTabChange={onTabChange}
      detailTab={{
        label: "Step details",
        render: () => <p>Selected step body</p>,
      }}
    />,
  );
  expect(liveTab).toHaveAttribute("aria-selected", "true");
  expect(liveReads().at(-1)).toEqual([
    `/live?windowMinutes=60&includeIdle=false&roadmapId=${ROAD}`,
    15_000,
    { keepPrevious: true },
  ]);
  expect(within(panel()).getByText("Updating")).toBeVisible();

  // Switching back to Step details pauses Live polling while keeping both tabs mounted.
  rerender(
    <RoadmapLivePanel
      roadmapId={ROAD}
      onClose={() => undefined}
      activeTab="details"
      onTabChange={onTabChange}
      detailTab={{
        label: "Step details",
        render: () => <p>Selected step body</p>,
      }}
    />,
  );
  expect(liveReads().at(-1)?.[1]).toBeUndefined();
  expect(within(panel()).getByText("Selected step body")).toBeVisible();
});
