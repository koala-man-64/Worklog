import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RoadmapDetail } from "./Roadmaps";
import type {
  Answer,
  LiveSnapshot,
  Roadmap,
  RoadmapQuestion,
  RoadmapWorker,
  Step,
} from "./apiTypes";
import { MADE, aRoadmap, aStep } from "./fixtures";

const ROADMAP = "44444444-4444-4444-8444-444444444444";
const id = (n: number) =>
  `aaaaaaaa-0000-4000-8000-${String(n).padStart(12, "0")}`;
const step = (
  n: number,
  title: string,
  parent: number | null,
  status = "open",
): Step =>
  aStep({
    id: id(n),
    title,
    parentId: parent === null ? null : id(parent),
    position: n,
    status,
  });
const question = (
  n: number,
  title: string,
  fields: Partial<RoadmapQuestion> = {},
): RoadmapQuestion => ({
  id: "bbbbbbbb-0000-4000-8000-00000000000" + n,
  roadmapId: ROADMAP,
  title,
  details: "",
  answer: "",
  status: "open",
  revision: 1,
  archived: false,
  createdAt: MADE,
  updatedAt: MADE,
  ...fields,
});
// Two phases: Build has a step done, one in progress and two open; Ship one open and one blocked.
// The read lists Docs first; its position puts it last in the outline.
const plan = [
  step(7, "Docs", 6),
  step(1, "Build", null, "doing"),
  step(2, "Parse", 1, "done"),
  step(3, "Lex", 1, "doing"),
  step(4, "Emit", 1),
  step(5, "Pack", 1),
  step(6, "Ship", null, "blocked"),
  step(8, "Release", 6, "blocked"),
];
// Nothing moves on the roadmap: Live answers for it with every list empty.
const quietLive: LiveSnapshot = {
  generatedAt: MADE,
  windowMinutes: 60,
  roadmapId: ROADMAP,
  roadmapTitle: "Compiler",
  summary: {
    activeAgents: 0,
    workingChats: 0,
    activeWorkItems: 0,
    activeSteps: 0,
    activeRepos: 0,
    repoOverlaps: 0,
    overCapRepos: 0,
    pendingRequests: 0,
    waitingOnAgents: 0,
    waitingProposals: 0,
    openQuestions: 0,
    needsYou: 0,
  },
  roadmaps: [],
  work: [],
  agents: [],
  untrackedSessions: [],
  repositories: [],
  workEdges: [],
  repoEdges: [],
  commEdges: [],
  lanes: [],
  pulses: [],
};
const liveReads = () =>
  vi
    .mocked(fetch)
    .mock.calls.map(([input]) => String(input))
    .filter((url) => url.includes("/live?"));
let served: Roadmap;
let board: Answer<"/api/v1/roadmaps/{identity}/board">;
let workers: RoadmapWorker[];
const writes: { url: string; method: string; body: unknown }[] = [];
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("roadmap-board-" + crypto.randomUUID());
  location.hash = "";
  writes.length = 0;
  served = aRoadmap({ id: ROADMAP, title: "Compiler", steps: plan });
  board = { waiting: [], recentlyDone: [] };
  workers = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method !== "GET") {
      writes.push({ url, method, body: JSON.parse(String(init?.body)) });
      return Response.json({ id: ROADMAP, revision: 5 });
    }
    if (url.endsWith(`/roadmaps/${ROADMAP}`)) return Response.json(served);
    if (url.endsWith(`/roadmaps/${ROADMAP}/board`)) return Response.json(board);
    if (url.endsWith(`/roadmaps/${ROADMAP}/agents`))
      return Response.json({
        items: workers,
        roster: { items: [], total: 0 },
      });
    if (url.includes("/live?")) return Response.json(quietLive);
    return Response.json({ items: [], total: 0 });
  });
});
const page = (view?: string) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    <RoadmapDetail id={ROADMAP} step={view} />
  </QueryClientProvider>
);
const section = async (name: string) =>
  (
    await screen.findByRole("heading", {
      level: 3,
      name: new RegExp("^" + name),
    })
  ).parentElement as HTMLElement;
const titles = (element: HTMLElement) =>
  within(element)
    .queryAllByRole("link")
    .filter((a) => a.classList.contains("item-title"))
    .map((a) => a.textContent);

