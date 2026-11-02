import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RoadmapDetail } from "./Roadmaps";
import type { RoadmapTie, RoadmapWorker } from "./agentTypes";
import type { AgentChat, Roadmap, Step } from "./types";

const ROADMAP = "22222222-2222-4222-8222-222222222222";
function step(id: string, title: string, parentId: string | null): Step {
  return {
    id,
    title,
    description: "",
    parentId,
    position: 0,
    status: "open",
    revision: 1,
    retired: false,
  };
}
const road: Roadmap = {
  id: ROADMAP,
  revision: 1,
  title: "Plan",
  overview: "",
  steps: [
    step("aaaaaaaa-0000-4000-8000-000000000001", "Parent", null),
    step(
      "aaaaaaaa-0000-4000-8000-000000000002",
      "Child",
      "aaaaaaaa-0000-4000-8000-000000000001",
    ),
    step("aaaaaaaa-0000-4000-8000-000000000003", "Solo", null),
  ],
  versions: [],
  notes: [],
  attachments: [],
  history: [],
};
let served = road;
let workers: RoadmapWorker[] = [];
const AGENTS = `/roadmaps/${ROADMAP}/agents`;
function respond(input: RequestInfo | URL) {
  const url = String(input);
  if (url.endsWith(AGENTS)) return Response.json({ items: workers });
  return Response.json(
    url.endsWith("/roadmaps/" + ROADMAP) ? served : { items: [], total: 0 },
  );
}
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("roadmap-steps-" + crypto.randomUUID());
  served = road;
  workers = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    respond(input),
  );
});
const PARENT = "aaaaaaaa-0000-4000-8000-000000000001";
const CHILD = "aaaaaaaa-0000-4000-8000-000000000002";
const SOLO = "aaaaaaaa-0000-4000-8000-000000000003";
const view = (step?: string) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RoadmapDetail id={ROADMAP} step={step} />
    </QueryClientProvider>,
  );

