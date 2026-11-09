import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { setDataContext } from "./api";
import { ChatAgents, RunningSessions, runsIn, sessionState } from "./ChatTies";
import type {
  AgentSummary,
  ChatAgent,
  ChatOverview,
  Item,
  Roadmap,
  RunningSession,
} from "./apiTypes";

const ITEM = { id: "item-1", title: "Import format", status: "doing" } as Item;
const ROADMAP = { id: "roadmap-1", title: "Platform" } as Roadmap;
const HELPER: AgentSummary = {
  id: "agent-helper",
  label: "Helper",
  active: true,
  createdAt: "2026-11-01T10:00:00Z",
  lastActivityAt: null,
  providers: ["claude"],
  openRuns: 1,
  runCount: 1,
  messageCount: 0,
  working: [],
  roles: [],
};
const REVOKED: AgentSummary = {
  ...HELPER,
  id: "agent-old",
  label: "Old helper",
  active: false,
};
const registered: ChatAgent = {
  id: "agent-parser",
  label: "Parser agent",
  avatar: null,
  active: true,
  binding: "registered",
  chatId: "session-parser",
};
const claimant: ChatAgent = {
  ...registered,
  id: "agent-claims",
  label: "Claimant",
  binding: "run",
  chatId: "LOCAL_PARSER",
};
const confirmed: ChatAgent = {
  ...registered,
  id: "agent-confirmed",
  label: "Reviewer",
  binding: "confirmed",
  chatId: "local_parser",
};
function session(
  name: string,
  fields: Partial<RunningSession> = {},
): RunningSession {
  return {
    sessionId: "session-" + name.toLowerCase().replace(/\W+/g, "-"),
    hostSessionId: null,
    chatId: "session-" + name.toLowerCase().replace(/\W+/g, "-"),
    name,
    cwd:
      "C:\\Projects\\worklog\\.claude\\worktrees\\" +
      name.toLowerCase().replace(/\W+/g, "-"),
    entrypoint: "claude-desktop",
    kind: "interactive",
    status: "idle",
    waitingFor: "",
    startedAt: "2026-11-06T08:00:00Z",
    statusAt: "2026-11-06T09:00:00Z",
    version: "2.1.317",
    model: "claude-opus-5-5",
    tracked: [],
    agents: [],
    ...fields,
  };
}
const parser = session("Parser fix", {
  sessionId: "session-parser",
  hostSessionId: "local_parser",
  chatId: "session-parser",
  status: "working",
  agents: [registered, confirmed, claimant],
});
const docs = session("Docs pass", {
  status: "waiting",
  waitingFor: "permission prompt",
  entrypoint: "cli",
  model: "",
});
// Its only chat is archived: it is offered again, saying where it was.
const revived = session("Release notes", {
  tracked: [
    {
      id: "chat-old",
      title: "Release notes",
      archived: true,
      ownerType: "item",
      ownerId: ITEM.id,
      ownerTitle: ITEM.title,
    },
  ],
});
const tracked = session("Already tracked", {
  tracked: [{ ...revived.tracked[0], id: "chat-live", archived: false }],
});

type Call = { method: string; path: string; body: unknown };
function serve(sessions: RunningSession[]) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    calls.push({
      method,
      path,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    });
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200 });
    if (method !== "GET")
      return json(path === "/chats" ? { id: "chat-new" } : {});
    if (path.startsWith("/claude-sessions")) return json({ items: sessions });
    if (path.startsWith("/work-items"))
      return json({ items: [ITEM], total: 1 });
    if (path.startsWith("/roadmaps"))
      return json({ items: [ROADMAP], total: 1 });
    if (path.startsWith("/agents"))
      return json({
        items: [
          HELPER,
          REVOKED,
          { ...HELPER, id: registered.id, label: registered.label },
          { ...HELPER, id: claimant.id, label: claimant.label, openRuns: 2 },
        ],
        total: 4,
      });
    return json({ items: [], total: 0 });
  });
  return calls;
}
const ago = (iso: string | null) => (iso ? "an hour ago" : "");
function Sessions() {
  const [closed, setClosed] = useState(false);
  return (
    <RunningSessions
      refreshMs={60_000}
      ago={ago}
      closed={closed}
      toggle={() => setClosed(!closed)}
    />
  );
}
function show(node = <Sessions />) {
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
      {node}
    </QueryClientProvider>,
  );
}
const writes = (calls: Call[]) => calls.filter((c) => c.method !== "GET");

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  setDataContext("ties-test-" + crypto.randomUUID());
  window.location.hash = "";
});

