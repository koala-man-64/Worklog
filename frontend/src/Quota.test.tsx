import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Quota } from "./Quota";
import type { QuotaDashboard } from "./quotaTypes";

const CHAT_ID = "22222222-2222-4222-8222-222222222222";
const SESSION_ID = "sess-claude-99";

function dashboardFixture(): QuotaDashboard {
  const now = new Date();
  const start = new Date(now.getTime() - 2 * 86_400_000).toISOString();
  const resetsAt = new Date(now.getTime() + 5 * 86_400_000).toISOString();
  return {
    generated_at: now.toISOString(),
    sample_count: 18,
    default_target_local: "2026-11-06T17:00",
    target_date: null,
    warnings: [],
    capacity: {
      instance_id: "inst-1",
      revision: 4,
      generated_at: now.toISOString(),
      policy: { reserve_pct: 10 },
      provider_groups: [],
      collector_health: { claude: { state: "healthy" } },
    },
    cards: [
      {
        id: "c1",
        group: "claude",
        group_title: "Claude Code",
        provider: "claude",
        window: "7d (all models)",
        window_min: 10080,
        title: "7d (all models)",
        status: "over",
        raw_status: "over",
        stale: false,
        badge: "▲ 14 pts over linear pace",
        used: 42.5,
        remaining_pct: 57.5,
        pace: 28.5,
        delta: 14.0,
        rate_per_hour: 0.88,
        remaining_min: 7200,
        start,
        resets_at: resetsAt,
        resets_local: "Fri 17:00",
        sample_ts: now.toISOString(),
        sample_local: "Today 08:00",
        age_min: 1.0,
        source: "api",
        samples_count: 18,
        left_value: "5d 0h",
        left_label: "resets Fri 17:00",
        proj_value: "65h left",
        proj_label: "at 0.88 pp/h",
        issue: null,
        reset_credits_count: 0,
        reset_credits_known: false,
        credit_earliest_expiry: null,
        next_reset_local: null,
        runway_with_resets: null,
        token_efficiency: {
          tokens_per_point: 120_000,
          tokens: 5_100_000,
          percentage_points: 42.5,
          start_local: "Sun 17:00",
          end_local: "Today 08:00",
        },
        domain_start: start,
        domain_end: resetsAt,
        chart_heading: "Two-cycle 7d burndown",
        chart_svg: '<svg viewBox="0 0 800 220"><title>Burndown</title></svg>',
        chart_points: [
          {
            x: 400,
            y: 110,
            t: "Today 08:00",
            v: "42.5% used (pace 28.5%)",
          },
        ],
        token_legend: [
          {
            slot: 1,
            slot_class: "tok-1",
            model: "claude-sonnet-4-6",
            tokens: 5_100_000,
          },
        ],
      },
    ],
    usage: {
      generated_at: now.toISOString(),
      days: 7,
      summary: {
        claude: {
          today: {
            prompts: 4,
            requests: 12,
            input: 1000,
            cache_read: 5000,
            cache_write: 500,
            output: 800,
            reasoning: 0,
            total: 7300,
            inferred_requests: 0,
            inferred_input: 0,
          },
          "7d": {
            prompts: 20,
            requests: 60,
            input: 10000,
            cache_read: 50000,
            cache_write: 5000,
            output: 8000,
            reasoning: 0,
            total: 73000,
            inferred_requests: 0,
            inferred_input: 0,
          },
          "30d": {
            prompts: 20,
            requests: 60,
            input: 10000,
            cache_read: 50000,
            cache_write: 5000,
            output: 8000,
            reasoning: 0,
            total: 73000,
            inferred_requests: 0,
            inferred_input: 0,
          },
        },
      },
      by_model_effort: [],
      recent: [
        {
          id: "req-row-1",
          ts: now.toISOString(),
          ts_local: "08:05:00",
          provider: "claude",
          tool: "claude-code",
          session_id: SESSION_ID,
          thread: "main",
          model: "claude-sonnet-4-6",
          effort: "high",
          inferred: false,
          input_tokens: 1200,
          cache_read_tokens: 4800,
          cache_write_tokens: 300,
          output_tokens: 450,
          reasoning_tokens: 0,
          total_tokens: 6750,
          input_tokens_inferred: null,
        },
      ],
      note: "",
    },
    efficiency: null,
    plans: {
      revision: 2,
      plans: { claude: null, codex: null },
      observations: {},
      models: {
        claude: [
          {
            id: "claude-sonnet-4-6",
            label: "Claude Sonnet 4.6",
            speeds: ["standard"],
            rates_version: "2026-10-07",
          },
        ],
        codex: [
          {
            id: "gpt-6-sol",
            label: "GPT-6 Sol",
            speeds: ["standard", "fast"],
            rates_version: "2026-10-07",
          },
        ],
      },
    },
  };
}