it("collapses and expands a step's sub-steps, only for steps that have them", async () => {
  view();
  expect(await screen.findByText("Child")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Collapse Solo" })).toBeNull();
  const toggle = screen.getByRole("button", { name: "Collapse Parent" });
  expect(toggle).toHaveAttribute("aria-expanded", "true");

  await userEvent.click(toggle);
  expect(screen.queryByText("Child")).toBeNull();
  expect(screen.getByText(/1 sub-step hidden/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Expand Parent" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );

  await userEvent.click(screen.getByRole("button", { name: "Expand Parent" }));
  expect(screen.getByText("Child")).toBeVisible();
});

it("collapses and expands every parent at once", async () => {
  view();
  await screen.findByText("Child");
  const expand = screen.getByRole("button", { name: "Expand all" });
  expect(expand).toBeDisabled();
  expect(screen.getByRole("button", { name: "Collapse done" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  expect(screen.queryByText("Child")).toBeNull();
  expect(screen.getByRole("button", { name: "Collapse all" })).toBeDisabled();
  await userEvent.click(expand);
  expect(screen.getByText("Child")).toBeVisible();
});

it("collapses the sections whose steps are all done, keeping the rest as they are", async () => {
  const SHIPPED = "aaaaaaaa-0000-4000-8000-000000000004";
  served = {
    ...road,
    steps: [
      ...road.steps,
      { ...step(SHIPPED, "Shipped", null), status: "done" },
      {
        ...step("aaaaaaaa-0000-4000-8000-000000000005", "Released", SHIPPED),
        status: "done",
      },
    ],
  };
  const first = view();
  expect(await screen.findByText("Released")).toBeVisible();
  const collapseDone = screen.getByRole("button", { name: "Collapse done" });
  await userEvent.click(collapseDone);
  expect(screen.queryByText("Released")).toBeNull();
  expect(screen.getByText("Child")).toBeVisible();
  expect(collapseDone).toBeDisabled();

  await userEvent.click(screen.getByRole("button", { name: "Expand Shipped" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Collapse Parent" }),
  );
  await userEvent.click(collapseDone);
  expect(screen.queryByText("Released")).toBeNull();
  expect(screen.queryByText("Child")).toBeNull();

  first.unmount();
  view();
  expect(
    await screen.findByRole("button", { name: "Expand Shipped" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Expand Parent" })).toBeVisible();
});

it("applies prominent state classes to step containers, status dots, and status selects", async () => {
  view();
  await screen.findByText("Child");
  const select = screen.getByRole("combobox", { name: "Progress for Solo" });
  expect(select).toHaveClass("status-select", "open");
  expect(select).toHaveAttribute("data-status", "open");
  const soloStep = screen.getByText("Solo").closest(".step");
  expect(soloStep).toHaveClass("step", "status-open");
  expect(soloStep?.querySelector(".status-dot.open")).not.toBeNull();
});

it("shows the selected step's details beside the outline", async () => {
  view(CHILD);
  const details = await screen.findByRole("region", { name: "Step details" });
  expect(within(details).getByRole("heading", { name: "Child" })).toBeVisible();
  expect(within(details).getByRole("link", { name: "Parent" })).toHaveAttribute(
    "href",
    `#roadmaps/${ROADMAP}/${PARENT}`,
  );
  const outline = screen.getByRole("region", { name: "Plan outline" });
  expect(within(outline).getByRole("link", { name: "Child" })).toHaveAttribute(
    "aria-current",
    "true",
  );
  expect(
    within(outline).getByRole("link", { name: "Solo" }),
  ).not.toHaveAttribute("aria-current");
});

it("falls back to the roadmap overview for a step that is not in the plan", async () => {
  view("bbbbbbbb-0000-4000-8000-000000000009");
  const details = await screen.findByRole("region", {
    name: "Roadmap details",
  });
  expect(within(details).getByText(/not in the current plan/)).toBeVisible();
  expect(
    within(details).getByRole("heading", { name: "Overview" }),
  ).toBeVisible();
});

it("remembers collapsed steps when the roadmap is opened again", async () => {
  const first = view();
  await userEvent.click(
    await screen.findByRole("button", { name: "Collapse Parent" }),
  );
  first.unmount();
  view();
  expect(
    await screen.findByRole("button", { name: "Expand Parent" }),
  ).toBeVisible();
  expect(screen.queryByText("Child")).toBeNull();
});

it("expands the collapsed parents of a step opened by link", async () => {
  const first = view();
  await userEvent.click(
    await screen.findByRole("button", { name: "Collapse Parent" }),
  );
  first.unmount();
  view(CHILD);
  const outline = await screen.findByRole("region", { name: "Plan outline" });
  expect(within(outline).getByRole("link", { name: "Child" })).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Collapse Parent" }),
  ).toHaveAttribute("aria-expanded", "true");
});

function chat(id: string, title: string, stepIds: string[]): AgentChat {
  return {
    id,
    revision: 3,
    ownerType: "roadmap",
    ownerId: ROADMAP,
    chatId: "conv-" + id.slice(-1),
    title,
    createdDate: "2026-10-31",
    provider: "",
    model: "",
    repos: [],
    stepIds,
  };
}
const API_CHAT = {
  ...chat("cccccccc-0000-4000-8000-000000000001", "Build the API", [PARENT]),
  provider: "Anthropic",
  model: "claude-opus-5-5",
  repos: ["acme/api", "acme/web"],
};
const DOCS_CHAT = {
  ...chat("cccccccc-0000-4000-8000-000000000002", "Write the docs", []),
  provider: "OpenAI",
  model: "gpt-5.5",
};
function recordWrites() {
  const writes: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const method = init?.method ?? "GET";
    if (method !== "GET")
      writes.push({
        url: String(input),
        method,
        body: JSON.parse(String(init?.body)),
      });
    return respond(input);
  });
  return writes;
}

it("shows the roadmap's chats with their agents, repositories and steps", async () => {
  served = { ...road, chats: [API_CHAT, DOCS_CHAT] };
  view();
  const details = await screen.findByRole("region", {
    name: "Roadmap details",
  });
  const apiChat = within(details)
    .getByText("Build the API")
    .closest(".chat-line") as HTMLElement;
  expect(
    within(apiChat).getByText("Anthropic · claude-opus-5-5"),
  ).toBeVisible();
  expect(within(apiChat).getByText("acme/web")).toBeVisible();
  expect(within(apiChat).getByRole("link", { name: "Parent" })).toHaveAttribute(
    "href",
    `#roadmaps/${ROADMAP}/${PARENT}`,
  );
});

it("assigns, unassigns, and adds agent chats from a step", async () => {
  served = { ...road, chats: [API_CHAT, DOCS_CHAT] };
  const writes = recordWrites();
  const child = view(CHILD);
  let details = await screen.findByRole("region", { name: "Step details" });
  expect(
    within(details).getByText("No agent chat is working on this step."),
  ).toBeVisible();
  expect(
    within(details).getByText(/is working on the parent/),
  ).toHaveTextContent(
    "Build the API (Anthropic · claude-opus-5-5) is working on the parent step Parent.",
  );
  await userEvent.selectOptions(
    within(details).getByRole("combobox", { name: "Agent chat to assign" }),
    DOCS_CHAT.id,
  );
  await userEvent.click(
    within(details).getByRole("button", { name: "Assign chat" }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: "/api/v1/chats/" + DOCS_CHAT.id,
      method: "PATCH",
      body: { revision: 3, stepIds: [CHILD] },
    }),
  );
  child.unmount();

  view(PARENT);
  details = await screen.findByRole("region", { name: "Step details" });
  await userEvent.click(
    within(details).getByRole("button", { name: "Unassign Build the API" }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: "/api/v1/chats/" + API_CHAT.id,
      method: "PATCH",
      body: { revision: 3, stepIds: [] },
    }),
  );
  await userEvent.type(
    within(details).getByRole("textbox", { name: "Agent chat title" }),
    "Review the API",
  );
  await userEvent.type(
    within(details).getByRole("textbox", { name: "Agent chat ID" }),
    "a950ec9c",
  );
  await userEvent.type(
    within(details).getByRole("combobox", { name: "Agent chat model" }),
    "claude-opus-5-5",
  );
  await userEvent.type(
    within(details).getByRole("textbox", { name: "Agent chat repositories" }),
    "acme/api",
  );
  await userEvent.click(
    within(details).getByRole("button", { name: "Add chat to step" }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: "/api/v1/chats",
      method: "POST",
      body: {
        ownerType: "roadmap",
        ownerId: ROADMAP,
        title: "Review the API",
        chatId: "a950ec9c",
        provider: "",
        model: "claude-opus-5-5",
        repos: ["acme/api"],
        stepIds: [PARENT],
      },
    }),
  );
});

it("edits a chat's provider, model, and repositories in place", async () => {
  served = { ...road, chats: [API_CHAT] };
  const writes = recordWrites();
  view();
  const details = await screen.findByRole("region", {
    name: "Roadmap details",
  });
  await userEvent.click(
    await within(details).findByRole("button", {
      name: "Edit chat Build the API",
    }),
  );
  const model = within(details).getByRole("combobox", {
    name: "Edited chat model",
  });
  expect(model).toHaveValue("claude-opus-5-5");
  await userEvent.clear(model);
  await userEvent.type(model, "claude-sonnet-5-5");
  await userEvent.clear(
    within(details).getByRole("textbox", { name: "Edited chat repositories" }),
  );
  await userEvent.click(
    within(details).getByRole("button", { name: "Save chat" }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: "/api/v1/chats/" + API_CHAT.id,
      method: "PATCH",
      body: {
        revision: 3,
        title: "Build the API",
        chatId: API_CHAT.chatId,
        provider: "Anthropic",
        model: "claude-sonnet-5-5",
        repos: [],
        createdDate: "2026-10-31",
      },
    }),
  );
});

function worker(
  kind: RoadmapWorker["kind"],
  id: string,
  label: string,
  ties: Partial<RoadmapTie>[],
  extra: Partial<RoadmapWorker> = {},
): RoadmapWorker {
  const full = ties.map((t) => ({
    stepId: null,
    via: "chat" as const,
    item: null,
    chat: null,
    role: null,
    lastAt: "2026-11-02T07:00:00+00:00",
    stale: false,
    ...t,
  }));
  return {
    kind,
    id,
    label,
    avatar: null,
    provider: "",
    model: "",
    ties: full,
    lastAt: full[0]?.lastAt ?? null,
    stale: full.every((t) => t.stale),
    ...extra,
  };
}
const BUILDER = worker(
  "agent",
  "dddddddd-0000-4000-8000-000000000001",
  "Builder",
  [
    {
      stepId: CHILD,
      via: "participation",
      item: { id: "item-1", title: "Build the API" },
    },
    {
      via: "participation",
      item: { id: "item-2", title: "Chores" },
      lastAt: "2026-11-02T03:00:00+00:00",
      stale: true,
    },
  ],
  { avatar: "teal-visor-antenna" },
);
const REVIEWER = worker(
  "agent",
  "dddddddd-0000-4000-8000-000000000002",
  "Reviewer",
  [
    {
      stepId: PARENT,
      via: "role",
      role: { id: "role-1", label: "Review" },
      lastAt: "2026-11-02T06:00:00+00:00",
    },
  ],
);
const DOCS = worker(
  "chat",
  DOCS_CHAT.id,
  "Write the docs",
  [{ stepId: SOLO, chat: { id: DOCS_CHAT.id, title: "Write the docs" } }],
  { provider: "OpenAI", model: "gpt-5.5" },
);
const SLEEPY = worker(
  "agent",
  "dddddddd-0000-4000-8000-000000000003",
  "Sleepy",
  [
    {
      stepId: SOLO,
      via: "participation",
      item: { id: "item-3", title: "Old report" },
      lastAt: "2026-11-01T01:00:00+00:00",
      stale: true,
    },
  ],
);
const chipsIn = (element: Element | null) =>
  Array.from(element?.querySelectorAll("a.agent-chip") ?? []).map((a) =>
    a.getAttribute("aria-label"),
  );
async function outlineRows() {
  const outline = await screen.findByRole("region", { name: "Plan outline" });
  return {
    outline,
    row: (title: string) =>
      within(outline)
        .getByRole("link", { name: title })
        .closest(".step-row") as HTMLElement,
  };
}

it("shows which agents work on each step, and on the sub-steps a collapsed step hides", async () => {
  workers = [SLEEPY, REVIEWER, BUILDER, DOCS];
  view();
  const { outline, row } = await outlineRows();
  const summary = (await within(outline).findByText("Agents working here"))
    .parentElement;
  // Fresh work first, then the most recent.
  expect(chipsIn(summary)).toEqual([
    "Builder",
    "Write the docs",
    "Reviewer",
    "Sleepy, stale report",
  ]);
  const everywhere = within(summary as HTMLElement).getByRole("link", {
    name: "Builder",
  });
  expect(everywhere).toHaveAttribute(
    "title",
    expect.stringMatching(
      /^Builder\nReporting work on “Build the API” on Child\nStale report on “Chores”, last reported .+$/,
    ),
  );
  expect(
    within(summary as HTMLElement).getByRole("link", {
      name: "Sleepy, stale report",
    }),
  ).toHaveAttribute(
    "title",
    expect.stringMatching(
      /^Sleepy\nStale report on “Old report” on Solo, last reported .+$/,
    ),
  );

  // An open step shows only who works on it; its sub-steps show their own.
  expect(chipsIn(row("Parent"))).toEqual(["Reviewer"]);
  expect(chipsIn(row("Child"))).toEqual(["Builder"]);
  const builder = within(row("Child")).getByRole("link", { name: "Builder" });
  expect(builder).toHaveAttribute("href", "#agents/" + BUILDER.id);
  expect(builder).toHaveAttribute(
    "title",
    "Builder\nReporting work on “Build the API”",
  );
  expect(chipsIn(row("Solo"))).toEqual([
    "Write the docs",
    "Sleepy, stale report",
  ]);
  const docs = within(row("Solo")).getByRole("link", {
    name: "Write the docs",
  });
  expect(docs).toHaveAttribute("href", "#chats/" + DOCS_CHAT.id);
  expect(docs).toHaveAttribute(
    "title",
    "Write the docs · OpenAI · gpt-5.5\nTracked chat",
  );
  expect(
    within(row("Solo")).getByRole("link", { name: "Sleepy, stale report" }),
  ).toHaveClass("agent-chip", "stale");

  await userEvent.click(
    within(outline).getByRole("button", { name: "Collapse Parent" }),
  );
  expect(chipsIn(row("Parent"))).toEqual(["Builder", "Reviewer"]);
  expect(
    within(row("Parent")).getByRole("link", { name: "Builder" }),
  ).toHaveAttribute(
    "title",
    "Builder\nReporting work on “Build the API” on Child",
  );
});

it("shows three agents on a step until asked for the rest", async () => {
  workers = ["One", "Two", "Three", "Four", "Five"].map((label, i) =>
    worker("chat", `eeeeeeee-0000-4000-8000-00000000000${i}`, label, [
      { stepId: SOLO, chat: { id: "chat-" + i, title: label } },
    ]),
  );
  view();
  const { row } = await outlineRows();
  await waitFor(() =>
    expect(chipsIn(row("Solo"))).toEqual(["One", "Two", "Three"]),
  );
  await userEvent.click(
    within(row("Solo")).getByRole("button", { name: "Show 2 more" }),
  );
  expect(chipsIn(row("Solo"))).toEqual(["One", "Two", "Three", "Four", "Five"]);
});

it("says when it cannot tell who works on the roadmap, and keeps the plan", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    String(input).endsWith(AGENTS)
      ? Response.json(
          { error: { code: "database_busy", message: "Database is busy" } },
          { status: 503 },
        )
      : respond(input),
  );
  view();
  const { outline } = await outlineRows();
  expect(await within(outline).findByRole("alert")).toHaveTextContent(
    "Database is busy",
  );
  expect(within(outline).getByRole("link", { name: "Child" })).toBeVisible();
});
