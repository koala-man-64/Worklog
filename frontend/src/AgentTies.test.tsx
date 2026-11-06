import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { setDataContext } from "./api";
import { AgentTieFold, AgentTiedTo, AgentTies, useTiesOn } from "./AgentTies";
import { TIE_TYPES, pickerFilter, pickerPath, tieHref } from "./agentTieTypes";
import type { AgentTie, TieType } from "./agentTieTypes";

const ITEM_ID = "11111111-1111-4111-8111-111111111111";
const BUILDER = "aaaaaaaa-0000-4000-8000-000000000001";
const REVIEWER = "aaaaaaaa-0000-4000-8000-000000000002";
const RETIRED = "aaaaaaaa-0000-4000-8000-000000000003";
const SPARE = "aaaaaaaa-0000-4000-8000-000000000004";

function tie(
  agentId: string,
  label: string,
  fields: Partial<AgentTie> = {},
): AgentTie {
  return {
    id: "tie-" + agentId + (fields.targetId ?? ""),
    agentId,
    agent: { id: agentId, label, avatar: null, active: true },
    targetType: "item",
    targetId: ITEM_ID,
    target: { id: ITEM_ID, title: "Fix the parser", archived: false },
    relationship: "",
    setBy: "human",
    createdAt: "2026-11-06T10:00:00Z",
    ...fields,
  };
}

const participants = {
  items: [
    {
      id: "local-user",
      label: "You",
      kind: "human",
      active: true,
      avatar: null,
    },
    {
      id: BUILDER,
      label: "Builder",
      kind: "agent",
      active: true,
      avatar: null,
    },
    {
      id: REVIEWER,
      label: "Reviewer",
      kind: "agent",
      active: true,
      avatar: null,
    },
    {
      id: RETIRED,
      label: "Old chat",
      kind: "agent",
      active: false,
      avatar: null,
    },
    { id: SPARE, label: "Spare", kind: "agent", active: true, avatar: null },
  ],
  self: { id: "local-user", label: "You", kind: "human" },
};

type Call = { url: string; method: string; body: unknown };

/** Answers each GET from `reads` by the first key its URL contains, and records every request. */
function serve(reads: [string, unknown][]) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    if (method === "GET") {
      const found = reads.find(([key]) => url.includes(key));
      return new Response(
        JSON.stringify(found ? found[1] : { items: [], total: 0 }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ id: "written" }), { status: 200 });
  });
  return calls;
}
const writes = (calls: Call[]) => calls.filter((c) => c.method !== "GET");

function show(ui: ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {ui}
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("agent-ties-test-" + crypto.randomUUID());
});

