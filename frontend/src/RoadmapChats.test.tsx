import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RoadmapDetail } from "./Roadmaps";
import type { ChatOverview, Roadmap, Transcript } from "./apiTypes";
import { aChat, aRoadmap, aStep } from "./fixtures";

const TOP = "22222222-2222-4222-8222-000000000000";
const ROADMAP = "22222222-2222-4222-8222-222222222222";
const SUB = "22222222-2222-4222-8222-333333333333";
const OTHER = "22222222-2222-4222-8222-444444444444";
const PARENT = "aaaaaaaa-0000-4000-8000-000000000001";
const NEW = "cccccccc-0000-4000-8000-0000000000ff";
const minutesAgo = (n: number) =>
  new Date(Date.now() - n * 60_000).toISOString();
const summary = (id: string, title: string) => ({ id, title, archived: false });

/** A tracked chat as /chats lists it: on this roadmap, idle and with no transcript, unless told otherwise. */
function listed(
  id: string,
  title: string,
  fields: Partial<ChatOverview> = {},
): ChatOverview {
  return {
    ...aChat({
      id,
      revision: 3,
      ownerType: "roadmap",
      ownerId: ROADMAP,
      chatId: "conv-" + id.slice(-2),
      title,
      createdDate: "2026-10-31",
    }),
    owner: { type: "roadmap", id: ROADMAP, title: "Plan", archived: false },
    roadmaps: [summary(TOP, "Platform"), summary(ROADMAP, "Plan")],
    steps: [],
    agents: [],
    transcript: {
      found: false,
      source: null,
      lastActivityAt: null,
      state: "idle",
      threads: [],
    },
    ...fields,
  };
}
// The roadmap's own chat, from before schema v24 with a label that names no linked repository.
const OWN = listed("cccccccc-0000-4000-8000-000000000001", "Build the API", {
  provider: "Anthropic",
  model: "claude-opus-5-5",
  repos: ["acme/web"],
  stepIds: [PARENT],
  steps: [{ id: PARENT, title: "Parent", status: "doing", retired: false }],
});
// A work item on the roadmap, its chat at work right now.
const ON_ITEM = listed(
  "cccccccc-0000-4000-8000-000000000002",
  "Refresh tokens",
  {
    ownerType: "item",
    ownerId: "bbbbbbbb-0000-4000-8000-000000000001",
    owner: {
      type: "item",
      id: "bbbbbbbb-0000-4000-8000-000000000001",
      title: "Token refresh",
      archived: false,
    },
    transcript: {
      found: true,
      source: "claude",
      lastActivityAt: minutesAgo(2),
      state: "working",
      threads: [],
    },
  },
);
const ON_SUB = listed("cccccccc-0000-4000-8000-000000000003", "Tune storage", {
  ownerId: SUB,
  owner: { type: "roadmap", id: SUB, title: "Storage", archived: false },
  roadmaps: [
    summary(TOP, "Platform"),
    summary(ROADMAP, "Plan"),
    summary(SUB, "Storage"),
  ],
  transcript: {
    found: true,
    source: "codex",
    lastActivityAt: minutesAgo(30),
    state: "idle",
    threads: [],
  },
});
// Neither another roadmap's chat, nor the parent roadmap's, nor one on no roadmap is this one's.
const ELSEWHERE = [
  listed("cccccccc-0000-4000-8000-000000000004", "Other plan chat", {
    ownerId: OTHER,
    owner: { type: "roadmap", id: OTHER, title: "Other", archived: false },
    roadmaps: [summary(OTHER, "Other")],
  }),
  listed("cccccccc-0000-4000-8000-000000000005", "Platform chat", {
    ownerId: TOP,
    owner: { type: "roadmap", id: TOP, title: "Platform", archived: false },
    roadmaps: [summary(TOP, "Platform")],
  }),
  listed("cccccccc-0000-4000-8000-000000000006", "Loose chat", {
    ownerType: "item",
    ownerId: "bbbbbbbb-0000-4000-8000-000000000009",
    owner: {
      type: "item",
      id: "bbbbbbbb-0000-4000-8000-000000000009",
      title: "Chores",
      archived: false,
    },
    roadmaps: [],
  }),
];
const road: Roadmap = aRoadmap({
  id: ROADMAP,
  title: "Plan",
  parentId: TOP,
  steps: [aStep({ id: PARENT, title: "Parent" })],
  chats: [OWN],
});

