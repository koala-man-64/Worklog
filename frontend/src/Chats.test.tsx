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
import { Chats, ago, tallyText } from "./Chats";
import type { ChatOverview, ChatThread, Transcript } from "./types";
import type { Outbox } from "./outboxTypes";

const ROADMAP_ID = "22222222-2222-4222-8222-222222222222";
const API_ID = "33333333-3333-4333-8333-333333333333";
const ITEM_ID = "11111111-1111-4111-8111-111111111111";
const TOKEN_ID = "44444444-4444-4444-8444-444444444444";
const PLATFORM = { id: ROADMAP_ID, title: "Platform", archived: false };
const minutesAgo = (n: number) =>
  new Date(Date.now() - n * 60_000).toISOString();

const root: ChatThread = {
  key: "root",
  name: "Codex",
  title: "Release review",
  task: "",
  parent: null,
  model: "gpt-6.1-sol",
  state: "working",
  lastActivityAt: minutesAgo(2),
};
const lorentz: ChatThread = {
  ...root,
  key: "lorentz",
  name: "Lorentz",
  title: "",
  task: "pipeline_check",
  parent: "root",
};
function overview(
  id: string,
  title: string,
  fields: Partial<ChatOverview> = {},
): ChatOverview {
  return {
    id,
    revision: 1,
    ownerType: "roadmap",
    ownerId: ROADMAP_ID,
    chatId: id + "-chat",
    title,
    createdDate: "2026-11-01",
    provider: "OpenAI",
    model: "gpt-6.1-sol",
    repos: [],
    stepIds: [],
    owner: {
      type: "roadmap",
      id: ROADMAP_ID,
      title: "Platform",
      archived: false,
    },
    roadmaps: [PLATFORM],
    steps: [],
    transcript: {
      found: true,
      source: "codex",
      lastActivityAt: minutesAgo(90),
      state: "idle",
      threads: [],
    },
    ...fields,
  };
}
const release = overview("release", "Release review", {
  steps: [{ id: "step-1", title: "Ship 3.2", status: "doing", retired: false }],
  transcript: {
    found: true,
    source: "codex",
    lastActivityAt: minutesAgo(2),
    state: "working",
    threads: [root, lorentz],
  },
});
const docs = overview("docs", "Write the docs", {
  ownerType: "item",
  ownerId: ITEM_ID,
  provider: "Anthropic",
  model: "claude-opus-5-5",
  owner: { type: "item", id: ITEM_ID, title: "Docs refresh", archived: false },
  roadmaps: [],
});
// A work item on the API roadmap, which sits within Platform.
const token = overview("token", "Refresh tokens", {
  ownerType: "item",
  ownerId: TOKEN_ID,
  owner: {
    type: "item",
    id: TOKEN_ID,
    title: "Token refresh",
    archived: false,
  },
  roadmaps: [PLATFORM, { id: API_ID, title: "API", archived: false }],
  transcript: {
    found: true,
    source: "claude",
    lastActivityAt: minutesAgo(30),
    state: "idle",
    threads: [],
  },
});
const lost = overview("lost", "Old chat", {
  transcript: {
    found: false,
    source: null,
    lastActivityAt: null,
    state: "idle",
    threads: [],
  },
});

function transcript(fields: Partial<Transcript> = {}): Transcript {
  return {
    ...release.transcript,
    chat: release,
    total: 4,
    more: false,
    messages: [
      {
        id: "root:1",
        thread: "root",
        at: minutesAgo(9),
        kind: "user",
        author: "You",
        text: "Review the **release**",
      },
      {
        id: "root:2",
        thread: "root",
        at: minutesAgo(8),
        kind: "tools",
        author: "Codex",
        authorThread: "root",
        count: 3,
        tools: [
          { name: "exec", summary: "git status" },
          { name: "exec", summary: "pytest" },
          { name: "spawn_agent", summary: "Check the pipeline" },
        ],
      },
      {
        id: "lorentz:4",
        thread: "lorentz",
        at: minutesAgo(5),
        kind: "agent",
        author: "Lorentz",
        authorThread: "lorentz",
        recipient: "Codex",
        text: "Pipeline is green",
      },
      {
        id: "root:9",
        thread: "root",
        at: minutesAgo(2),
        kind: "assistant",
        author: "Codex",
        authorThread: "root",
        text: "Release looks good",
        truncated: true,
      },
    ],
    ...fields,
  };
}

