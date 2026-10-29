import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { People, PersonDetailView } from "./People";
import {
  AgentChatsSection,
  GithubRefsSection,
  LinksSection,
  PeopleLinks,
} from "./components";
import type { Item, Person, PersonDetail, Roadmap } from "./types";

const PERSON_ID = "33333333-3333-4333-8333-333333333333";
const ROADMAP_ID = "22222222-2222-4222-8222-222222222222";
const ITEM_ID = "11111111-1111-4111-8111-111111111111";

const samplePerson: Person = {
  id: PERSON_ID,
  revision: 1,
  name: "Alice Rivera",
  role: "Staff Engineer",
  notes: "Platform lead",
  roadmapCount: 1,
  workItemCount: 1,
};

const sampleRoadmap: Roadmap = {
  id: ROADMAP_ID,
  revision: 1,
  title: "Reliability roadmap",
  overview: "",
  steps: [],
  versions: [],
  notes: [],
  attachments: [],
  history: [],
};

const sampleItem: Item = {
  id: ITEM_ID,
  revision: 1,
  title: "Define SLOs",
  description: "",
  status: "open",
  adoUrl: null,
  roadmapId: ROADMAP_ID,
  stepId: null,
};

beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("people-test-" + crypto.randomUUID());
});

it("lists people in the directory and creates a new person", async () => {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && url.includes("/people"))
      return new Response(
        JSON.stringify({ items: [samplePerson], total: 1 }),
        { status: 200 },
      );
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(
      JSON.stringify({ ...samplePerson, id: "new-person", name: "Bob Chen" }),
      { status: 200 },
    );
  });

  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <People />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("Alice Rivera")).toBeInTheDocument();
  expect(
    screen.getByText(/Staff Engineer · 1 roadmaps · 1 work items/),
  ).toBeInTheDocument();

  await userEvent.type(
    screen.getByRole("textbox", { name: "Person name" }),
    "Bob Chen",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Person title or team" }),
    "Product Manager",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add person" }));

  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/people",
      method: "POST",
      body: { name: "Bob Chen", role: "Product Manager", notes: "" },
    }),
  );
});

it("displays person detail and ties/unlinks roadmaps and work items with roles", async () => {
  const detail: PersonDetail = {
    ...samplePerson,
    roadmaps: [
      {
        linkId: "link-r1",
        personId: PERSON_ID,
        roadmapId: ROADMAP_ID,
        relationship: "Owner",
        createdAt: "2026-10-29T00:00:00Z",
        roadmap: {
          id: ROADMAP_ID,
          title: "Reliability roadmap",
          overview: "",
          archived: false,
          revision: 1,
        },
      },
    ],
    workItems: [
      {
        linkId: "link-w1",
        personId: PERSON_ID,
        itemId: ITEM_ID,
        relationship: "Reviewer",
        createdAt: "2026-10-29T00:00:00Z",
        item: sampleItem,
      },
    ],
    history: [],
  };
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && url.includes(`/people/${PERSON_ID}`))
      return new Response(JSON.stringify(detail), { status: 200 });
    if (method === "GET" && url.includes("/roadmaps"))
      return new Response(
        JSON.stringify({ items: [sampleRoadmap], total: 1 }),
        { status: 200 },
      );
    if (method === "GET" && url.includes("/work-items"))
      return new Response(JSON.stringify({ items: [sampleItem], total: 1 }), {
        status: 200,
      });
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <PersonDetailView id={PERSON_ID} />
    </QueryClientProvider>,
  );

  expect(
    await screen.findByRole("link", { name: "Reliability roadmap" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("textbox", { name: "Role on Reliability roadmap" }),
  ).toHaveValue("Owner");
  expect(
    screen.getByRole("textbox", { name: "Role on Define SLOs" }),
  ).toHaveValue("Reviewer");

  // Tie work item with a role
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Choose work item to tie" }),
    ITEM_ID,
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Work item relationship or role" }),
    "Owner",
  );
  await userEvent.click(screen.getByRole("button", { name: "Tie work item" }));

  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/person-links",
      method: "POST",
      body: {
        personId: PERSON_ID,
        targetType: "item",
        targetId: ITEM_ID,
        relationship: "Owner",
      },
    }),
  );

  // Unlink roadmap
  await userEvent.click(
    screen.getByRole("button", { name: "Unlink Reliability roadmap" }),
  );
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/person-links/unlink",
      method: "POST",
      body: {
        personId: PERSON_ID,
        targetType: "roadmap",
        targetId: ROADMAP_ID,
      },
    }),
  );
});

