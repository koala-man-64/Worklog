import { beforeEach, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Agents } from "./Agents";
import type { AgentActivity, AgentDetail, AgentSummary } from "./apiTypes";

const roadmap = { id: "road-1", title: "Platform", archived: false };
const none = { items: [], total: 0 };
const summary: AgentSummary = {
  id: "agent-1",
  label: "Builder",
  active: true,
  createdAt: "2026-11-01T08:00:00Z",
  lastActivityAt: "2026-11-01T10:00:00Z",
  providers: ["claude"],
  openRuns: 1,
  runCount: 2,
  messageCount: 1,
  avatar: "teal-visor-antenna",
  working: [
    {
      participationId: "p-1",
      runId: "run-1",
      runName: "Implementation",
      description: "Writing routes",
      lastSeenAt: "2026-11-01T10:00:00Z",
      stale: false,
      item: {
        id: "item-1",
        title: "Build the API",
        status: "doing",
        archived: false,
        roadmapId: "roadmap-1",
      },
      roadmap,
      contexts: [
        {
          repositoryId: "repo-1",
          repositoryName: "Worklog",
          archived: false,
          checkoutPath: "",
          worktreePath: "",
          branch: "claude/api",
        },
      ],
    },
  ],
  roles: [
    {
      id: "role-1",
      label: "Review",
      contextType: "step",
      contextId: "step-1",
      contextTitle: "Web",
      roadmap,
      updatedAt: "2026-11-01T09:00:00Z",
    },
  ],
  repositories: {
    items: [
      {
        id: "repo-1",
        name: "Worklog",
        archived: false,
        via: ["participation"],
        current: true,
        lastAt: "2026-11-01T10:00:00Z",
      },
      {
        id: "repo-2",
        name: "Parser",
        archived: true,
        via: ["update", "message"],
        current: false,
        lastAt: "2026-10-20T09:00:00Z",
      },
    ],
    total: 2,
  },
  roadmaps: {
    items: [
      {
        ...roadmap,
        via: ["participation", "role"],
        current: true,
        lastAt: "2026-11-01T10:00:00Z",
      },
      {
        id: "road-2",
        title: "Docs",
        archived: false,
        via: ["message"],
        current: false,
        lastAt: "2026-10-21T09:00:00Z",
      },
    ],
    total: 2,
  },
};
const idle: AgentSummary = {
  ...summary,
  id: "agent-2",
  label: "Retired",
  active: false,
  lastActivityAt: null,
  providers: [],
  openRuns: 0,
  runCount: 0,
  messageCount: 0,
  avatar: null,
  working: [],
  roles: [],
  repositories: none,
  roadmaps: none,
};
const quiet: AgentSummary = {
  ...idle,
  id: "agent-3",
  label: "Reviewer",
  active: true,
  avatar: "red-dots-antenna",
};
const chat = {
  id: "chat-1",
  chatId: "c0ffee",
  title: "Fix session",
  provider: "claude",
  model: "opus",
  archived: false,
  ownerType: "item" as const,
  ownerId: "item-1",
  ownerTitle: "Build the API",
};
const tie = { lastAt: "2026-11-01T10:00:00Z" };
const detail: AgentDetail = {
  ...summary,
  credentials: [
    {
      id: "cred-2",
      label: "Builder",
      createdAt: "2026-11-01T08:00:00Z",
      revoked: false,
      registeredBy: { id: "agent-0", label: "Shared agents" },
      chatId: "7d3c1f2a-5b6e-4f10-9a8b-1c2d3e4f5a6b",
    },
    {
      id: "cred-1",
      label: "Builder",
      createdAt: "2026-10-01T08:00:00Z",
      revoked: true,
      registeredBy: null,
      chatId: null,
    },
  ],
  runs: {
    items: [
      {
        id: "run-1",
        agentId: "agent-1",
        credentialId: "cred-2",
        clientRunKey: "key",
        displayName: "Implementation",
        provider: "claude",
        externalChatId: "c0ffee",
        revision: 1,
        createdAt: "2026-11-01T09:00:00Z",
        updatedAt: "2026-11-01T09:00:00Z",
        endedAt: null,
        endReason: null,
        chats: [chat],
      },
    ],
    total: 1,
  },
  workItems: {
    items: [
      {
        id: "item-1",
        title: "Build the API",
        status: "doing",
        archived: false,
        roadmap,
        via: ["participation", "chat"],
        current: true,
        ...tie,
      },
    ],
    total: 1,
  },
  roadmaps: {
    items: [{ ...roadmap, via: ["role", "message"], current: true, ...tie }],
    total: 1,
  },
  repositories: {
    items: [
      {
        id: "repo-1",
        name: "Worklog",
        archived: true,
        via: ["update"],
        current: false,
        ...tie,
      },
    ],
    total: 3,
  },
  chats: {
    items: [{ ...chat, via: ["run"], current: true, ...tie }],
    total: 1,
  },
};
const activity: AgentActivity[] = [
  {
    kind: "message",
    id: "m-1",
    createdAt: "2026-11-01T10:00:00Z",
    text: "Starting on the plan",
    truncated: true,
    discussionId: "d-1",
    discussionTitle: "Plan",
    chatId: "c0ffee",
    chats: [{ id: "chat-1", title: "Fix session" }],
  },
  {
    kind: "update",
    id: "u-1",
    createdAt: "2026-11-01T09:30:00Z",
    text: "Waiting on review",
    truncated: false,
    updateKind: "blocker",
    runName: "Implementation",
    participationId: "p-1",
    item: { id: "item-1", title: "Build the API" },
    contexts: [
      {
        repositoryId: "repo-1",
        repositoryName: "Worklog",
        archived: false,
        checkoutPath: "",
        worktreePath: "",
        branch: "",
      },
    ],
  },
];
function show(component: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {component}
    </QueryClientProvider>,
  );
}
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
const page = <T,>(items: T[]) => ({
  items,
  total: items.length,
  limit: 200,
  offset: 0,
});
/** The registry, as the page reads it: the list, an agent's page and its activity. */
function serve(agents: AgentSummary[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.includes("/activity")) return json(page(activity));
    if (path.includes("/proposals")) return json(page([]));
    return json(path.includes("/agents?") ? page(agents) : detail);
  });
}
const index = () => screen.getByRole("region", { name: "Agent index" });
const stage = () => screen.getByRole("region", { name: "Agent card" });
// Read out on each flip: where the card is, and whose it is.
const position = () => within(stage()).getByText(/^Card \d+ of \d+/);
const entry = (name: string) =>
  within(index()).getByRole("button", { name: new RegExp("^" + name) });
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("agents-test-" + crypto.randomUUID());
});