it("shows where each phase stands, the steps in progress with their agents, and the open steps next", async () => {
  workers = [
    {
      kind: "agent",
      id: "cccccccc-0000-4000-8000-000000000001",
      label: "Builder",
      avatar: null,
      provider: "",
      model: "",
      lastAt: "2026-11-03T09:00:00+00:00",
      stale: false,
      ties: [
        {
          stepId: id(3),
          via: "participation",
          item: { id: "item-1", title: "Write the lexer" },
          chat: null,
          role: null,
          lastAt: "2026-11-03T09:00:00+00:00",
          stale: false,
        },
      ],
    },
  ];
  render(page());
  const board = await screen.findByRole("region", { name: "Status" });
  const phases = await within(board).findByRole("table", { name: "Phases" });
  expect(
    within(phases)
      .getAllByRole("columnheader")
      .map((h) => h.textContent),
  ).toEqual([
    "Phase",
    "Progress",
    "Done",
    "Active",
    "Doing",
    "Blocked",
    "Open",
  ]);
  const [, ...rows] = within(phases).getAllByRole("row");
  // A phase counts its leaves by status; the parent's own derived status doesn't count.
  expect(
    rows.map((row) => [
      within(row).getByRole("rowheader").textContent,
      ...within(row)
        .getAllByRole("cell")
        .slice(1)
        .map((c) => c.textContent),
    ]),
  ).toEqual([
    ["Build", "1", "0", "1", "0", "2"],
    ["Ship", "0", "0", "0", "1", "1"],
  ]);
  // The bar fills in the same order, each part its share of the phase; open steps are its track.
  expect(
    rows.map((row) =>
      Array.from(row.querySelectorAll<HTMLElement>(".status-bar > span")).map(
        (part) => [part.className, part.style.width],
      ),
    ),
  ).toEqual([
    [
      ["done", "25%"],
      ["doing", "25%"],
    ],
    [["blocked", "50%"]],
  ]);
  expect(within(board).getByRole("link", { name: "Build" })).toHaveAttribute(
    "href",
    `#roadmaps/${ROADMAP}/${id(1)}`,
  );

  const now = await section("Now");
  expect(titles(now)).toEqual(["Lex"]);
  // Each step names its phase, and the agents working on it.
  expect(within(now).getByText("Build")).toBeVisible();
  expect(
    await within(now).findByRole("link", { name: "Builder" }),
  ).toHaveAttribute("href", "#agents/cccccccc-0000-4000-8000-000000000001");
  // In outline order, whatever order the read lists the steps in.
  expect(titles(await section("Next"))).toEqual(["Emit", "Pack", "Docs"]);
  expect(
    within(await section("Next")).getByRole("link", { name: "Emit" }),
  ).toHaveAttribute("href", `#roadmaps/${ROADMAP}/${id(4)}`);
});

it("shows the roadmap's steps by status beside the bar at the top", async () => {
  render(page());
  const counts = await screen.findByRole("list", { name: "Steps by status" });
  const progress = counts.parentElement as HTMLElement;
  // Its six leaves; the parents' derived statuses don't count.
  expect(progress).toHaveTextContent(/^6 steps/);
  expect(
    within(counts)
      .getAllByRole("listitem")
      .map((li) => li.textContent),
  ).toEqual(["1 done", "0 active", "1 doing", "1 blocked", "3 open"]);
  // Filled done, then in progress, then blocked, each its share; open steps are the track.
  const parts = Array.from(
    progress.querySelectorAll<HTMLElement>(".status-bar > span"),
  );
  expect(parts.map((part) => part.className)).toEqual([
    "done",
    "doing",
    "blocked",
  ]);
  for (const part of parts)
    expect(parseFloat(part.style.width)).toBeCloseTo(100 / 6);
});