it("lists a record's agents with who set each tie, and folds away retired agents", async () => {
  serve([
    [
      "/agent-links?targetType=item&targetId=" + ITEM_ID,
      {
        items: [
          tie(BUILDER, "Builder", {
            relationship: "Implementer",
            setBy: "agent",
          }),
          tie(REVIEWER, "Reviewer", { relationship: "Reviewer" }),
          tie(RETIRED, "Old chat", {
            agent: {
              id: RETIRED,
              label: "Old chat",
              avatar: null,
              active: false,
            },
          }),
        ],
        total: 3,
      },
    ],
    ["/discussions/participants", participants],
  ]);
  show(<AgentTies targetType="item" targetId={ITEM_ID} />);
  const panel = await screen.findByRole("region", { name: "Agents" });
  expect(
    await within(panel).findByRole("link", { name: "Builder" }),
  ).toHaveAttribute("href", "#agents/" + BUILDER);
  expect(within(panel).getByText("3")).toBeInTheDocument();
  expect(within(panel).getByText("By the agent")).toBeInTheDocument();
  // The reviewer's tie and the retired agent's, which the person set.
  expect(within(panel).getAllByText("By you")).toHaveLength(2);
  expect(
    within(panel).getByRole("textbox", { name: "Role for Builder" }),
  ).toHaveValue("Implementer");
  expect(within(panel).getByText("Retired agents (1)")).toBeInTheDocument();
  // The picker offers live agents not already tied: neither the person nor a revoked identity.
  const choose = within(panel).getByRole("combobox", { name: "Choose agent" });
  await waitFor(() =>
    expect(
      within(choose)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Select agent to tie…", "Spare"]),
  );
});

it("ties an agent with a role, re-roles it on blur and unlinks it", async () => {
  const calls = serve([
    [
      "/agent-links?targetType=item",
      {
        items: [tie(BUILDER, "Builder", { relationship: "Implementer" })],
        total: 1,
      },
    ],
    ["/discussions/participants", participants],
  ]);
  show(<AgentTies targetType="item" targetId={ITEM_ID} />);
  const role = await screen.findByRole("textbox", { name: "Role for Builder" });
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Choose agent" }),
    REVIEWER,
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Agent's role" }),
    " Reviewer ",
  );
  await userEvent.click(screen.getByRole("button", { name: "Tie agent" }));
  await waitFor(() =>
    expect(writes(calls).at(-1)).toEqual({
      url: "/api/v1/agent-links",
      method: "POST",
      body: {
        agentId: REVIEWER,
        targetType: "item",
        targetId: ITEM_ID,
        relationship: "Reviewer",
      },
    }),
  );
  await userEvent.clear(role);
  await userEvent.type(role, "Lead");
  await userEvent.tab();
  await waitFor(() =>
    expect(writes(calls).at(-1)).toEqual({
      url: "/api/v1/agent-links",
      method: "POST",
      body: {
        agentId: BUILDER,
        targetType: "item",
        targetId: ITEM_ID,
        relationship: "Lead",
      },
    }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Unlink Builder" }));
  await waitFor(() =>
    expect(writes(calls).at(-1)).toEqual({
      url: "/api/v1/agent-links/unlink",
      method: "POST",
      body: { agentId: BUILDER, targetType: "item", targetId: ITEM_ID },
    }),
  );
});

it("never offers an agent a tie to itself, and an archived record takes no new tie", async () => {
  serve([["/discussions/participants", participants]]);
  show(
    <AgentTies
      targetType="agent"
      targetId={SPARE}
      heading="Agents tied to this agent"
    />,
  );
  const choose = await screen.findByRole("combobox", { name: "Choose agent" });
  await waitFor(() =>
    expect(
      within(choose)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Select agent to tie…", "Builder", "Reviewer"]),
  );
  vi.restoreAllMocks();
  serve([]);
  show(
    <AgentTies
      targetType="repository"
      targetId={ITEM_ID}
      canTie={false}
      heading="Archived"
    />,
  );
  const archived = await screen.findByRole("region", { name: "Archived" });
  expect(
    within(archived).queryByRole("button", { name: "Tie agent" }),
  ).not.toBeInTheDocument();
});

it("folds a page's ties until they are opened, with their count in view", async () => {
  serve([
    [
      "/agent-links?targetType=chat",
      { items: [tie(BUILDER, "Builder", { targetType: "chat" })], total: 1 },
    ],
    ["/discussions/participants", participants],
  ]);
  show(
    <AgentTies
      targetType="chat"
      targetId={ITEM_ID}
      layout="fold"
      note="A tie doesn't bring an agent your messages: they reach only the agents bound to this chat."
    />,
  );
  const summary = await screen.findByText("Agents");
  await waitFor(() =>
    expect(summary.parentElement).toHaveTextContent("Agents 1"),
  );
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  await userEvent.click(summary);
  expect(
    await screen.findByRole("link", { name: "Builder" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/they reach only the agents bound to this chat/),
  ).toBeInTheDocument();
});

it("shows a row's tied agents as chips, and its fold ties more", async () => {
  serve([["/discussions/participants", participants]]);
  show(
    <AgentTieFold
      targetType="question"
      targetId={ITEM_ID}
      title="Which build?"
      ties={[
        tie(BUILDER, "Builder", {
          targetType: "question",
          relationship: "Answers",
        }),
      ]}
    />,
  );
  const chips = screen.getByLabelText("Agents tied to Which build?");
  expect(
    within(chips).getByRole("link", { name: "Builder (Answers)" }),
  ).toHaveAttribute("href", "#agents/" + BUILDER);
  await userEvent.click(screen.getByText("Agents"));
  expect(
    await screen.findByRole("button", { name: "Tie agent" }),
  ).toBeInTheDocument();
});

it("lists everything an agent is tied to by type, and ties it to a record found by search", async () => {
  const calls = serve([
    [
      "/agent-links?agentId=" + BUILDER,
      {
        items: [
          tie(BUILDER, "Builder", { relationship: "Implementer" }),
          tie(BUILDER, "Builder", {
            targetType: "step",
            targetId: "step-1",
            target: {
              id: "step-1",
              title: "Cut the branch",
              archived: true,
              roadmapId: "road-1",
              roadmapTitle: "Release",
            },
            setBy: "agent",
          }),
          tie(BUILDER, "Builder", {
            targetType: "ticket",
            targetId: "ticket-1",
            target: {
              id: "ticket-1",
              title: "Flaky import",
              archived: false,
              number: 7,
              status: "open",
            },
          }),
        ],
        total: 3,
      },
    ],
    [
      "/search?",
      {
        items: [{ id: "q-1", type: "question", title: "Which build?" }],
        total: 1,
      },
    ],
  ]);
  show(<AgentTiedTo agentId={BUILDER} />);
  const section = await screen.findByRole("region", { name: "Tied to" });
  const steps = await within(section).findByRole("region", {
    name: "Tied steps",
  });
  expect(
    within(steps).getByRole("link", { name: "Cut the branch" }),
  ).toHaveAttribute("href", "#roadmaps/road-1/step-1");
  expect(within(steps).getByRole("link", { name: "Release" })).toHaveAttribute(
    "href",
    "#roadmaps/road-1",
  );
  expect(within(steps).getByText("(retired)")).toBeInTheDocument();
  expect(within(steps).getByText("By the agent")).toBeInTheDocument();
  expect(within(section).getByText("T-7")).toBeInTheDocument();
  expect(
    within(section)
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent),
  ).toEqual(["Work items 1", "Steps 1", "Tickets 1"]);
  // Tie it to a question, found through search narrowed to questions.
  await userEvent.selectOptions(
    within(section).getByRole("combobox", { name: "Type of record to tie" }),
    "question",
  );
  await userEvent.type(
    within(section).getByRole("textbox", { name: "Role on the record" }),
    "Answers",
  );
  await userEvent.click(
    within(section).getByRole("button", { name: "Tie to a record" }),
  );
  await userEvent.type(
    within(section).getByRole("textbox", { name: "Question search" }),
    "build",
  );
  await userEvent.click(
    await within(section).findByRole("button", { name: "Which build?" }),
  );
  await waitFor(() =>
    expect(writes(calls).at(-1)).toEqual({
      url: "/api/v1/agent-links",
      method: "POST",
      body: {
        agentId: BUILDER,
        targetType: "question",
        targetId: "q-1",
        relationship: "Answers",
      },
    }),
  );
  expect(
    calls.some((c) =>
      c.url.includes("/search?archived=false&limit=20&type=question&q=build"),
    ),
  ).toBe(true);
});

function TiedCount({ ids }: { ids: string[] }) {
  const ties = useTiesOn("question", ids);
  return <p>{ids.filter((id) => ties.has(id)).length} tied</p>;
}

it("reads the ties on more than 200 records 200 at a time", async () => {
  const ids = Array.from({ length: 201 }, (_, n) => `q-${n}`);
  const on = (targetId: string) => ({
    items: [tie(BUILDER, "Builder", { targetType: "question", targetId })],
    total: 1,
  });
  const calls = serve([
    ["targetId=q-200", on("q-200")],
    ["targetId=q-0&", on("q-0")],
  ]);
  show(<TiedCount ids={ids} />);
  expect(await screen.findByText("2 tied")).toBeInTheDocument();
  const reads = calls.filter((c) => c.url.includes("/agent-links?"));
  expect(reads.map((c) => c.url.split("&targetId=").length - 1)).toEqual([
    200, 1,
  ]);
});

it("knows where each type of record opens and where its picker searches", () => {
  const target = { id: "x", title: "X", archived: false, roadmapId: "road" };
  const opens: Record<TieType, string> = {
    item: "#work/x",
    roadmap: "#roadmaps/x",
    step: "#roadmaps/road/x",
    repository: "#repositories/x",
    person: "#people/x",
    resource: "#resources/x",
    discussion: "#discussions/x",
    chat: "#chats/x",
    proposal: "#review/x",
    ticket: "#tickets/x",
    agent: "#agents/x",
    question: "#roadmaps/road",
    decision: "#roadmaps/road",
  };
  for (const type of TIE_TYPES)
    expect(tieHref(type, "x", target)).toBe(opens[type]);
  expect(pickerPath("step", "cut it")).toBe(
    "/search?archived=false&limit=20&type=step&q=cut%20it",
  );
  expect(pickerPath("repository", "")).toBe(
    "/repositories?archived=false&limit=20&q=",
  );
  const agents = pickerFilter("agent", BUILDER)!;
  expect(
    participants.items.filter((p) => agents(p, "")).map((p) => p.label),
  ).toEqual(["Reviewer", "Spare"]);
  expect(
    participants.items.filter((p) => agents(p, "rev")).map((p) => p.label),
  ).toEqual(["Reviewer"]);
  expect(pickerFilter("item", BUILDER)).toBeUndefined();
});
