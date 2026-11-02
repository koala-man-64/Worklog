import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Agents } from "./Agents";
import type { AgentActivity, AgentDetail, AgentSummary } from "./agentTypes";

const roadmap = { id: "road-1", title: "Platform", archived: false };
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
      },
      roadmap,
      contexts: [
        {
          repositoryId: "repo-1",
          repositoryName: "Worklog",
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
  working: [],
  roles: [],
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
    },
    {
      id: "cred-1",
      label: "Builder",
      createdAt: "2026-10-01T08:00:00Z",
      revoked: true,
      registeredBy: null,
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
    contexts: [{ repositoryId: "repo-1", repositoryName: "Worklog" }],
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
  limit: 50,
  offset: 0,
});
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("agents-test-" + crypto.randomUUID());
});

it("lists who works on what with links to the work, roadmap and repository", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(page([summary, idle])));
  show(<Agents />);
  const builder = (
    await screen.findByRole("link", { name: "Builder" })
  ).closest("article")!;
  expect(builder).toHaveTextContent("Reporting work");
  expect(builder).toHaveTextContent("claude · 1 open of 2 runs · 1 message");
  const work = within(builder).getByRole("list", { name: "Reported work" });
  expect(
    within(work).getByRole("link", { name: "Build the API" }),
  ).toHaveAttribute("href", "#work/item-1");
  expect(within(work).getByRole("link", { name: "Platform" })).toHaveAttribute(
    "href",
    "#roadmaps/road-1",
  );
  expect(
    within(work).getByRole("link", { name: "Worklog · claude/api" }),
  ).toHaveAttribute("href", "#repositories/repo-1");
  const roles = within(builder).getByRole("list", { name: "Responsibilities" });
  expect(within(roles).getByRole("link", { name: "Web" })).toHaveAttribute(
    "href",
    "#roadmaps/road-1/step-1",
  );
  const retired = screen
    .getByRole("link", { name: "Retired" })
    .closest("article")!;
  expect(retired).toHaveTextContent("Credential revoked");
  expect(retired).toHaveTextContent(
    "Provider unspecified · 0 open of 0 runs · 0 messages · No activity yet",
  );
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/agents?limit=50&offset=0",
    expect.anything(),
  );
});

it("explains an empty registry", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(page([])));
  show(<Agents />);
  expect(await screen.findByText(/No agents yet/)).toBeInTheDocument();
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
  expect(screen.getByText(/^Registered by Shared agents/)).toHaveTextContent(
    "· Active",
  );
  expect(screen.getByText(/^Issued/)).toHaveTextContent("· Revoked");
  expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
    "href",
    "#settings",
  );
});