function show(component: React.ReactNode) {
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
      {component}
    </QueryClientProvider>,
  );
}

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });

type Write = { method: string; path: string; body: unknown };

function serve(reads: Record<string, () => unknown>) {
  const writes: Write[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    if (method !== "GET") {
      const w = { method, path, body: JSON.parse(String(init?.body)) };
      writes.push(w);
      return json({ accepted: true, revision: 3 });
    }
    const match = Object.keys(reads)
      .sort((a, b) => b.length - a.length)
      .find((prefix) => path.startsWith(prefix));
    return json(match ? reads[match]() : { items: [], total: 0 });
  });
  return { writes };
}

beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("quota-test-" + crypto.randomUUID());
});

it("renders burndown cards, links sessions to tracked Worklog chats, loads request text, and saves policy/plans", async () => {
  const { writes } = serve({
    "/quota/dashboard": () => dashboardFixture(),
    "/chats": () => ({
      items: [
        {
          id: CHAT_ID,
          chatId: SESSION_ID,
          title: "Quota integration session",
        },
      ],
      total: 1,
    }),
    "/quota/recent-text/req-row-1": () => ({
      status: "ok",
      prompt: "Fold quota-burndown into worklog",
      response: "Implemented unified quota service and UI.",
    }),
  });

  show(<Quota />);

  // Weekly burndown card renders
  expect(
    await screen.findByRole("heading", { name: "Quota Burndown" }),
  ).toBeInTheDocument();
  expect(screen.getByText("43%")).toBeInTheDocument();
  expect(
    screen.getByText(/120,000 recorded tokens per 1 percentage point used/i),
  ).toBeInTheDocument();

  // Recent request links session_id directly to #chats/{CHAT_ID}
  const chatLink = await screen.findByRole("link", {
    name: `${SESSION_ID.slice(0, 8)}…`,
  });
  expect(chatLink).toHaveAttribute("href", `#chats/${CHAT_ID}`);

  // On-demand request text expansion
  await userEvent.click(screen.getByRole("button", { name: "View text" }));
  expect(
    await screen.findByText("Fold quota-burndown into worklog"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Implemented unified quota service and UI."),
  ).toBeInTheDocument();

  // Change reserve buffer policy
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Reserve buffer percentage" }),
    "20",
  );
  expect(writes).toContainEqual({
    method: "POST",
    path: "/quota/policy",
    body: { reserve_pct: 20 },
  });

  // Save a Claude credit plan
  const claudePlanHeading = screen.getByRole("heading", {
    name: "Claude Extra Usage (USD)",
  });
  const claudeCard = claudePlanHeading.closest("article")!;
  await userEvent.type(
    within(claudeCard).getByLabelText("Budget (USD)"),
    "50.00",
  );
  await userEvent.click(
    within(claudeCard).getByRole("button", { name: "Save plan" }),
  );

  const planWrite = writes.find((w) => w.path === "/quota/plans");
  expect(planWrite).toBeDefined();
  expect(planWrite?.body).toMatchObject({
    provider: "claude",
    expected_revision: 2,
    plan: {
      amount: "50.00",
      model: "claude-sonnet-4-6",
      speed: "standard",
    },
  });
});