it("offers the sessions running on this machine that nothing tracks yet", async () => {
  serve([docs, parser, revived, tracked]);
  show();
  const region = await screen.findByRole("region", {
    name: "Running on this machine",
  });
  expect(region).toHaveTextContent("3 Claude Code sessions nothing tracks yet");
  const list = within(region).getByRole("list", { name: "Sessions to track" });
  const rows = within(list)
    .getAllByRole("listitem")
    .filter((li) => li.parentElement === list);
  expect(rows.map((row) => row.querySelector("strong")?.textContent)).toEqual([
    "Docs pass",
    "Parser fix",
    "Release notes",
  ]);
  expect(rows[0]).toHaveTextContent("Waiting for permission prompt");
  expect(within(rows[0]).getByText("Terminal")).toBeInTheDocument();
  expect(within(rows[0]).getByText("docs-pass")).toHaveAttribute(
    "title",
    docs.cwd,
  );
  expect(rows[1]).toHaveTextContent("Working");
  expect(within(rows[1]).getByText("Claude desktop")).toBeInTheDocument();
  expect(within(rows[1]).getByText("claude-opus-5-5")).toBeInTheDocument();
  expect(rows[2]).toHaveTextContent("Idle, last active an hour ago");
  // Who works in it already, how each is tied, and which are tied under the session's other ID,
  // which a chat tracked by "session-parser" does not reach.
  const agents = within(rows[1]).getByRole("list", {
    name: "Agents tied to Parser fix",
  });
  expect(
    within(agents)
      .getAllByRole("listitem")
      .map((li) => li.textContent),
  ).toEqual([
    "Parser agent· registered with this chat",
    "Reviewer· confirmed by you· under another IDUntie",
    "Claimant· reports this chat, not confirmed· under another IDConfirm",
  ]);
  expect(within(agents).getAllByText("· under another ID")[0]).toHaveAttribute(
    "title",
    "It is tied under local_parser, but this session is tracked by session-parser, so what you write into its chat won't reach it.",
  );
  expect(
    within(agents).getByRole("link", { name: "Parser agent" }),
  ).toHaveAttribute("href", "#agents/agent-parser");
  expect(
    within(rows[2]).getByRole("link", { name: ITEM.title }),
  ).toHaveAttribute("href", "#work/" + ITEM.id);
  expect(rows[2]).toHaveTextContent("Archived chat on");
  expect(region).not.toHaveTextContent("Already tracked");
});

it("ties a session to a work item or a roadmap by the ID its agents use, then opens it", async () => {
  const user = userEvent.setup();
  const calls = serve([parser]);
  show();
  const ties = await screen.findByRole("group", { name: "Tie Parser fix" });
  await user.click(within(ties).getByRole("button", { name: "Tie work item" }));
  await user.click(
    await within(ties).findByRole("button", { name: /Import format/ }),
  );
  await waitFor(() => expect(window.location.hash).toBe("#chats/chat-new"));
  expect(writes(calls)).toEqual([
    {
      method: "POST",
      path: "/chats",
      body: {
        ownerType: "item",
        ownerId: ITEM.id,
        chatId: "session-parser",
        title: "Parser fix",
        provider: "Anthropic",
        model: "claude-opus-5-5",
      },
    },
  ]);
  await user.click(within(ties).getByRole("button", { name: "Tie roadmap" }));
  await user.click(
    await within(ties).findByRole("button", { name: "Platform" }),
  );
  await waitFor(() => expect(writes(calls)).toHaveLength(2));
  expect(writes(calls)[1].body).toMatchObject({
    ownerType: "roadmap",
    ownerId: ROADMAP.id,
    chatId: "session-parser",
  });
});