let served: Roadmap = road;
let writes: { url: string; method: string; body: unknown }[] = [];
function transcript(chat: ChatOverview): Transcript {
  return {
    ...chat.transcript,
    chat,
    total: chat.transcript.found ? 1 : 0,
    more: false,
    messages: chat.transcript.found
      ? [
          {
            id: "root:1",
            thread: "root",
            at: minutesAgo(30),
            kind: "assistant",
            author: "Codex",
            authorThread: "root",
            text: "Storage tuned",
          },
        ]
      : [],
  };
}
function respond(url: string, method: string): unknown {
  if (method === "POST" && url.endsWith("/api/v1/chats"))
    return { ...OWN, id: NEW, title: "Review the API" };
  if (method !== "GET") return OWN;
  if (url.endsWith("/roadmaps/" + ROADMAP)) return served;
  if (url.includes("/api/v1/chats?"))
    return { items: [OWN, ...ELSEWHERE, ON_SUB, ON_ITEM] };
  const read = url.match(/\/chats\/([^/]+)\/transcript/);
  if (read) {
    const chat = [OWN, ON_ITEM, ON_SUB].find((c) => c.id === read[1])!;
    return transcript(chat);
  }
  if (url.endsWith(`/roadmaps/${ROADMAP}/agents`))
    return { items: [], roster: { items: [], total: 0 } };
  return { items: [], total: 0 };
}
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("roadmap-chats-" + crypto.randomUUID());
  window.location.hash = "";
  served = road;
  writes = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method !== "GET")
      writes.push({ url, method, body: JSON.parse(String(init?.body)) });
    return Response.json(respond(url, method));
  });
});
const view = (chat?: string) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RoadmapDetail id={ROADMAP} step="chats" chat={chat} />
    </QueryClientProvider>,
  );
const chatList = () => screen.findByRole("region", { name: "Agent chats" });
const history = () => screen.findByRole("region", { name: "Chat history" });
const row = (list: HTMLElement, title: string) =>
  within(list).getByRole("link", { name: new RegExp("^" + title) })
    .parentElement as HTMLElement;

it("lists the chats on the roadmap, its work items and sub-roadmaps, working ones first", async () => {
  view();
  const list = await chatList();
  await within(list).findByText("Refresh tokens");
  const links = within(list)
    .getAllByRole("link")
    .filter((a) => a.classList.contains("chat-link"));
  expect(links.map((a) => a.querySelector("strong")?.textContent)).toEqual([
    "Refresh tokens",
    "Tune storage",
    "Build the API",
  ]);
  expect(links.map((a) => a.getAttribute("href"))).toEqual(
    [ON_ITEM, ON_SUB, OWN].map((c) => `#roadmaps/${ROADMAP}/chats/${c.id}`),
  );
  expect(within(list).getByText("3")).toHaveClass("count");
  for (const other of ELSEWHERE)
    expect(within(list).queryByText(other.title)).toBeNull();
  // A chat tracked elsewhere in the roadmap names its record; the roadmap's own don't repeat it.
  expect(
    within(row(list, "Refresh tokens")).getByRole("link", {
      name: "Token refresh",
    }),
  ).toHaveAttribute("href", "#work/" + ON_ITEM.ownerId);
  expect(
    within(row(list, "Tune storage")).getByRole("link", { name: "Storage" }),
  ).toHaveAttribute("href", "#roadmaps/" + SUB);
  const own = row(list, "Build the API");
  expect(within(own).queryByText(/^Roadmap/)).toBeNull();
  expect(within(own).getByText("Anthropic · claude-opus-5-5")).toBeVisible();
  expect(await within(own).findByText("acme/web · Not linked")).toBeVisible();
  expect(within(own).getByRole("link", { name: "Parent" })).toHaveAttribute(
    "href",
    `#roadmaps/${ROADMAP}/${PARENT}`,
  );
  // With none chosen, the most recently active chat opens, and only the roadmap's own are edited here.
  const pane = await history();
  expect(
    within(pane).getByRole("heading", { name: "Refresh tokens", level: 2 }),
  ).toBeVisible();
  expect(links[0]).toHaveAttribute("aria-current", "true");
  expect(within(pane).queryByRole("button", { name: /^Edit chat/ })).toBeNull();
  expect(
    within(screen.getByRole("navigation", { name: "Roadmap views" })).getByRole(
      "link",
      { name: "Chats" },
    ),
  ).toHaveAttribute("aria-current", "page");
});

