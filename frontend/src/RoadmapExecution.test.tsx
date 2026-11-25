import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RoadmapExecution } from "./RoadmapExecution";
import type { Repository, RoadmapDispatch } from "./apiTypes";

const REPO_A: Repository = {
  id: "repo-a",
  name: "contracts",
  url: "https://github.com/org/contracts",
  remoteIdentity: "github.com/org/contracts",
  duplicateOf: null,
  mergedInto: null,
  description: "Shared schemas",
  wipCap: 1,
  tier: 1,
  registeredBy: null,
  reviewed: true,
  revision: 1,
  archived: false,
  createdAt: "2026-11-24T00:00:00Z",
  updatedAt: "2026-11-24T00:00:00Z",
};

const REPO_B: Repository = {
  ...REPO_A,
  id: "repo-b",
  name: "control-plane",
  url: "https://github.com/org/control-plane",
  remoteIdentity: "github.com/org/control-plane",
  tier: 2,
};

const dispatchFixture: RoadmapDispatch = {
  roadmap: {
    id: "rm-1",
    title: "Asset Allocation",
    revision: 3,
    archived: false,
  },
  summary: {
    totalLeaves: 5,
    doingLeaves: 2,
    blockedLeaves: 0,
    doneLeaves: 0,
    overCapRepos: 1,
    competingDoingCount: 1,
    multiRepoLeafCount: 1,
    unassignedLeafCount: 1,
    pendingRepinCount: 1,
  },
  waves: [
    {
      id: "wave-1",
      roadmapId: "rm-1",
      name: "Wave 1",
      summary: "Freeze contracts",
      status: "active",
      position: 0,
      discussionId: "disc-w1",
      archived: false,
      revision: 2,
      createdAt: "2026-11-24T00:00:00Z",
      updatedAt: "2026-11-24T00:00:00Z",
      activatedAt: "2026-11-24T01:00:00Z",
      completedAt: null,
      stepCount: 2,
      doneStepCount: 0,
      frozenRepoCount: 1,
      pendingRepinCount: 1,
    },
  ],
  activeWave: {
    id: "wave-1",
    roadmapId: "rm-1",
    name: "Wave 1",
    summary: "Freeze contracts",
    status: "active",
    position: 0,
    discussionId: "disc-w1",
    archived: false,
    revision: 2,
    createdAt: "2026-11-24T00:00:00Z",
    updatedAt: "2026-11-24T00:00:00Z",
    activatedAt: "2026-11-24T01:00:00Z",
    completedAt: null,
    stepCount: 2,
    doneStepCount: 0,
    frozenRepoCount: 1,
    pendingRepinCount: 1,
    stepIds: ["step-wave"],
    steps: [
      {
        id: "step-wave",
        title: "Publish contract v2",
        status: "doing",
        workClass: "critical_path",
        parentId: "phase-1",
        repositoryIds: ["repo-a"],
      },
    ],
    repositories: [
      {
        repositoryId: "repo-a",
        repositoryName: "contracts",
        tier: 1,
        freezeStatus: "frozen",
        frozenRef: "contracts@v2.0.0",
        frozenAt: "2026-11-24T01:30:00Z",
        frozenByLabel: "You",
        notes: "Frozen",
        revision: 2,
        updatedAt: "2026-11-24T01:30:00Z",
      },
    ],
    repins: [
      {
        waveId: "wave-1",
        upstreamRepoId: "repo-a",
        upstreamRepoName: "contracts",
        consumerRepoId: "repo-b",
        consumerRepoName: "control-plane",
        status: "in_progress",
        pinnedRef: "contracts@v2.0.0",
        prUrl: "https://github.com/org/control-plane/pull/99",
        commitSha: "abcdef123456",
        reportedByLabel: "Codex",
        verifiedAt: null,
        revision: 2,
        updatedAt: "2026-11-24T02:00:00Z",
      },
    ],
    tierPipeline: [
      {
        tier: 1,
        repositories: [
          {
            repositoryId: "repo-a",
            repositoryName: "contracts",
            tier: 1,
            freezeStatus: "frozen",
            frozenRef: "contracts@v2.0.0",
            frozenAt: "2026-11-24T01:30:00Z",
            frozenByLabel: "You",
            notes: "Frozen",
            revision: 2,
            updatedAt: "2026-11-24T01:30:00Z",
            stepCount: 1,
            doingCount: 1,
            doneCount: 0,
            blockedCount: 0,
            pendingDownstreamRepins: 1,
            pendingUpstreamRepins: 0,
          },
        ],
      },
    ],
  },
  repoLanes: [
    {
      repositoryId: "repo-a",
      repositoryName: "contracts",
      url: "https://github.com/org/contracts",
      discussionId: "disc-a",
      wipCap: 1,
      effectiveWipCap: 1,
      tier: 1,
      upstreamIds: [],
      downstreamIds: ["repo-b"],
      slotSummary: {
        maxActiveLeases: 2,
        activeCount: 1,
        queuedCount: 0,
        dirtyCount: 0,
        rescuedCount: 0,
        baseReadonly: false,
        configured: true,
        revision: 1,
      },
      activeWriters: 1,
      doingCount: 2,
      overWipCap: true,
      freezeState: {
        repositoryId: "repo-a",
        repositoryName: "contracts",
        tier: 1,
        freezeStatus: "frozen",
        frozenRef: "contracts@v2.0.0",
        frozenAt: "2026-11-24T01:30:00Z",
        frozenByLabel: "You",
        notes: "Frozen",
        revision: 2,
        updatedAt: "2026-11-24T01:30:00Z",
      },
      repins: [],
      doing: [
        {
          id: "step-wave",
          title: "Publish contract v2",
          status: "doing",
          revision: 3,
          workClass: "critical_path",
          repositoryIds: ["repo-a"],
          multiRepo: false,
          waveId: "wave-1",
          inActiveWave: true,
          queuePosition: 0,
          phasePath: [{ id: "phase-1", title: "Phase 1: Core" }],
          activeAgents: [],
        },
      ],
      competingWip: [
        {
          id: "step-comp",
          title: "Unplanned cleanup",
          status: "doing",
          revision: 4,
          workClass: "refactor",
          repositoryIds: ["repo-a"],
          multiRepo: false,
          waveId: null,
          inActiveWave: false,
          queuePosition: null,
          phasePath: [{ id: "phase-1", title: "Phase 1: Core" }],
          activeAgents: [],
        },
      ],
      queued: [
        {
          id: "step-queued",
          title: "Next schema change",
          status: "open",
          revision: 1,
          workClass: "standard",
          repositoryIds: ["repo-a"],
          multiRepo: false,
          waveId: null,
          inActiveWave: false,
          queuePosition: 1,
          phasePath: [{ id: "phase-1", title: "Phase 1: Core" }],
          activeAgents: [],
        },
      ],
      blocked: [],
      done: [],
      blockedCount: 0,
      doneCount: 0,
      participations: [],
    },
  ],
  multiRepoLeaves: [
    {
      id: "step-multi",
      title: "Wire contracts and control-plane",
      status: "open",
      revision: 2,
      workClass: "standard",
      repositoryIds: ["repo-a", "repo-b"],
      multiRepo: true,
      waveId: null,
      inActiveWave: false,
      queuePosition: null,
      phasePath: [{ id: "phase-1", title: "Phase 1: Core" }],
      activeAgents: [],
    },
  ],
  unassignedLeaves: [
    {
      id: "step-unassigned",
      title: "[control-plane] Add health check",
      status: "open",
      revision: 1,
      workClass: "",
      repositoryIds: [],
      multiRepo: false,
      waveId: null,
      inActiveWave: false,
      queuePosition: null,
      phasePath: [{ id: "phase-1", title: "Phase 1: Core" }],
      activeAgents: [],
      suggestedRepoIds: ["repo-b"],
    },
  ],
  gates: {
    gateSteps: [
      {
        id: "step-gate",
        title: "60-day golden path",
        status: "open",
        revision: 1,
        workClass: "gate",
        repositoryIds: ["repo-a", "repo-b"],
        multiRepo: false,
        waveId: null,
        inActiveWave: false,
        queuePosition: null,
        phasePath: [{ id: "phase-1", title: "Phase 1: Core" }],
        activeAgents: [],
      },
    ],
    phases: [
      {
        id: "phase-1",
        title: "Phase 1: Core",
        status: "doing",
        totalLeaves: 5,
        doneLeaves: 0,
        doingLeaves: 2,
        blockedLeaves: 0,
        openLeaves: 3,
      },
    ],
  },
};