it("shows each agent as a card with the repositories and roadmaps its work ties it to", async () => {
  serve([summary, idle]);
  show(<Agents />);
  const card = await within(stage()).findByRole("article", {
    name: "Builder",
  });
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/agents?limit=200&offset=0",
    expect.anything(),
  );
  expect(card).toHaveTextContent("Reporting work");
  expect(card).toHaveTextContent("claude · 1 open of 2 runs · 1 message");
  expect(within(card).getByRole("link", { name: "Builder" })).toHaveAttribute(
    "href",
    "#agents/agent-1",
  );
  expect(
    within(card).getByRole("link", { name: "Open full record" }),
  ).toHaveAttribute("href", "#agents/agent-1");

  const repositories = within(card).getByRole("region", {
    name: "Repositories",
  });
  expect(
    within(repositories).getByRole("link", { name: "Worklog" }),
  ).toHaveAttribute("href", "#repositories/repo-1");
  const [now, before] = within(repositories).getAllByRole("listitem");
  expect(now).toHaveTextContent(/^WorklogCurrentReported work$/);
  expect(before).toHaveTextContent("Parser (archived)");
  expect(before).toHaveTextContent("Status update · Discussion");
  expect(within(before).getByRole("time")).toHaveAttribute(
    "dateTime",
    "2026-10-20T09:00:00Z",
  );
  const roadmaps = within(card).getByRole("region", { name: "Roadmaps" });
  expect(
    within(roadmaps).getByRole("link", { name: "Platform" }),
  ).toHaveAttribute("href", "#roadmaps/road-1");
  expect(within(roadmaps).getByRole("link", { name: "Docs" })).toHaveAttribute(
    "href",
    "#roadmaps/road-2",
  );
  expect(roadmaps).toHaveTextContent("Reported work · Responsibility");

  const work = within(card).getByRole("list", { name: "Reported work" });
  expect(
    within(work).getByRole("link", { name: "Build the API" }),
  ).toHaveAttribute("href", "#work/item-1");
  expect(
    within(work).getByRole("link", { name: "Worklog · claude/api" }),
  ).toHaveAttribute("href", "#repositories/repo-1");
  const roles = within(card).getByRole("list", { name: "Responsibilities" });
  expect(within(roles).getByRole("link", { name: "Web" })).toHaveAttribute(
    "href",
    "#roadmaps/road-1/step-1",
  );
  expect(position()).toHaveTextContent("Card 1 of 2: Builder");
  expect(position()).toHaveAttribute("aria-live", "polite");

  // The index files active agents apart from retired ones, each saying where it worked.
  const active = within(index()).getByRole("region", { name: "Active agents" });
  expect(within(active).getByRole("heading")).toHaveTextContent("Active 1");
  expect(
    within(active).getByRole("button", { name: /^Builder/ }),
  ).toHaveAttribute("aria-current", "true");
  expect(entry("Builder")).toHaveTextContent(/Worklog · Platform · /);
  expect(entry("Builder")).toHaveTextContent(/Reporting work$/);
  const retired = within(index()).getByRole("region", {
    name: "Retired agents",
  });
  expect(within(retired).getByRole("heading")).toHaveTextContent("Retired 1");
  expect(within(retired).getByRole("button")).toHaveTextContent(
    /^RetiredNo activity yetCredential revoked$/,
  );
});