it("opens the addressed chat with its transcript", async () => {
  view(ON_SUB.id);
  const pane = await history();
  expect(
    await within(pane).findByRole("heading", {
      name: "Tune storage",
      level: 2,
    }),
  ).toBeVisible();
  expect(await within(pane).findByText("Storage tuned")).toBeVisible();
  const list = await chatList();
  expect(
    within(list).getByRole("link", { name: /^Tune storage/ }),
  ).toHaveAttribute("aria-current", "true");
});

it("says so when the addressed chat isn't one of the roadmap's", async () => {
  view(ELSEWHERE[0].id);
  await within(await chatList()).findByText("Refresh tokens");
  expect(
    within(await history()).getByText(/not among this roadmap's chats/),
  ).toBeVisible();
});

it("tracks a chat on the roadmap and opens it", async () => {
  view();
  const list = await chatList();
  await userEvent.type(
    within(list).getByRole("textbox", { name: "Agent chat title" }),
    "Review the API",
  );
  await userEvent.type(
    within(list).getByRole("textbox", { name: "Agent chat ID" }),
    "a950ec9c",
  );
  await userEvent.click(within(list).getByRole("button", { name: "Add chat" }));
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
        model: "",
      },
    }),
  );
  await waitFor(() =>
    expect(window.location.hash).toBe(`#roadmaps/${ROADMAP}/chats/${NEW}`),
  );
});

it("edits the roadmap's own chat in place, leaving its labels alone, and archives it", async () => {
  view(OWN.id);
  const pane = await history();
  expect(
    await within(pane).findByText(
      /No transcript on this machine has this chat ID/,
    ),
  ).toBeVisible();
  expect(
    within(pane).getByRole("button", { name: "Copy ID of Build the API" }),
  ).toBeVisible();
  await userEvent.click(
    within(pane).getByRole("button", { name: "Edit chat Build the API" }),
  );
  const model = within(pane).getByRole("combobox", {
    name: "Edited chat model",
  });
  expect(model).toHaveValue("claude-opus-5-5");
  await userEvent.clear(model);
  await userEvent.type(model, "claude-sonnet-5-5");
  // Labels from before schema v24 are read only: the form has no field for them.
  expect(
    within(pane).queryByRole("textbox", { name: "Edited chat repositories" }),
  ).toBeNull();
  await userEvent.click(
    within(pane).getByRole("button", { name: "Save chat" }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: "/api/v1/chats/" + OWN.id,
      method: "PATCH",
      body: {
        revision: 3,
        title: "Build the API",
        chatId: OWN.chatId,
        provider: "Anthropic",
        model: "claude-sonnet-5-5",
        createdDate: "2026-10-31",
      },
    }),
  );
  window.location.hash = `#roadmaps/${ROADMAP}/chats/${OWN.id}`;
  await userEvent.click(
    await within(pane).findByRole("button", {
      name: "Archive chat Build the API",
    }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: `/api/v1/chats/${OWN.id}/archive`,
      method: "POST",
      body: { revision: 3 },
    }),
  );
  // Back to the roadmap's chats, where the most recent one opens.
  await waitFor(() =>
    expect(window.location.hash).toBe(`#roadmaps/${ROADMAP}/chats`),
  );
});

it("keeps an archived roadmap's chats read only, with its archived chats to recover", async () => {
  const old = aChat({
    id: "cccccccc-0000-4000-8000-000000000007",
    revision: 2,
    ownerType: "roadmap",
    ownerId: ROADMAP,
    title: "Old lead",
    archived: true,
  });
  served = { ...road, archived: true, chats: [OWN, old] };
  view(OWN.id);
  const pane = await history();
  expect(
    await within(pane).findByRole("button", {
      name: "Copy ID of Build the API",
    }),
  ).toBeVisible();
  expect(within(pane).queryByRole("button", { name: /^Edit chat/ })).toBeNull();
  expect(
    within(pane).queryByRole("button", { name: /^Archive chat/ }),
  ).toBeNull();
  const list = await chatList();
  expect(
    within(list).queryByRole("heading", { name: "Track a chat" }),
  ).toBeNull();
  expect(
    within(list).queryByRole("textbox", { name: "Agent chat title" }),
  ).toBeNull();
  await userEvent.click(
    within(list).getByText("Archived supporting records (1)"),
  );
  expect(within(list).getByText("Old lead")).toBeVisible();
  await userEvent.click(within(list).getByRole("button", { name: "Recover" }));
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: `/api/v1/chats/${old.id}/recover`,
      method: "POST",
      body: { revision: 2 },
    }),
  );
});