it("ties an existing or new person to a target via PeopleLinks", async () => {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && url.includes("/people"))
      return new Response(
        JSON.stringify({ items: [samplePerson], total: 1 }),
        { status: 200 },
      );
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(JSON.stringify({ id: PERSON_ID, revision: 1 }), {
      status: 200,
    });
  });

  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <PeopleLinks targetType="roadmap" targetId={ROADMAP_ID} people={[]} />
    </QueryClientProvider>,
  );

  expect(
    await screen.findByRole("option", { name: /Alice Rivera/ }),
  ).toBeInTheDocument();
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Choose person" }),
    PERSON_ID,
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Relationship or role" }),
    "Stakeholder",
  );
  await userEvent.click(screen.getByRole("button", { name: "Tie person" }));

  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/person-links",
      method: "POST",
      body: {
        personId: PERSON_ID,
        targetType: "roadmap",
        targetId: ROADMAP_ID,
        relationship: "Stakeholder",
      },
    }),
  );
});

it("adds and renders web links, agent chats, and GitHub commits/PRs", async () => {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(JSON.stringify({ id: "created", revision: 1 }), {
      status: 200,
    });
  });

  const existingChat = {
    id: "chat-row-1",
    revision: 1,
    ownerType: "item",
    ownerId: ITEM_ID,
    chatId: "conv-12345",
    title: "Debug SLO metrics",
    createdDate: "2026-10-29",
  };

  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <LinksSection ownerType="item" ownerId={ITEM_ID} links={[]} />
      <AgentChatsSection
        ownerType="item"
        ownerId={ITEM_ID}
        chats={[existingChat]}
      />
      <GithubRefsSection
        ownerType="item"
        ownerId={ITEM_ID}
        github={[]}
        chats={[existingChat]}
      />
    </QueryClientProvider>,
  );

  // Add a web link with description
  await userEvent.type(
    screen.getByRole("textbox", { name: "Link title" }),
    "Architecture RFC",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Link URL" }),
    "https://example.com/rfc",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Link description" }),
    "Core design notes",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add link" }));

  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/links",
      method: "POST",
      body: {
        ownerType: "item",
        ownerId: ITEM_ID,
        title: "Architecture RFC",
        url: "https://example.com/rfc",
        description: "Core design notes",
      },
    }),
  );

  // Add an agent chat
  await userEvent.type(
    screen.getByRole("textbox", { name: "Agent chat title" }),
    "Implement retry logic",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Agent chat ID" }),
    "conv-999",
  );
  await userEvent.type(
    screen.getByLabelText("Agent chat date created"),
    "2026-10-29",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add chat" }));

  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/chats",
      method: "POST",
      body: {
        ownerType: "item",
        ownerId: ITEM_ID,
        title: "Implement retry logic",
        chatId: "conv-999",
        createdDate: "2026-10-29",
      },
    }),
  );

  // Add a GitHub PR associated with the agent chat
  await userEvent.type(
    screen.getByRole("textbox", { name: "GitHub URL" }),
    "https://github.com/acme/worklog/pull/42",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "GitHub title" }),
    "Add retry policy",
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Associated agent chat" }),
    existingChat.id,
  );
  await userEvent.click(screen.getByRole("button", { name: "Add GitHub ref" }));

  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/github",
      method: "POST",
      body: {
        ownerType: "item",
        ownerId: ITEM_ID,
        kind: "pr",
        url: "https://github.com/acme/worklog/pull/42",
        title: "Add retry policy",
        reference: "",
        description: "",
        chatId: existingChat.id,
      },
    }),
  );
});