it("flips through the cards with its buttons and the arrow keys, round from either end", async () => {
  const user = userEvent.setup();
  serve([summary, quiet, idle]);
  show(<Agents />);
  await within(stage()).findByRole("article", { name: "Builder" });
  const open = (name: string) => within(stage()).getByRole("article", { name });
  // The two cards behind show their tabs, the furthest first.
  expect(
    [...stage().querySelectorAll(".rolodex-peek .rolodex-tab")].map(
      (tab) => tab.textContent,
    ),
  ).toEqual(["Retired", "Reviewer"]);

  // Previous from the first card goes round to the last.
  await user.click(
    within(stage()).getByRole("button", { name: "Previous card" }),
  );
  const card = open("Retired");
  expect(card).toHaveClass("flip-back");
  expect(
    within(stage()).queryByRole("article", { name: "Builder" }),
  ).not.toBeInTheDocument();
  expect(card).toHaveTextContent("Credential revoked");
  expect(card).toHaveTextContent(
    "Provider unspecified · 0 open of 0 runs · 0 messages · No activity yet",
  );
  expect(card).toHaveTextContent("No repositories yet.");
  expect(card).toHaveTextContent("No roadmaps yet.");
  expect(position()).toHaveTextContent("Card 3 of 3: Retired");
  expect(entry("Retired")).toHaveAttribute("aria-current", "true");
  expect(entry("Builder")).not.toHaveAttribute("aria-current");

  // Next from the last card goes round to the first.
  await user.click(within(stage()).getByRole("button", { name: "Next card" }));
  expect(open("Builder")).toHaveClass("flip-next");
  expect(position()).toHaveTextContent("Card 1 of 3: Builder");
  // Keys on the buttons flip the card and leave focus on the button.
  await user.keyboard("{ArrowRight}");
  expect(open("Reviewer")).toBeInTheDocument();
  expect(
    within(stage()).getByRole("button", { name: "Next card" }),
  ).toHaveFocus();
  // The index is one tab stop, the open card's tab; the arrow keys move within it.
  expect(entry("Reviewer")).toHaveAttribute("tabindex", "0");
  expect(entry("Builder")).toHaveAttribute("tabindex", "-1");
  expect(entry("Retired")).toHaveAttribute("tabindex", "-1");

  // In the index, focus follows the open card, across groups and round from the last.
  await user.click(entry("Builder"));
  expect(open("Builder")).toHaveClass("flip-back");
  await user.keyboard("{ArrowDown}");
  expect(open("Reviewer")).toBeInTheDocument();
  expect(entry("Reviewer")).toHaveFocus();
  await user.keyboard("{ArrowDown}");
  expect(open("Retired")).toBeInTheDocument();
  expect(entry("Retired")).toHaveFocus();
  await user.keyboard("{ArrowDown}");
  expect(entry("Builder")).toHaveFocus();
  await user.keyboard("{End}");
  expect(entry("Retired")).toHaveFocus();
  await user.keyboard("{Home}");
  expect(open("Builder")).toBeInTheDocument();
  expect(entry("Builder")).toHaveFocus();
});

