import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { aStep } from "./fixtures";
import {
  EMPTY_GATE_SUMMARY,
  GateBadge,
  RoadmapGatesSummary,
  StepGatesPanel,
} from "./StepGates";
import type { RoadmapGate, StepGate } from "./apiTypes";

const ROADMAP = "10000000-0000-4000-8000-000000000001";
const STEP = "20000000-0000-4000-8000-000000000001";
const GATE_1 = "30000000-0000-4000-8000-000000000001";
const GATE_2 = "30000000-0000-4000-8000-000000000002";
const TICKET = "40000000-0000-4000-8000-000000000001";

function aGate(overrides: Partial<StepGate> = {}): StepGate {
  return {
    id: GATE_1,
    stepId: STEP,
    roadmapId: ROADMAP,
    stepTitle: "Deploy service",
    title: "Security sign-off",
    criteria: "Confirm auth boundary.",
    kind: "approval",
    humanOnly: true,
    target: null,
    storedStatus: "open",
    state: "open",
    satisfied: false,
    autoEvaluated: false,
    resolution: null,
    position: 0,
    archived: false,
    revision: 1,
    createdAt: "2026-11-28T10:00:00Z",
    updatedAt: "2026-11-28T10:00:00Z",
    ...overrides,
  };
}

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

beforeEach(() => {
  setDataContext("test-gates");
  vi.restoreAllMocks();
});

it("renders GateBadge counts for blocking and satisfied summaries", () => {
  const { rerender } = render(<GateBadge summary={EMPTY_GATE_SUMMARY} />);
  expect(screen.queryByText(/gate/i)).toBeNull();

  rerender(
    <GateBadge
      summary={{
        ...EMPTY_GATE_SUMMARY,
        total: 2,
        open: 2,
        blocking: 2,
        humanOnlyBlocking: 1,
      }}
    />,
  );
  expect(screen.getByText("2 open gates · Human")).toBeInTheDocument();

  rerender(
    <GateBadge
      summary={{
        ...EMPTY_GATE_SUMMARY,
        total: 1,
        passed: 1,
        blocking: 0,
      }}
    />,
  );
  expect(screen.getByText("Gates met")).toBeInTheDocument();
});