function renderView(ui: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      {ui}
    </QueryClientProvider>,
  );
}

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });

describe("RoadmapExecution", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setDataContext("exec-test-" + crypto.randomUUID());
  });

  it("renders wave tier pipeline, verifies re-pins, pauses competing WIP, guards frozen start, splits multi-repo leaves, and applies suggestions", async () => {
    const writes: { method: string; path: string; body: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (method !== "GET") {
        writes.push({
          method,
          path,
          body: init?.body ? JSON.parse(String(init.body)) : null,
        });
        return json({ ok: true });
      }
      if (path.includes("/roadmaps/rm-1/dispatch")) return json(dispatchFixture);
      if (path.includes("/repositories"))
        return json({ items: [REPO_A, REPO_B], total: 2 });
      return json({});
    });

    renderView(<RoadmapExecution roadmapId="rm-1" archived={false} />);

    // Wave panel, freeze state, and consumer re-pin table render.
    expect(await screen.findAllByText("contracts@v2.0.0")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() =>
      expect(writes[0]).toEqual({
        method: "POST",
        path: "/api/v1/waves/wave-1/repins/verify",
        body: {
          revision: 2,
          upstreamRepoId: "repo-a",
          consumerRepoId: "repo-b",
          status: "verified",
        },
      }),
    );

    // Competing Doing WIP can be paused to open with 1 click.
    await userEvent.click(screen.getByRole("button", { name: "Pause to open" }));
    await waitFor(() =>
      expect(writes.at(-1)).toEqual({
        method: "PATCH",
        path: "/api/v1/steps/step-comp/progress",
        body: { revision: 4, status: "open" },
      }),
    );

    // Starting a queued step on a frozen / over-cap repo shows an inline confirmation guardrail first.
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      /contracts is frozen in Wave 1/,
    );
    await userEvent.click(screen.getByRole("button", { name: "Confirm start" }));
    await waitFor(() =>
      expect(writes.at(-1)).toEqual({
        method: "PATCH",
        path: "/api/v1/steps/step-queued/progress",
        body: { revision: 1, status: "doing" },
      }),
    );

    // Unassigned leaf 1-click Apply suggestion updates repositoryIds.
    await userEvent.click(
      screen.getByRole("button", { name: "Apply suggestion" }),
    );
    await waitFor(() =>
      expect(writes.at(-1)).toEqual({
        method: "PATCH",
        path: "/api/v1/steps/step-unassigned",
        body: { revision: 1, repositoryIds: ["repo-b"] },
      }),
    );

    // Multi-repo leaf Split by repo opens native dialog pre-populated with 2 children and submits step.split.
    const multiSection = screen.getByRole("region", {
      name: "Multi-repo leaves",
    });
    await userEvent.click(
      within(multiSection).getByRole("button", { name: "Split by repo" }),
    );
    const submitSplit = await screen.findByRole("button", {
      name: "Create 2 child steps",
    });
    await userEvent.click(submitSplit);
    await waitFor(() =>
      expect(writes.at(-1)?.path).toBe("/api/v1/steps/step-multi/split"),
    );
    const splitPayload = writes.at(-1)?.body as {
      revision: number;
      children: { repositoryIds: string[] }[];
    };
    expect(splitPayload.revision).toBe(2);
    expect(splitPayload.children.map((c) => c.repositoryIds)).toEqual([
      ["repo-a"],
      ["repo-b"],
    ]);
  });
});