it("finds agents by name, repository or roadmap", async () => {
  const user = userEvent.setup();
  serve([summary, idle]);
  show(<Agents />);
  await within(stage()).findByRole("article", { name: "Builder" });
  const find = within(index()).getByRole("searchbox", { name: "Find agents" });

  await user.type(find, "docs");
  expect(entry("Builder")).toBeInTheDocument();
  expect(
    within(index()).queryByRole("button", { name: /^Retired/ }),
  ).not.toBeInTheDocument();
  expect(position()).toHaveTextContent("Card 1 of 1: Builder");
  expect(
    within(stage()).getByRole("button", { name: "Next card" }),
  ).toBeDisabled();
  // A key with no card to flip to moves nothing, focus included, now or at the next change.
  await user.click(entry("Builder"));
  await user.keyboard("{ArrowDown}");
  expect(entry("Builder")).toHaveFocus();

  // Words that hide the open card open the first card they find, which stays open once cleared.
  await user.clear(find);
  await user.type(find, "RETIRED");
  expect(
    within(stage()).getByRole("article", { name: "Retired" }),
  ).toBeInTheDocument();
  expect(find).toHaveValue("RETIRED");
  expect(find).toHaveFocus();
  await user.clear(find);
  expect(
    within(stage()).getByRole("article", { name: "Retired" }),
  ).toBeInTheDocument();
  expect(position()).toHaveTextContent("Card 2 of 2: Retired");

  await user.type(find, "nowhere");
  expect(
    within(index()).getByText("No agent matches those words."),
  ).toBeInTheDocument();
  expect(within(stage()).queryByRole("article")).not.toBeInTheDocument();
});

it("opens again the card left open, or the agent whose page was open", async () => {
  const user = userEvent.setup();
  serve([summary, idle]);
  const first = show(<Agents />);
  await within(stage()).findByRole("article", { name: "Builder" });
  await user.click(within(stage()).getByRole("button", { name: "Next card" }));
  first.unmount();

  const again = show(<Agents />);
  expect(
    await within(stage()).findByRole("article", { name: "Retired" }),
  ).toBeInTheDocument();
  again.unmount();

  const agentPage = show(<Agents selected="agent-1" />);
  await screen.findByRole("heading", { name: "Builder", level: 1 });
  agentPage.unmount();
  show(<Agents />);
  expect(
    await within(stage()).findByRole("article", { name: "Builder" }),
  ).toBeInTheDocument();
});

it("opens the first card, without a flip, when the open agent leaves the list", async () => {
  const user = userEvent.setup();
  let agents = [summary, idle];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    json(page(agents)),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <Agents />
    </QueryClientProvider>,
  );
  await within(stage()).findByRole("article", { name: "Builder" });
  await user.click(within(stage()).getByRole("button", { name: "Next card" }));
  expect(within(stage()).getByRole("article", { name: "Retired" })).toHaveClass(
    "flip-next",
  );

  agents = [summary];
  await act(() => client.invalidateQueries());
  const card = await within(stage()).findByRole("article", {
    name: "Builder",
  });
  expect(card).not.toHaveClass("flip-next");
  expect(card).not.toHaveClass("flip-back");
});