it("creates, passes, waives, and reopens gates inside StepGatesPanel", async () => {
  const user = userEvent.setup();
  let gates: StepGate[] = [
    aGate(),
    aGate({
      id: GATE_2,
      title: "Fix race ticket",
      kind: "prerequisite",
      humanOnly: false,
      target: {
        type: "ticket",
        id: TICKET,
        title: "Race condition",
        status: "open",
        available: true,
        roadmapId: ROADMAP,
        href: `#tickets/${TICKET}`,
      },
      autoEvaluated: true,
    }),
  ];
  const calls: { method: string; url: string; body?: unknown }[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body =
        typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, url, body });

      if (url.includes(`/steps/${STEP}/gates?archived=false`) && method === "GET") {
        const blocking = gates.filter((g) => !g.satisfied).length;
        return new Response(
          JSON.stringify({
            items: gates,
            total: gates.length,
            summary: {
              ...EMPTY_GATE_SUMMARY,
              total: gates.length,
              open: blocking,
              passed: gates.filter((g) => g.state === "passed").length,
              waived: gates.filter((g) => g.state === "waived").length,
              blocking,
              humanOnlyBlocking: gates.filter((g) => !g.satisfied && g.humanOnly).length,
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith(`/gates/${GATE_1}/pass`) && method === "POST") {
        gates = gates.map((g) =>
          g.id === GATE_1
            ? {
                ...g,
                storedStatus: "passed",
                state: "passed",
                satisfied: true,
                revision: 2,
                resolution: {
                  resolvedAt: "2026-11-28T11:00:00Z",
                  actorId: "local-user",
                  actorLabel: "You",
                  actorKind: "human",
                  proposalId: null,
                  note: String((body as { note?: string })?.note ?? ""),
                },
              }
            : g,
        );
        return new Response(JSON.stringify(gates[0]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith(`/gates/${GATE_2}/waive`) && method === "POST") {
        gates = gates.map((g) =>
          g.id === GATE_2
            ? {
                ...g,
                storedStatus: "waived",
                state: "waived",
                satisfied: true,
                autoEvaluated: false,
                revision: 2,
                resolution: {
                  resolvedAt: "2026-11-28T11:05:00Z",
                  actorId: "local-user",
                  actorLabel: "You",
                  actorKind: "human",
                  proposalId: null,
                  note: String((body as { reason?: string })?.reason ?? ""),
                },
              }
            : g,
        );
        return new Response(JSON.stringify(gates[1]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith(`/steps/${STEP}/gates`) && method === "POST") {
        const created = aGate({
          id: "30000000-0000-4000-8000-000000000003",
          title: String((body as { title?: string })?.title ?? ""),
          humanOnly: Boolean((body as { humanOnly?: boolean })?.humanOnly),
        });
        gates = [...gates, created];
        return new Response(JSON.stringify(created), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );

  const step = aStep({
    id: STEP,
    title: "Deploy service",
    status: "doing",
    gateSummary: {
      ...EMPTY_GATE_SUMMARY,
      total: 2,
      open: 2,
      blocking: 2,
      humanOnlyBlocking: 1,
    },
  });

  wrap(
    <StepGatesPanel
      roadmapId={ROADMAP}
      step={step}
      isParent={false}
      allChildrenDone={false}
      disabled={false}
    />,
  );

  await screen.findByText("Security sign-off");
  expect(screen.getByText("Human-only")).toBeInTheDocument();
  expect(screen.getByText("Fix race ticket")).toBeInTheDocument();
  expect(screen.getByText("Auto-evaluated")).toBeInTheDocument();

  // Pass the manual gate.
  await user.click(screen.getByRole("button", { name: "Pass gate" }));
  await user.type(
    screen.getByPlaceholderText("Evidence, build link, or sign-off note"),
    "Reviewed PR",
  );
  await user.click(screen.getByRole("button", { name: "Confirm pass" }));
  await waitFor(() =>
    expect(screen.getByText(/Passed by/)).toBeInTheDocument(),
  );

  // Waive the linked ticket gate with a required reason.
  await user.click(screen.getByRole("button", { name: "Waive" }));
  await user.type(
    screen.getByPlaceholderText("Why this gate is waived for completion"),
    "Deferred to next wave",
  );
  await user.click(screen.getByRole("button", { name: "Confirm waive" }));
  await waitFor(() =>
    expect(screen.getByText(/Deferred to next wave/)).toBeInTheDocument(),
  );

  // Add a new gate.
  await user.click(screen.getByRole("button", { name: "Add gate" }));
  await user.type(screen.getByLabelText("New gate title"), "Load test green");
  await user.click(screen.getByRole("button", { name: "Create gate" }));
  await screen.findByText("Load test green");

  expect(calls.some((c) => c.method === "POST" && c.url.endsWith(`/gates/${GATE_1}/pass`))).toBe(true);
  expect(calls.some((c) => c.method === "POST" && c.url.endsWith(`/gates/${GATE_2}/waive`))).toBe(true);
  expect(calls.some((c) => c.method === "POST" && c.url.endsWith(`/steps/${STEP}/gates`))).toBe(true);
});

it("shows open roadmap gates summary with step link and phase tag", async () => {
  const roadmapGate: RoadmapGate = {
    ...aGate({ title: "Phase 1 sign-off" }),
    stepStatus: "doing",
    isParent: true,
    phaseId: STEP,
    phaseTitle: "Phase 1",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          items: [roadmapGate],
          total: 1,
          summary: {
            ...EMPTY_GATE_SUMMARY,
            total: 1,
            open: 1,
            blocking: 1,
            humanOnlyBlocking: 1,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );

  wrap(<RoadmapGatesSummary roadmapId={ROADMAP} />);
  await screen.findByText("Phase 1 sign-off");
  expect(screen.getByText("Phase gate")).toBeInTheDocument();
  expect(screen.getByText(/1 human-only awaiting you/)).toBeInTheDocument();
});
