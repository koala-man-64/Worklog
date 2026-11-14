import { beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RoadmapDetail } from "./Roadmaps";
import type {
  AgentChat,
  Roadmap,
  RoadmapTie,
  RoadmapWorker,
  RosterAgent,
  Step,
} from "./apiTypes";
import { aChat, aRoadmap, aStep } from "./fixtures";

const ROADMAP = "22222222-2222-4222-8222-222222222222";
function step(id: string, title: string, parentId: string | null): Step {
  return aStep({ id, title, parentId });
}
const road: Roadmap = aRoadmap({
  id: ROADMAP,
  title: "Plan",
  steps: [
    step("aaaaaaaa-0000-4000-8000-000000000001", "Parent", null),
    step(
      "aaaaaaaa-0000-4000-8000-000000000002",
      "Child",
      "aaaaaaaa-0000-4000-8000-000000000001",
    ),
    step("aaaaaaaa-0000-4000-8000-000000000003", "Solo", null),
  ],
});
let served = road;
let workers: RoadmapWorker[] = [];
let roster: RosterAgent[] = [];
let rosterTotal: number | null = null;
const AGENTS = `/roadmaps/${ROADMAP}/agents`;
function respond(input: RequestInfo | URL) {
  const url = String(input);
  if (url.endsWith(AGENTS))
    return Response.json({
      items: workers,
      roster: { items: roster, total: rosterTotal ?? roster.length },
    });
  return Response.json(
    url.endsWith("/roadmaps/" + ROADMAP) ? served : { items: [], total: 0 },
  );
}
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("roadmap-steps-" + crypto.randomUUID());
  served = road;
  workers = [];
  roster = [];
  rosterTotal = null;
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
  view("outline");
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
  view("outline");
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
  const first = view("outline");
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
  view("outline");
  expect(
    await screen.findByRole("button", { name: "Expand Shipped" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Expand Parent" })).toBeVisible();
});

it("applies prominent state classes to step containers, status dots, and status selects", async () => {
  view("outline");
  await screen.findByText("Child");
  const select = screen.getByRole("combobox", { name: "Progress for Solo" });
  expect(select).toHaveClass("status-select", "open");
  expect(select).toHaveAttribute("data-status", "open");
  // Only work items wait: a step keeps its four states.
  expect(
    within(select)
      .getAllByRole("option")
      .map((option) => option.getAttribute("value")),
  ).toEqual(["open", "doing", "blocked", "done"]);
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

it("leads a step's details with its outcome, above the description", async () => {
  served = aRoadmap({
    ...road,
    steps: road.steps.map((s) =>
      s.id === CHILD
        ? { ...s, outcome: "Every fixture parses", description: "The how" }
        : s,
    ),
  });
  view(CHILD);
  const details = await screen.findByRole("region", { name: "Step details" });
  const outcome = within(details).getByRole("region", { name: "Outcome" });
  expect(within(outcome).getByText("Every fixture parses")).toBeVisible();
  const how = within(details).getByText("The how");
  // The outcome comes before the description.
  expect(
    outcome.compareDocumentPosition(how) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

it("asks for an outcome on a step that has none", async () => {
  view(SOLO);
  const details = await screen.findByRole("region", { name: "Step details" });
  const outcome = within(details).getByRole("region", { name: "Outcome" });
  expect(outcome).toHaveClass("missing");
  expect(within(outcome).getByText(/No outcome yet/)).toBeVisible();
});

it("says beside the outline when a step is not in the plan", async () => {
  view("bbbbbbbb-0000-4000-8000-000000000009");
  const details = await screen.findByRole("region", { name: "Step details" });
  expect(within(details).getByText(/not in the current plan/)).toBeVisible();
  expect(
    within(details).getByText(
      "Choose a step to see its work, chats and notes.",
    ),
  ).toBeVisible();
  expect(
    within(screen.getByRole("region", { name: "Plan outline" })).getByRole(
      "link",
      { name: "Child" },
    ),
  ).not.toHaveAttribute("aria-current");
});

it("remembers collapsed steps when the roadmap is opened again", async () => {
  const first = view("outline");
  await userEvent.click(
    await screen.findByRole("button", { name: "Collapse Parent" }),
  );
  first.unmount();
  view("outline");
  expect(
    await screen.findByRole("button", { name: "Expand Parent" }),
  ).toBeVisible();
  expect(screen.queryByText("Child")).toBeNull();
});

it("expands the collapsed parents of a step opened by link", async () => {
  const first = view("outline");
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

it("shows retired steps at their places in the outline only when asked", async () => {
  view("outline");
  await screen.findByText("Child");
  expect(
    screen.queryByRole("checkbox", { name: "Show retired steps" }),
  ).toBeNull();
  cleanup();
  served = {
    ...road,
    steps: [
      ...road.steps,
      {
        ...step("aaaaaaaa-0000-4000-8000-000000000009", "Old idea", PARENT),
        position: 1,
        status: "done",
        retired: true,
      },
      {
        ...step("aaaaaaaa-0000-4000-8000-00000000000a", "Older idea", SOLO),
        retired: true,
      },
    ],
  };
  view("outline");
  const outline = await screen.findByRole("region", { name: "Plan outline" });
  await within(outline).findByText("Child");
  expect(within(outline).queryByText("Old idea")).toBeNull();
  await userEvent.click(
    within(outline).getByRole("checkbox", { name: "Show retired steps" }),
  );
  const old = within(outline).getByText("Old idea").closest(".step");
  expect(old).toHaveClass("retired");
  expect(within(old as HTMLElement).getByText("Retired · Done")).toBeVisible();
  // Out of the plan, it has nothing to open or set.
  expect(within(old as HTMLElement).queryByRole("link")).toBeNull();
  expect(within(old as HTMLElement).queryByRole("combobox")).toBeNull();
  // It sits under its old parent.
  const parent = within(outline)
    .getByRole("link", { name: "Parent" })
    .closest(".step");
  expect(parent).toContainElement(old as HTMLElement);
  // A step with only retired steps under it is still a leaf of the plan, its progress set by hand,
  // and folds with the rest.
  expect(
    within(outline).getByRole("combobox", { name: "Progress for Solo" }),
  ).toBeEnabled();
  await userEvent.click(
    within(outline).getByRole("button", { name: "Collapse all" }),
  );
  expect(
    within(outline).getByRole("button", { name: "Expand Solo" }),
  ).toBeVisible();
  expect(within(outline).queryByText("Older idea")).toBeNull();
  await userEvent.click(
    within(outline).getByRole("button", { name: "Expand all" }),
  );
  expect(within(outline).getByText("Older idea")).toBeVisible();
});

function chat(id: string, title: string, stepIds: string[]): AgentChat {
  return aChat({
    id,
    revision: 3,
    ownerType: "roadmap",
    ownerId: ROADMAP,
    chatId: "conv-" + id.slice(-1),
    title,
    createdDate: "2026-10-31",
    stepIds,
  });
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
  view("material");
  const details = await screen.findByRole("region", { name: "Material" });
  const apiChat = within(details)
    .getByText("Build the API")
    .closest(".chat-line") as HTMLElement;
  expect(
    within(apiChat).getByText("Anthropic · claude-opus-5-5"),
  ).toBeVisible();
  // A label from before schema v24 that names no repository linked here.
  expect(within(apiChat).getByText("acme/web · Not linked")).toBeVisible();
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
        stepIds: [PARENT],
      },
    }),
  );
});

it("edits a chat's provider and model in place, leaving its labels alone", async () => {
  served = { ...road, chats: [API_CHAT] };
  const writes = recordWrites();
  view("material");
  const details = await screen.findByRole("region", { name: "Material" });
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
  // Labels from before schema v24 are read only: the form has no field for them.
  expect(
    within(details).queryByRole("textbox", {
      name: "Edited chat repositories",
    }),
  ).toBeNull();
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
  view("outline");
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
  view("outline");
  const { row } = await outlineRows();
  await waitFor(() =>
    expect(chipsIn(row("Solo"))).toEqual(["One", "Two", "Three"]),
  );
  await userEvent.click(
    within(row("Solo")).getByRole("button", { name: "Show 2 more" }),
  );
  expect(chipsIn(row("Solo"))).toEqual(["One", "Two", "Three", "Four", "Five"]);
});

function onRoster(
  id: string,
  label: string,
  steps: Partial<RosterAgent["steps"][number]>[],
  extra: Partial<RosterAgent> = {},
): RosterAgent {
  const full = steps.map((s) => ({
    stepId: SOLO,
    via: ["participation" as const],
    current: false,
    lastAt: "2026-11-02T04:00:00+00:00",
    ...s,
  }));
  return {
    id,
    label,
    avatar: null,
    active: true,
    via: [...new Set(full.flatMap((s) => s.via))],
    current: full.some((s) => s.current),
    lastAt: full[0]?.lastAt ?? "2026-11-01T00:00:00+00:00",
    steps: full,
    ...extra,
  };
}
const HAND = onRoster("ffffffff-0000-4000-8000-000000000001", "Builder", [
  { stepId: CHILD, current: true, lastAt: "2026-11-03T09:00:00+00:00" },
]);
const LEAD = onRoster("ffffffff-0000-4000-8000-000000000002", "Reviewer", [
  { stepId: PARENT, via: ["role"], current: true },
]);
const WRITER = onRoster("ffffffff-0000-4000-8000-000000000003", "Writer", [
  { via: ["message"], lastAt: "2026-11-02T08:00:00+00:00" },
]);
// Its tracked chat worked on Child before; it reports work on Solo now.
const FIXER = onRoster("ffffffff-0000-4000-8000-000000000004", "Fixer", [
  { stepId: SOLO, current: true, lastAt: "2026-11-03T08:00:00+00:00" },
  { stepId: CHILD, via: ["chat"], lastAt: "2026-11-02T07:00:00+00:00" },
]);
// Its only work had no step: the roadmap lists it, no step does.
const OLD_HAND = onRoster(
  "ffffffff-0000-4000-8000-000000000005",
  "Old hand",
  [],
  { active: false, via: ["chat"], lastAt: "2026-10-30T00:00:00+00:00" },
);
// It wrote about Parent lately and reports work on Child, under Parent, now.
const PAIR = onRoster("ffffffff-0000-4000-8000-000000000006", "Pair", [
  {
    stepId: PARENT,
    via: ["message"],
    lastAt: "2026-11-03T10:00:00+00:00",
  },
  { stepId: CHILD, current: true, lastAt: "2026-11-01T10:00:00+00:00" },
]);
// The roadmap's agents are on its status board; a step's beside the outline.
async function agentsSection() {
  const detail = await screen.findByRole("region", {
    name: /^(Status|Step details)$/,
  });
  return within(detail).findByRole("region", { name: "Agents" });
}
const rowsIn = (section: HTMLElement) =>
  Array.from(section.querySelectorAll(".agent-tie-row")).map((row) =>
    Array.from(row.querySelectorAll("a, .badge, time")).map((part) =>
      part.tagName === "TIME"
        ? part.getAttribute("datetime")
        : part.textContent,
    ),
  );

it("lists every agent whose work ties it to the roadmap, from that work, with no tying by hand", async () => {
  roster = [HAND, FIXER, LEAD, WRITER, OLD_HAND];
  view();
  const section = await agentsSection();
  await waitFor(() => expect(section).toHaveTextContent("Agents 5"));
  expect(section).toHaveTextContent(
    "From their work here: reported work, status updates, responsibilities, discussions and tracked chats.",
  );
  // As the roadmap's read orders them; the agents whose credentials are revoked are folded below.
  expect(rowsIn(section)).toEqual([
    ["Builder", "Current", "Reported work", "2026-11-03T09:00:00+00:00"],
    [
      "Fixer",
      "Current",
      "Reported work",
      "Tracked chat",
      "2026-11-03T08:00:00+00:00",
    ],
    ["Reviewer", "Current", "Responsibility", "2026-11-02T04:00:00+00:00"],
    ["Writer", "Discussion", "2026-11-02T08:00:00+00:00"],
    ["Old hand", "Tracked chat", "2026-10-30T00:00:00+00:00"],
  ]);
  expect(
    within(section).getByRole("link", { name: "Builder" }),
  ).toHaveAttribute("href", "#agents/" + HAND.id);
  const retired = within(section)
    .getByText("Retired agents (1)")
    .closest("details") as HTMLElement;
  expect(
    within(retired).getByRole("link", { name: "Old hand" }),
  ).toBeInTheDocument();
  expect(
    within(section).queryByRole("button", { name: "Tie agent" }),
  ).toBeNull();
  expect(within(section).queryByRole("combobox")).toBeNull();
});

it("says when no agent has worked on the roadmap, and how many it does not show", async () => {
  view();
  const section = await agentsSection();
  expect(
    await within(section).findByText(
      "No agent has worked on this roadmap yet.",
    ),
  ).toBeVisible();
  cleanup();
  roster = [HAND];
  rosterTotal = 201;
  view();
  const listed = await agentsSection();
  expect(await within(listed).findByText("Showing 1 of 201.")).toBeVisible();
  expect(listed).toHaveTextContent("Agents 201");
});

it("lists a step's agents from its work there and on the steps under it, with only those ties", async () => {
  roster = [HAND, FIXER, LEAD, PAIR, WRITER, OLD_HAND];
  view(PARENT);
  const section = await agentsSection();
  await waitFor(() => expect(section).toHaveTextContent("Agents 4"));
  expect(section).toHaveTextContent(
    "From their work on this step and the steps under it.",
  );
  // Pair's two steps merge: every reason, current if either is, and the later time. The Fixer's
  // work under Parent is its chat on Child, which finished.
  expect(rowsIn(section)).toEqual([
    [
      "Pair",
      "Current",
      "Reported work",
      "Discussion",
      "2026-11-03T10:00:00+00:00",
    ],
    ["Builder", "Current", "Reported work", "2026-11-03T09:00:00+00:00"],
    ["Reviewer", "Current", "Responsibility", "2026-11-02T04:00:00+00:00"],
    ["Fixer", "Tracked chat", "2026-11-02T07:00:00+00:00"],
  ]);
  cleanup();
  view(SOLO);
  const solo = await agentsSection();
  await waitFor(() => expect(solo).toHaveTextContent("Agents 2"));
  expect(solo).toHaveTextContent("From their work on this step.");
  expect(rowsIn(solo)).toEqual([
    ["Fixer", "Current", "Reported work", "2026-11-03T08:00:00+00:00"],
    ["Writer", "Discussion", "2026-11-02T08:00:00+00:00"],
  ]);
});

it("never shows the last roadmap's agents on another roadmap while that one's load", async () => {
  roster = [HAND];
  workers = [BUILDER];
  const OTHER = "33333333-3333-4333-8333-333333333333";
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith(`/roadmaps/${OTHER}/agents`)) {
      await held;
      return Response.json({
        items: [],
        roster: { items: [WRITER], total: 1 },
      });
    }
    if (url.endsWith("/roadmaps/" + OTHER))
      return Response.json({ ...road, id: OTHER, title: "Other plan" });
    return respond(input);
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const page = (id: string, view?: string) => (
    <QueryClientProvider client={client}>
      <RoadmapDetail id={id} step={view} />
    </QueryClientProvider>
  );
  const { rerender } = render(page(ROADMAP, "outline"));
  expect(await screen.findByText("Agents working here")).toBeInTheDocument();
  rerender(page(ROADMAP));
  expect(
    await within(await agentsSection()).findByRole("link", { name: "Builder" }),
  ).toBeInTheDocument();
  rerender(page(OTHER));
  await screen.findByRole("heading", { name: "Other plan", level: 1 });
  const other = await agentsSection();
  expect(within(other).queryByRole("link", { name: "Builder" })).toBeNull();
  expect(other.querySelector(".count")).toBeNull();
  // Nor does its outline say the last roadmap's agents work here.
  rerender(page(OTHER, "outline"));
  await screen.findByRole("region", { name: "Plan outline" });
  expect(screen.queryByText("Agents working here")).toBeNull();
  release();
  rerender(page(OTHER));
  expect(
    await within(await agentsSection()).findByRole("link", { name: "Writer" }),
  ).toBeInTheDocument();
});

it("says when no agent has worked on a step", async () => {
  roster = [WRITER];
  view(CHILD);
  const section = await agentsSection();
  expect(
    await within(section).findByText("No agent has worked on this step yet."),
  ).toBeVisible();
  expect(section).toHaveTextContent("Agents 0");
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
  view("outline");
  const { outline } = await outlineRows();
  expect(await within(outline).findByRole("alert")).toHaveTextContent(
    "Database is busy",
  );
  expect(within(outline).getByRole("link", { name: "Child" })).toBeVisible();
  cleanup();
  // The roadmap's agents, on its status board, say so too, rather than counting none.
  view();
  const section = await agentsSection();
  expect(await within(section).findByRole("alert")).toHaveTextContent(
    "Database is busy",
  );
  expect(section.querySelector(".count")).toBeNull();
  expect(within(section).queryByText(/No agent has worked/)).toBeNull();
});