it("shows the steps an agent works on as Active, apart from Doing, and in progress on the board", async () => {
  // An agent works on Emit, which the person left open, and on Lex, which they marked doing.
  served = aRoadmap({
    id: ROADMAP,
    title: "Compiler",
    steps: plan.map((s) =>
      [id(1), id(3), id(4)].includes(s.id) ? { ...s, agentActive: true } : s,
    ),
  });
  render(page());
  const counts = await screen.findByRole("list", { name: "Steps by status" });
  // Active steps count apart from the status the person set.
  expect(
    within(counts)
      .getAllByRole("listitem")
      .map((li) => li.textContent),
  ).toEqual(["1 done", "2 active", "0 doing", "1 blocked", "2 open"]);
  expect(
    Array.from(
      (counts.parentElement as HTMLElement).querySelectorAll<HTMLElement>(
        ".status-bar > span",
      ),
    ).map((part) => part.className),
  ).toEqual(["done", "active", "blocked"]);
  const phases = await screen.findByRole("table", { name: "Phases" });
  const build = within(phases).getByRole("row", { name: /^Build/ });
  expect(
    within(build)
      .getAllByRole("cell")
      .slice(1)
      .map((c) => c.textContent),
  ).toEqual(["1", "2", "0", "0", "1"]);
  // Now holds the steps in progress, Active ones among them; Next only the open steps nobody is on.
  const now = await section("Now");
  expect(titles(now)).toEqual(["Lex", "Emit"]);
  expect(now.querySelectorAll(".status-dot.active")).toHaveLength(2);
  expect(titles(await section("Next"))).toEqual(["Pack", "Docs"]);
});

it("marks Active steps in the outline beside the progress the person set", async () => {
  served = aRoadmap({
    id: ROADMAP,
    title: "Compiler",
    steps: plan.map((s) => (s.id === id(4) ? { ...s, agentActive: true } : s)),
  });
  render(page("outline"));
  const progress = await screen.findByRole("combobox", {
    name: "Progress for Emit",
  });
  const row = progress.closest("li") as HTMLElement;
  expect(row).toHaveClass("status-active");
  expect(within(row).getByText("Active")).toHaveAttribute(
    "title",
    expect.stringMatching(/^Active: an agent reported work/),
  );
  // The menu keeps the status the person set, and offers no Active: nobody sets it.
  expect(progress).toHaveValue("open");
  expect(
    within(progress)
      .getAllByRole("option")
      .map((o) => o.textContent),
  ).toEqual(["Open", "Doing", "Blocked", "Done"]);
  const pack = (
    await screen.findByRole("combobox", { name: "Progress for Pack" })
  ).closest("li") as HTMLElement;
  expect(pack).toHaveClass("status-open");
  expect(within(pack).queryByText("Active")).toBeNull();
});

it("lists what waits on the person: open questions, blocked steps and proposals", async () => {
  served = {
    ...served,
    questions: [
      question(1, "Which grammar?"),
      question(2, "Settled?", { status: "answered", answer: "Yes" }),
      question(3, "Put away?", { archived: true }),
    ],
  };
  board = {
    waiting: [
      {
        id: "dddddddd-0000-4000-8000-000000000001",
        title: "Add a parser step",
        status: "submitted",
        revision: 2,
        actorLabel: "Planner",
        agentId: null,
        createdAt: MADE,
        submittedAt: MADE,
        updatedAt: MADE,
        operationCount: 2,
      },
    ],
    recentlyDone: [],
  };
  render(page());
  const needs = await section("Needs you");
  await waitFor(() => expect(needs).toHaveTextContent("Needs you 3"));
  expect(
    within(needs).getByRole("link", { name: "Which grammar?" }),
  ).toHaveAttribute("href", `#roadmaps/${ROADMAP}/questions`);
  expect(within(needs).queryByText("Settled?")).toBeNull();
  expect(within(needs).queryByText("Put away?")).toBeNull();
  expect(within(needs).getByRole("link", { name: "Release" })).toHaveAttribute(
    "href",
    `#roadmaps/${ROADMAP}/${id(8)}`,
  );
  expect(
    within(needs).getByRole("link", { name: "Add a parser step" }),
  ).toHaveAttribute("href", "#review/dddddddd-0000-4000-8000-000000000001");
  expect(within(needs).getByText("Planner · 2 changes")).toBeVisible();
});

it("lists the steps lately done, the latest first", async () => {
  served = {
    ...served,
    steps: plan.map((s) => (s.id === id(4) ? { ...s, status: "done" } : s)),
  };
  board = {
    waiting: [],
    recentlyDone: [
      { id: id(4), title: "Emit", doneAt: "2026-11-05T10:00:00+00:00" },
      { id: id(2), title: "Parse", doneAt: "2026-11-02T10:00:00+00:00" },
    ],
  };
  render(page());
  const done = await section("Recently done");
  await waitFor(() => expect(titles(done)).toEqual(["Emit", "Parse"]));
  expect(
    Array.from(done.querySelectorAll("time")).map((t) =>
      t.getAttribute("datetime"),
    ),
  ).toEqual(["2026-11-05T10:00:00+00:00", "2026-11-02T10:00:00+00:00"]);
});