it("does not offer the record whose archived chat already tracks the session", async () => {
  const user = userEvent.setup();
  serve([revived]);
  show();
  const ties = await screen.findByRole("group", { name: "Tie Release notes" });
  await user.click(within(ties).getByRole("button", { name: "Tie work item" }));
  expect(
    await within(ties).findByText("Every match is already linked."),
  ).toBeInTheDocument();
});

it("ties, confirms and unties agents under the ID each one's runs report", async () => {
  const user = userEvent.setup();
  const calls = serve([parser]);
  show();
  const ties = await screen.findByRole("group", { name: "Tie Parser fix" });
  await user.click(within(ties).getByRole("button", { name: "Tie agent" }));
  const matches = await within(ties).findByRole("list", {
    name: "Agent to tie matches",
  });
  // Agents already bound and agents whose credential was revoked are not offered, and each
  // offer says what tells it apart, since agents choose their own labels.
  expect(
    within(matches)
      .getAllByRole("button")
      .map((b) => b.textContent),
  ).toEqual([
    "Helperclaude · 1 open run · id agent-he",
    "Claimantclaude · 2 open runs · id agent-cl",
  ]);
  await user.click(within(matches).getByRole("button", { name: /^Helper/ }));
  // Picked or confirmed on its row, an agent whose run reports the session is confirmed
  // under the ID that run reports, which delivery matches exactly.
  await user.click(within(ties).getByRole("button", { name: "Tie agent" }));
  await user.click(
    await within(ties).findByRole("button", { name: /^Claimant/ }),
  );
  await waitFor(() => expect(writes(calls)).toHaveLength(2));
  await user.click(
    screen.getByRole("button", {
      name: "Confirm Claimant works in this session",
    }),
  );
  await waitFor(() => expect(writes(calls)).toHaveLength(3));
  await user.click(screen.getByRole("button", { name: "Untie Reviewer" }));
  await waitFor(() => expect(writes(calls)).toHaveLength(4));
  expect(writes(calls)).toEqual([
    {
      method: "POST",
      path: "/chat-confirmations",
      body: { chatId: "session-parser", agentId: HELPER.id },
    },
    {
      method: "POST",
      path: "/chat-confirmations",
      body: { chatId: "LOCAL_PARSER", agentId: claimant.id },
    },
    {
      method: "POST",
      path: "/chat-confirmations",
      body: { chatId: "LOCAL_PARSER", agentId: claimant.id },
    },
    // A confirmation is taken back under the ID it was given, whichever of the session's IDs that was.
    {
      method: "POST",
      path: "/chat-confirmations/remove",
      body: { chatId: "local_parser", agentId: confirmed.id },
    },
  ]);
});

it("tells apart agents that go by the same label", async () => {
  const twin: ChatAgent = { ...registered, id: "agent-twin-2", chatId: "x" };
  serve([session("Twins", { agents: [registered, twin] })]);
  show();
  const agents = await screen.findByRole("list", {
    name: "Agents tied to Twins",
  });
  expect(
    within(agents)
      .getAllByRole("listitem")
      .map((li) => li.querySelector("small")?.textContent),
  ).toEqual([
    "· id agent-pa · registered with this chat",
    "· id agent-tw · registered with this chat",
  ]);
});