it("names the first repositories and roadmaps on a card and leaves the rest to the agent's page", async () => {
  const many = Array.from({ length: 10 }, (_, n) => ({
    id: "repo-" + n,
    name: "Repository " + n,
    archived: false,
    via: ["update" as const],
    current: false,
    lastAt: "2026-11-01T10:00:00Z",
  }));
  serve([{ ...summary, repositories: { items: many, total: 12 } }]);
  show(<Agents />);
  const card = await within(stage()).findByRole("article", {
    name: "Builder",
  });
  const repositories = within(card).getByRole("region", {
    name: "Repositories",
  });
  expect(repositories).toHaveTextContent(/^Repositories 12/);
  expect(within(repositories).getAllByRole("listitem")).toHaveLength(8);
  expect(
    within(repositories).getByRole("link", { name: "4 more on its page" }),
  ).toHaveAttribute("href", "#agents/agent-1");
  // A lone card has nothing to flip to.
  expect(
    within(stage()).getByRole("button", { name: "Previous card" }),
  ).toBeDisabled();
});

it("explains an empty registry", async () => {
  serve([]);
  show(<Agents />);
  expect(await screen.findByText(/No agents yet/)).toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "Agent index" }),
  ).not.toBeInTheDocument();
});

it("shows an agent's ties with their reasons, runs, messages and updates", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    String(input).includes("/activity") ? json(page(activity)) : json(detail),
  );
  show(<Agents selected="agent-1" />);
  expect(
    await screen.findByRole("heading", { name: "Builder" }),
  ).toBeInTheDocument();
  const work = screen.getByRole("region", { name: "Work items" });
  expect(within(work).getByText("Current")).toBeInTheDocument();
  expect(within(work).getByText("Reported work")).toBeInTheDocument();
  expect(within(work).getByText("Tracked chat")).toBeInTheDocument();
  const roadmaps = screen.getByRole("region", { name: "Roadmaps" });
  expect(within(roadmaps).getByText("Responsibility")).toBeInTheDocument();
  expect(within(roadmaps).getByText("Discussion")).toBeInTheDocument();
  const repositories = screen.getByRole("region", { name: "Repositories" });
  expect(repositories).toHaveTextContent("Worklog (archived)");
  expect(repositories).toHaveTextContent("Showing 1 of 3.");
  expect(within(repositories).queryByText("Current")).not.toBeInTheDocument();
  const chats = screen.getByRole("region", { name: "Tracked chats" });
  expect(
    within(chats).getByRole("link", { name: "Fix session" }),
  ).toHaveAttribute("href", "#chats/chat-1");
  expect(chats).toHaveTextContent("claude · opus · Build the API");
  expect(
    screen.getByRole("button", { name: "End run Implementation" }),
  ).toBeInTheDocument();

  const feed = screen.getByRole("region", { name: "Messages and updates" });
  expect(
    await within(feed).findByRole("link", { name: "Plan" }),
  ).toHaveAttribute("href", "#discussions/d-1");
  expect(feed).toHaveTextContent("Starting on the plan…");
  expect(within(feed).getByText("Blocker")).toBeInTheDocument();
  expect(feed).toHaveTextContent("Waiting on review");
  const registrar = screen.getByRole("link", { name: "Shared agents" });
  expect(registrar).toHaveAttribute("href", "#agents/agent-0");
  expect(registrar.parentElement).toHaveTextContent(
    /^Registered by Shared agents for chat 7d3c1f2a-5b6e-4f10-9a8b-1c2d3e4f5a6b .* · Active$/,
  );
  expect(screen.getByText(/^Issued/)).toHaveTextContent("· Revoked");
  expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
    "href",
    "#settings",
  );
});