it("shows the first steps of a long list and counts the rest, linking to the outline", async () => {
  served = {
    ...served,
    steps: [
      step(1, "Build", null),
      ...Array.from({ length: 11 }, (_, i) => step(i + 2, `Task ${i + 1}`, 1)),
    ],
  };
  board = {
    waiting: Array.from({ length: 10 }, (_, i) => ({
      id: `dddddddd-0000-4000-8000-0000000000${10 + i}`,
      title: `Proposal ${i + 1}`,
      status: "submitted",
      revision: 2,
      actorLabel: "Planner",
      agentId: null,
      createdAt: MADE,
      submittedAt: MADE,
      updatedAt: MADE,
      operationCount: 1,
    })),
    recentlyDone: [],
  };
  render(page());
  const next = await section("Next");
  expect(next).toHaveTextContent("Next 11");
  expect(titles(next)).toHaveLength(8);
  expect(
    within(next).getByRole("link", { name: "3 more open" }),
  ).toHaveAttribute("href", `#roadmaps/${ROADMAP}/outline`);
  // The proposals waiting on it too, the rest on Review.
  const needs = await section("Needs you");
  expect(
    await within(needs).findByRole("link", { name: "2 more to review" }),
  ).toHaveAttribute("href", "#review");
  expect(titles(needs)).toHaveLength(8);
  expect(needs).toHaveTextContent("Needs you 10");
});