const noMessages: Outbox = {
  chat: { id: "release", chatId: "release-chat", archived: false },
  recipients: [],
  items: [],
  total: 0,
  limit: 20,
  offset: 0,
};
function serve(
  respond: (url: string) => unknown = (url) =>
    url.includes("/transcript")
      ? transcript()
      : { items: [docs, lost, release] },
  selected?: string,
) {
  const urls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    urls.push(url);
    // An open chat also asks who a message to it would reach.
    const body = /\/chats\/[^/]+\/messages/.test(url)
      ? noMessages
      : respond(url);
    return new Response(JSON.stringify(body), { status: 200 });
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Chats selected={selected} />
    </QueryClientProvider>,
  );
  return urls;
}

beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("chats-test-" + crypto.randomUUID());
});

it("lists who is working on what, working chats first", async () => {
  serve();
  const list = await screen.findByRole("region", {
    name: "Who is working on what",
  });
  const rows = await within(list).findAllByRole("listitem");
  const titles = within(list)
    .getAllByRole("link")
    .filter((a) => a.getAttribute("href")?.startsWith("#chats/"))
    .map((a) => a.textContent);
  expect(titles[0]).toMatch(/^Release review/);
  expect(titles.slice(1).map((t) => t?.split(/Idle|No /)[0])).toEqual([
    "Write the docs",
    "Old chat",
  ]);
  const first = rows[0];
  expect(
    within(first).getByText(/Working, active 2 minutes ago/),
  ).toBeInTheDocument();
  expect(within(first).getByRole("link", { name: "Ship 3.2" })).toHaveAttribute(
    "href",
    `#roadmaps/${ROADMAP_ID}/step-1`,
  );
  expect(
    within(first).getByRole("list", { name: "Agents in Release review" }),
  ).toHaveTextContent("Lorentz · pipeline_check");
  expect(screen.getByRole("link", { name: "Docs refresh" })).toHaveAttribute(
    "href",
    "#work/" + ITEM_ID,
  );
  expect(screen.getByText("No transcript on this machine")).toBeInTheDocument();
});

it("opens the most recent chat and shows every message sent", async () => {
  const urls = serve();
  const history = await screen.findByRole("region", { name: "Chat history" });
  const messages = await within(history).findByRole("list", {
    name: "Messages",
  });
  expect(urls).toContain("/api/v1/chats/release/transcript?limit=200");
  const items = within(messages).getAllByRole("listitem", { hidden: false });
  expect(items[0]).toHaveTextContent("You");
  expect(within(items[0]).getByText("release").tagName).toBe("STRONG");
  expect(items[1]).toHaveTextContent("Codex ran 3 tools: exec ×2, spawn_agent");
  expect(
    items.find((li) => li.textContent?.includes("Pipeline is green")),
  ).toHaveTextContent(/Lorentz\s*to\s*Codex/);
  expect(within(history).getByText(/Shortened here/)).toBeInTheDocument();
  expect(
    within(history).getByText(/Codex thread, working/),
  ).toBeInTheDocument();
});

