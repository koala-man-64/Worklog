import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Chats, ago } from "./Chats";
import type { ChatOverview, ChatThread, Transcript } from "./types";

const ROADMAP_ID = "22222222-2222-4222-8222-222222222222";
const ITEM_ID = "11111111-1111-4111-8111-111111111111";
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

function serve(
  respond: (url: string) => unknown = (url) =>
    url.includes("/transcript")
      ? transcript()
      : { items: [docs, lost, release] },
) {
  const urls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    urls.push(url);
    return new Response(JSON.stringify(respond(url)), { status: 200 });
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Chats />
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

it("describes times relative to now", () => {
  const now = Date.parse("2026-11-01T12:00:00Z");
  expect(ago("2026-11-01T11:59:40Z", now)).toBe("just now");
  expect(ago("2026-11-01T11:55:00Z", now)).toBe("5 minutes ago");
  expect(ago("2026-11-01T09:00:00Z", now)).toBe("3 hours ago");
  expect(ago("2026-10-31T12:00:00Z", now)).toBe("yesterday");
  expect(ago(null, now)).toBe("");
});