it("hides a session in this browser until shown again, and folds the list away", async () => {
  const user = userEvent.setup();
  serve([docs, parser]);
  const first = show();
  await user.click(
    await screen.findByRole("button", { name: "Hide Docs pass" }),
  );
  const list = screen.getByRole("list", { name: "Sessions to track" });
  expect(list).not.toHaveTextContent("Docs pass");
  first.unmount();

  // A hidden session missing from one reading of the list stays hidden.
  serve([parser]);
  const second = show();
  await user.click(
    await screen.findByRole("button", { name: "Hide Parser fix" }),
  );
  second.unmount();

  serve([docs, parser]);
  show();
  const again = await screen.findByRole("button", {
    name: "Show 2 hidden sessions",
  });
  expect(
    screen.queryByRole("list", { name: "Sessions to track" }),
  ).not.toBeInTheDocument();
  await user.click(again);
  expect(
    screen.getByRole("button", { name: "Leave hidden sessions out" }),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Unhide Docs pass" }));
  await user.click(screen.getByRole("button", { name: "Unhide Parser fix" }));
  expect(
    screen.getByRole("list", { name: "Sessions to track" }),
  ).toHaveTextContent("Docs pass");
  expect(
    screen.queryByRole("button", { name: /hidden session/ }),
  ).not.toBeInTheDocument();

  await user.click(
    screen.getByRole("button", { name: "Collapse running sessions" }),
  );
  expect(
    screen.queryByRole("list", { name: "Sessions to track" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Expand running sessions" }),
  ).toBeInTheDocument();
});

it("shows nothing while no session needs tracking", async () => {
  const calls = serve([tracked]);
  show();
  await waitFor(() =>
    expect(calls.some((c) => c.path === "/claude-sessions")).toBe(true),
  );
  expect(
    screen.queryByRole("region", { name: "Running on this machine" }),
  ).not.toBeInTheDocument();
});

it("names where a session runs and what it is doing", () => {
  expect(
    ["claude-desktop", "cli", "claude-vscode", "sdk-ts", "", "mcp"].map(runsIn),
  ).toEqual([
    "Claude desktop",
    "Terminal",
    "VS Code",
    "Agent SDK",
    "Claude Code",
    "mcp",
  ]);
  expect(sessionState(session("A", { status: "waiting" }), ago)).toBe(
    "Waiting for you",
  );
  expect(
    sessionState(session("A", { status: "idle", statusAt: null }), ago),
  ).toBe("Idle");
});

it("lists the agents tied to a tracked chat, and ties and unties them through the chat", async () => {
  const user = userEvent.setup();
  const calls = serve([]);
  const chat = {
    id: "chat-1",
    chatId: "session-parser",
    archived: false,
    agents: [registered, { ...confirmed, chatId: "session-parser" }, claimant],
  } as unknown as ChatOverview;
  const view = show(<ChatAgents chat={chat} />);
  const tied = screen.getByRole("list", { name: "Agents tied to this chat" });
  // A run that only claims the chat waits for confirmation where messages are sent.
  expect(
    within(tied)
      .getAllByRole("link")
      .map((a) => a.textContent),
  ).toEqual(["Parser agent", "Reviewer"]);
  await user.click(screen.getByRole("button", { name: "Tie agent" }));
  await user.click(await screen.findByRole("button", { name: /^Helper/ }));
  await waitFor(() => expect(writes(calls)).toHaveLength(1));
  await user.click(screen.getByRole("button", { name: "Untie Reviewer" }));
  await waitFor(() =>
    expect(writes(calls)).toEqual([
      {
        method: "POST",
        path: "/chats/chat-1/confirmations",
        body: { agentId: HELPER.id },
      },
      {
        method: "POST",
        path: "/chats/chat-1/confirmations/remove",
        body: { agentId: confirmed.id },
      },
    ]),
  );
  view.unmount();
  // An archived chat shows who was tied, and changes nothing until it is recovered.
  const archived = show(<ChatAgents chat={{ ...chat, archived: true }} />);
  expect(
    within(
      screen.getByRole("list", { name: "Agents tied to this chat" }),
    ).getAllByRole("link"),
  ).toHaveLength(2);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  archived.unmount();
  show(<ChatAgents chat={{ ...chat, archived: true, agents: [] }} />);
  expect(screen.getByText("None tied yet.")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Tie agent" }),
  ).not.toBeInTheDocument();
});