it("leaves out phases a plan doesn't have, and says when a list is empty", async () => {
  served = {
    ...served,
    steps: [step(1, "Only", null, "done"), step(2, "Other", null, "done")],
  };
  render(page());
  expect(
    await screen.findByText("Nothing waits on you here."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Phases" })).toBeNull();
  expect(screen.getByText("No step is in progress.")).toBeVisible();
  expect(screen.getByText("No step is open.")).toBeVisible();
  expect(screen.getByText("No step marked done yet.")).toBeVisible();
  // The overview, its people and agents follow the board.
  expect(screen.getByRole("heading", { name: "Overview" })).toBeVisible();
  expect(screen.getByRole("region", { name: "Agents" })).toBeInTheDocument();
});

it("links each view at its own address and marks the one shown", async () => {
  served = { ...served, questions: [question(1, "Which grammar?")] };
  const { rerender } = render(page());
  const views = await screen.findByRole("navigation", {
    name: "Roadmap views",
  });
  const links = within(views).getAllByRole("link");
  expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
    ["Status", `#roadmaps/${ROADMAP}`],
    ["Outline", `#roadmaps/${ROADMAP}/outline`],
    ["Deliverables", `#roadmaps/${ROADMAP}/deliverables`],
    ["Execution", `#roadmaps/${ROADMAP}/execution`],
    // Beside it, how many questions are open, which its name says in words.
    ["Questions and decisions1", `#roadmaps/${ROADMAP}/questions`],
    ["Chats", `#roadmaps/${ROADMAP}/chats`],
    ["Discussions", `#roadmaps/${ROADMAP}/discussions`],
    ["Live", `#roadmaps/${ROADMAP}/live`],
    ["Material", `#roadmaps/${ROADMAP}/material`],
    ["History", `#roadmaps/${ROADMAP}/history`],
  ]);
  expect(links[4]).toHaveAccessibleName("Questions and decisions, 1 open");
  const current = () =>
    within(views)
      .getAllByRole("link")
      .filter((a) => a.getAttribute("aria-current") === "page")
      .map((a) => a.textContent);
  expect(current()).toEqual(["Status"]);
  // Only the status board shows; the outline is its own view.
  expect(screen.queryByRole("region", { name: "Plan outline" })).toBeNull();
  rerender(page("material"));
  expect(
    await screen.findByRole("region", { name: "Material" }),
  ).toBeInTheDocument();
  expect(current()).toEqual(["Material"]);
  // A step's page is the outline's, with the step beside it.
  rerender(page(id(3)));
  expect(
    await screen.findByRole("region", { name: "Plan outline" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Step details" })).toBeVisible();
  expect(current()).toEqual(["Outline"]);
  // Its history opens below it, as other records' does, and no raw record shows at the top.
  expect(screen.queryByRole("button", { name: "Step history" })).toBeNull();
  expect(screen.getByText("Activity history")).toBeInTheDocument();
});

it("keeps the versions, with their notes and Restore, under History, and no revision counter", async () => {
  served = {
    ...served,
    revision: 4,
    versions: [
      { number: 1, note: "Created", createdAt: MADE },
      { number: 2, note: "", createdAt: MADE },
    ],
  };
  render(page("history"));
  const history = await screen.findByRole("region", { name: "History" });
  const versions = await section("Immutable versions");
  expect(
    Array.from(versions.querySelectorAll(".line")).map(
      (line) => line.querySelector("span")?.textContent,
    ),
  ).toEqual(["Created", "Structure saved"]);
  expect(
    within(history).queryByRole("button", { name: /^View version/ }),
  ).toBeNull();
  await userEvent.click(
    within(versions).getAllByRole("button", {
      name: "Restore as new version",
    })[0],
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: `/api/v1/roadmaps/${ROADMAP}/restore`,
      method: "POST",
      body: { revision: 4, number: 1 },
    }),
  );
  expect(screen.queryByText(/^Revision /)).toBeNull();
});

it("opens the structure editor beside the outline, and closes it for another view", async () => {
  const { rerender } = render(page());
  await userEvent.click(
    await screen.findByRole("button", { name: "Edit structure" }),
  );
  expect(location.hash).toBe(`#roadmaps/${ROADMAP}/outline`);
  expect(
    screen.getByRole("region", { name: "Structure editor" }),
  ).toBeVisible();
  expect(screen.getByRole("region", { name: "Plan outline" })).toBeVisible();
  rerender(page("outline"));
  expect(
    screen.getByRole("region", { name: "Structure editor" }),
  ).toBeVisible();
  // Another view closes it; its draft stays saved.
  rerender(page("questions"));
  expect(
    await screen.findByRole("region", { name: "Questions and decisions" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Structure editor" })).toBeNull();
  expect(screen.getByRole("button", { name: "Edit structure" })).toBeVisible();
});

it("gives each title it may cut short in full on hover", async () => {
  const long =
    "Write the lexer as a hand-written scanner that keeps every token kind the downstream tools read";
  served = {
    ...served,
    steps: plan.map((s) => (s.id === id(3) ? { ...s, title: long } : s)),
    questions: [
      question(1, "Which grammar do we keep for the old configuration files?"),
    ],
  };
  const { rerender } = render(page());
  const now = await section("Now");
  expect(within(now).getByRole("link", { name: long })).toHaveAttribute(
    "title",
    long,
  );
  // The step's phase, and each phase's own row, say theirs too.
  expect(within(now).getByText("Build")).toHaveAttribute("title", "Build");
  expect(
    within(await section("Phases")).getByRole("link", { name: "Ship" }),
  ).toHaveAttribute("title", "Ship");
  expect(
    within(await section("Needs you")).getByRole("link", {
      name: "Which grammar do we keep for the old configuration files?",
    }),
  ).toHaveAttribute(
    "title",
    "Which grammar do we keep for the old configuration files?",
  );
  rerender(page("outline"));
  const outline = await screen.findByRole("region", { name: "Plan outline" });
  expect(within(outline).getByRole("link", { name: long })).toHaveAttribute(
    "title",
    long,
  );
});

it("shows Live beside every view but the Live tab, and remembers that it was hidden", async () => {
  const { rerender } = render(page());
  const panel = await screen.findByRole("complementary", {
    name: "Live activity",
  });
  const toggle = screen.getByRole("button", { name: "Live panel" });
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(toggle).toHaveAttribute("aria-controls", panel.id);
  expect(
    await within(panel).findByText(
      "Nothing is moving on this roadmap in this window.",
    ),
  ).toBeInTheDocument();
  expect(liveReads()).toContain(
    `/api/v1/live?windowMinutes=60&includeIdle=false&roadmapId=${ROADMAP}`,
  );
  // A step's page keeps it, beside the outline and the step.
  rerender(page(id(3)));
  expect(
    await screen.findByRole("region", { name: "Plan outline" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("complementary", { name: "Live activity" }),
  ).toBeInTheDocument();
  // The Live tab shows all of it: no panel, and nothing to open one with.
  rerender(page("live"));
  expect(
    await screen.findByRole("group", { name: "Activity window" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("complementary", { name: "Live activity" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Live panel" })).toBeNull();
  rerender(page("questions"));
  await userEvent.click(
    within(
      await screen.findByRole("complementary", { name: "Live activity" }),
    ).getByRole("button", { name: "Hide Live panel" }),
  );
  expect(
    screen.queryByRole("complementary", { name: "Live activity" }),
  ).toBeNull();
  expect(screen.getByRole("button", { name: "Live panel" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(localStorage.getItem("roadmap-live-panel")).toBe("closed");
  rerender(page("material"));
  expect(
    await screen.findByRole("region", { name: "Material" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("complementary", { name: "Live activity" }),
  ).toBeNull();
});

it("reads no Live while the panel stays hidden, and opens it again from the header", async () => {
  localStorage.setItem("roadmap-live-panel", "closed");
  render(page());
  expect(
    await screen.findByRole("heading", { level: 1, name: "Compiler" }),
  ).toBeInTheDocument();
  const toggle = screen.getByRole("button", { name: "Live panel" });
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(toggle).not.toHaveAttribute("aria-controls");
  expect(
    screen.queryByRole("complementary", { name: "Live activity" }),
  ).toBeNull();
  expect(liveReads()).toEqual([]);
  await userEvent.click(toggle);
  expect(
    await screen.findByRole("complementary", { name: "Live activity" }),
  ).toBeInTheDocument();
  expect(localStorage.getItem("roadmap-live-panel")).toBe("open");
  await waitFor(() => expect(liveReads()).toHaveLength(1));
});

it("places Step details and Structure editor in a tab of the Live panel on Outline", async () => {
  const { rerender } = render(page("outline"));
  expect(
    await screen.findByRole("region", { name: "Plan outline" }),
  ).toBeInTheDocument();
  const panel = screen.getByRole("complementary", { name: "Live activity" });
  const liveTab = within(panel).getByRole("tab", { name: "Live" });
  const detailsTab = within(panel).getByRole("tab", { name: "Step details" });
  expect(liveTab).toHaveAttribute("aria-selected", "true");
  expect(detailsTab).toHaveAttribute("aria-selected", "false");

  // Switching to Step details inside the Live panel shows the prompt when no step is chosen.
  await userEvent.click(detailsTab);
  expect(detailsTab).toHaveAttribute("aria-selected", "true");
  expect(
    within(panel).getByRole("region", { name: "Step details" }),
  ).toBeVisible();
  expect(
    within(panel).getByText("Choose a step to see its work, chats and notes."),
  ).toBeVisible();

  // Selecting a step opens Step details inside the right panel, and Live stays one tab away.
  await userEvent.click(liveTab);
  expect(liveTab).toHaveAttribute("aria-selected", "true");
  rerender(page(id(3)));
  expect(detailsTab).toHaveAttribute("aria-selected", "true");
  const details = within(panel).getByRole("region", { name: "Step details" });
  expect(within(details).getByRole("heading", { name: "Lex" })).toBeVisible();

  // Clicking the already-selected step after switching back to Live returns to Step details.
  await userEvent.click(liveTab);
  expect(liveTab).toHaveAttribute("aria-selected", "true");
  await userEvent.click(
    within(screen.getByRole("region", { name: "Plan outline" })).getByRole(
      "link",
      { name: "Lex" },
    ),
  );
  expect(detailsTab).toHaveAttribute("aria-selected", "true");
  expect(
    within(panel).getByRole("region", { name: "Step details" }),
  ).toBeVisible();

  // Edit structure uses the same tab in the Live panel.
  await userEvent.click(screen.getByRole("button", { name: "Edit structure" }));
  const editorTab = within(panel).getByRole("tab", {
    name: "Structure editor",
  });
  expect(editorTab).toHaveAttribute("aria-selected", "true");
  expect(
    within(panel).getByRole("region", { name: "Structure editor" }),
  ).toBeVisible();
});

it("opens Step details even when the Live panel was hidden, without polling Live", async () => {
  localStorage.setItem("roadmap-live-panel", "closed");
  const { rerender } = render(page(id(3)));
  const panel = await screen.findByRole("complementary", {
    name: "Live activity",
  });
  expect(
    within(panel).getByRole("tab", { name: "Step details" }),
  ).toHaveAttribute("aria-selected", "true");
  expect(
    within(panel).getByRole("region", { name: "Step details" }),
  ).toBeVisible();
  expect(liveReads()).toEqual([]);
  // Leaving Outline keeps the Live panel hidden as preferred.
  rerender(page("material"));
  expect(
    await screen.findByRole("region", { name: "Material" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("complementary", { name: "Live activity" }),
  ).toBeNull();
  expect(liveReads()).toEqual([]);
});