it("shows one agent's messages and earlier pages on request", async () => {
  const urls = serve((url) =>
    url.includes("/transcript")
      ? transcript({ more: !url.includes("limit=400") })
      : { items: [release] },
  );
  await screen.findByRole("list", { name: "Messages" });
  await userEvent.click(
    screen.getByRole("button", { name: /Lorentz · pipeline_check/ }),
  );
  await waitFor(() =>
    expect(urls).toContain(
      "/api/v1/chats/release/transcript?limit=200&thread=lorentz",
    ),
  );
  expect(
    screen.getByRole("button", { name: /Lorentz · pipeline_check/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(
    await screen.findByRole("button", { name: "Show earlier messages" }),
  );
  await waitFor(() =>
    expect(urls).toContain(
      "/api/v1/chats/release/transcript?limit=400&thread=lorentz",
    ),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Show earlier messages" }),
    ).not.toBeInTheDocument(),
  );
});

it("says when no transcript on this machine has the chat ID", async () => {
  serve((url) =>
    url.includes("/transcript")
      ? {
          ...transcript(),
          ...lost.transcript,
          chat: lost,
          messages: [],
          total: 0,
        }
      : { items: [lost] },
  );
  expect(
    await screen.findByText(/No transcript on this machine has this chat ID/),
  ).toBeInTheDocument();
});

it("says when Codex kept a message between agents only encrypted", async () => {
  const agent = {
    thread: "lorentz",
    kind: "agent" as const,
    author: "Codex",
    authorThread: "root",
    recipient: "Lorentz",
  };
  serve((url) =>
    url.includes("/transcript")
      ? transcript({
          messages: [
            {
              ...agent,
              id: "lorentz:1",
              at: minutesAgo(3),
              text: "",
              messageType: "NEW_TASK",
              encrypted: true,
            },
            {
              ...agent,
              id: "lorentz:2",
              at: minutesAgo(2),
              text: "",
              messageType: "MESSAGE",
              encrypted: true,
            },
            {
              ...agent,
              id: "root:3",
              thread: "root",
              at: minutesAgo(1),
              author: "Lorentz",
              authorThread: "lorentz",
              recipient: "Codex",
              text: "Pipeline is **green**",
              messageType: "FINAL_ANSWER",
            },
          ],
        })
      : { items: [release] },
  );
  const messages = await screen.findByRole("list", { name: "Messages" });
  const [task, message, answer] = within(messages).getAllByRole("listitem");
  expect(task).toHaveTextContent(/Codex\s*to\s*Lorentz\s*· new task/);
  expect(task).toHaveTextContent("Encrypted by Codex");
  expect(message).toHaveTextContent("Encrypted by Codex");
  expect(message).not.toHaveTextContent(/·/);
  expect(answer).toHaveTextContent("· final answer");
  expect(within(answer).getByText("green").tagName).toBe("STRONG");
  expect(answer).not.toHaveTextContent("Encrypted");
});

it("describes times relative to now", () => {
  const now = Date.parse("2026-11-01T12:00:00Z");
  expect(ago("2026-11-01T11:59:40Z", now)).toBe("just now");
  expect(ago("2026-11-01T11:55:00Z", now)).toBe("5 minutes ago");
  expect(ago("2026-11-01T09:00:00Z", now)).toBe("3 hours ago");
  expect(ago("2026-10-31T12:00:00Z", now)).toBe("yesterday");
  expect(ago(null, now)).toBe("");
});

it("keeps links in transcripts from opening Worklog's own pages", async () => {
  serve((url) =>
    url.includes("/transcript")
      ? transcript({
          messages: [
            {
              id: "root:1",
              thread: "root",
              at: minutesAgo(1),
              kind: "assistant",
              author: "Codex",
              text: "Get the [backup](/api/v1/export) or read [the docs](https://example.com/docs)",
            },
          ],
        })
      : { items: [release] },
  );
  const docsLink = await screen.findByText("the docs");
  expect(docsLink.closest("a")).toHaveAttribute(
    "href",
    "https://example.com/docs",
  );
  expect(docsLink.closest("a")).toHaveAttribute("target", "_blank");
  expect(screen.getByText("backup").closest("a")).not.toHaveAttribute("href");
});

it("pages back no further than the server returns, then says what is not shown", async () => {
  const urls = serve((url) =>
    url.includes("/transcript")
      ? transcript({ more: true, total: 5000 })
      : { items: [release] },
  );
  await screen.findByRole("list", { name: "Messages" });
  for (let limit = 400; limit <= 2000; limit += 200) {
    await userEvent.click(
      await screen.findByRole("button", { name: "Show earlier messages" }),
    );
    await waitFor(() =>
      expect(urls).toContain(`/api/v1/chats/release/transcript?limit=${limit}`),
    );
  }
  expect(
    await screen.findByText(
      "4996 earlier messages are in the transcript but not shown here.",
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Show earlier messages" }),
  ).not.toBeInTheDocument();
  const limits = urls
    .filter((u) => u.includes("/transcript"))
    .map((u) => Number(new URL(u, "http://local").searchParams.get("limit")));
  expect(Math.max(...limits)).toBe(2000);
});

const everyone = (url: string) =>
  url.includes("/transcript")
    ? transcript()
    : { items: [docs, lost, release, token] };
const header = (list: HTMLElement) =>
  list.parentElement!.querySelector(".chat-group-head")!;

it("gathers chats under their roadmaps and work items with activity at each level", async () => {
  serve(everyone);
  await userEvent.click(await screen.findByRole("button", { name: "Roadmap" }));
  const platform = await screen.findByRole("list", {
    name: "Chats in Platform",
  });
  expect(header(platform)).toHaveTextContent(
    "Roadmap · 1 of 3 chats working, active 2 minutes ago",
  );
  // A sub-roadmap and its work items come before the roadmap's own chats.
  const inPlatform = [...platform.children].map(
    (li) => li.querySelector("a")?.textContent,
  );
  expect(inPlatform[0]).toBe("API");
  expect(
    inPlatform.slice(1).map((t) => t?.split(/Working|Idle|No /)[0]),
  ).toEqual(["Release review", "Old chat"]);
  const api = screen.getByRole("list", { name: "Chats in API" });
  expect(header(api)).toHaveTextContent(
    "Roadmap · 1 chat, last active 30 minutes ago",
  );
  expect(
    within(
      screen.getByRole("list", { name: "Chats in Token refresh" }),
    ).getByRole("link", { name: /Refresh tokens/ }),
  ).toHaveAttribute("href", "#chats/token");
  const loose = screen.getByRole("list", { name: "Chats in Not on a roadmap" });
  expect(header(loose)).toHaveTextContent(
    /^Not on a roadmap1 chat, last active/,
  );
  expect(
    within(loose).getByRole("link", { name: "Docs refresh" }),
  ).toHaveAttribute("href", "#work/" + ITEM_ID);
  // Under its group a chat does not name its record again.
  expect(screen.getAllByRole("link", { name: "Platform" })).toHaveLength(1);

  // Collapsed, a group keeps its summary and hides what is inside it.
  await userEvent.click(
    screen.getByRole("button", { name: "Collapse Platform" }),
  );
  expect(screen.queryByRole("list", { name: "Chats in Platform" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Expand Platform" }),
  ).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText(/1 of 3 chats working/)).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Expand Platform" }),
  );
  expect(screen.getByRole("button", { name: "Expand all" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  expect(screen.queryByRole("list", { name: /^Chats in/ })).toBeNull();
  expect(screen.getByRole("button", { name: "Collapse all" })).toBeDisabled();
  await userEvent.click(
    screen.getByRole("button", { name: "Expand Platform" }),
  );
  // Opening a roadmap shows what is within it, still summed up.
  expect(
    screen.getByRole("button", { name: "Expand API" }),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Expand all" }));
  expect(screen.getAllByRole("list", { name: /^Chats in/ })).toHaveLength(5);
});

it("shows activity by work item, keeping the chosen level", async () => {
  serve(everyone);
  await userEvent.click(
    await screen.findByRole("button", { name: "Work item" }),
  );
  const heads = [
    ...screen
      .getByRole("region", { name: "Who is working on what" })
      .querySelectorAll(".chat-group-head"),
  ].map((h) => h.textContent);
  expect(heads[0]).toBe(
    "PlatformRoadmap · 1 of 2 chats working, active 2 minutes ago",
  );
  expect(heads[1]).toBe(
    "Token refreshWork item in Platform › API · 1 chat, last active 30 minutes ago",
  );
  expect(heads[2]).toMatch(/^Docs refreshWork item · 1 chat, last active/);
  expect(heads).toHaveLength(3);

  cleanup();
  serve(everyone);
  expect(
    await screen.findByRole("button", { name: "Work item" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    await screen.findByRole("list", { name: "Chats in Token refresh" }),
  ).toBeInTheDocument();
});

it("opens the groups that hold a chat opened from elsewhere", async () => {
  serve(everyone);
  await userEvent.click(await screen.findByRole("button", { name: "Roadmap" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Collapse all" }),
  );
  cleanup();
  serve(everyone, "token");
  const item = await screen.findByRole("list", {
    name: "Chats in Token refresh",
  });
  expect(
    within(item).getByRole("link", { name: /Refresh tokens/ }),
  ).toHaveAttribute("aria-current", "true");
  // Groups that do not hold it stay as they were.
  expect(
    screen.queryByRole("list", { name: "Chats in Not on a roadmap" }),
  ).toBeNull();
});

it("sums up a group's chats in words", () => {
  const now = new Date().toISOString();
  const tally = (
    total: number,
    working: number,
    found: number,
    at: string | null,
  ) => tallyText({ total, working, found, lastActivityAt: at });
  expect(tally(1, 1, 1, now)).toBe("1 chat working, active just now");
  expect(tally(4, 2, 4, now)).toBe("2 of 4 chats working, active just now");
  expect(tally(3, 0, 3, now)).toBe("3 chats, last active just now");
  expect(tally(2, 0, 2, null)).toBe("2 chats");
  expect(tally(1, 0, 0, null)).toBe("1 chat, no transcript on this machine");
  expect(tally(3, 0, 0, null)).toBe("3 chats, no transcripts on this machine");
});
