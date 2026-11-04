import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { setDataContext } from "./api";
import { People, PersonDetailView, telephone } from "./People";
import {
  AgentChatsSection,
  GithubRefsSection,
  LinksSection,
  PeopleLinks,
} from "./components";
import type { Repository } from "./repositoryTypes";
import type { Item, Person, PersonDetail, Roadmap } from "./types";

const PERSON_ID = "33333333-3333-4333-8333-333333333333";
const ROADMAP_ID = "22222222-2222-4222-8222-222222222222";
const OTHER_ROADMAP_ID = "22222222-2222-4222-8222-000000000002";
const ITEM_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_ITEM_ID = "11111111-1111-4111-8111-000000000002";
const REPO_ID = "44444444-4444-4444-8444-444444444444";
const OTHER_REPO_ID = "44444444-4444-4444-8444-000000000002";

const samplePerson: Person = {
  id: PERSON_ID,
  revision: 3,
  name: "Alice Rivera",
  title: "Staff Engineer",
  team: "Platform",
  description: "Owns the ingest pipeline; ask her about Kafka.",
  email: "alice@contoso.com",
  phone: "+1 (312) 555-0100 ext. 12",
  chat: "@alice on Slack",
  location: "Chicago (US Central)",
  notes: "Platform lead",
  roadmapCount: 1,
  workItemCount: 2,
  repositoryCount: 1,
};

const sampleRoadmap = (id: string, title: string): Roadmap => ({
  id,
  revision: 1,
  title,
  overview: "",
  steps: [],
  versions: [],
  notes: [],
  attachments: [],
  history: [],
});

const sampleItem = (id: string, title: string): Item => ({
  id,
  revision: 1,
  title,
  description: "",
  status: "open",
  adoUrl: null,
  roadmapId: ROADMAP_ID,
  stepId: null,
});

const sampleRepository = (id: string, name: string): Repository => ({
  id,
  revision: 1,
  name,
  url: `https://github.com/acme/${name}`,
  remoteIdentity: `github.com/acme/${name}`,
  duplicateOf: null,
  mergedInto: null,
  description: "",
  registeredBy: null,
  reviewed: true,
  archived: false,
});

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
      item: sampleItem(ITEM_ID, "Define SLOs"),
    },
  ],
  repositories: [
    {
      linkId: "link-p1",
      personId: PERSON_ID,
      repositoryId: REPO_ID,
      relationship: "Maintainer",
      createdAt: "2026-10-29T00:00:00Z",
      repository: sampleRepository(REPO_ID, "ingest"),
    },
  ],
  history: [],
};

type Call = { url: string; method: string; body: unknown };

/** Answers each GET from `reads` by the first key its URL contains, and records every write. */
function serve(reads: [string, unknown][]) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET") {
      const found = reads.find(([key]) => url.includes(key));
      return new Response(
        JSON.stringify(found ? found[1] : { items: [], total: 0 }),
        { status: 200 },
      );
    }
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(JSON.stringify({ id: "written", revision: 4 }), {
      status: 200,
    });
  });
  return calls;
}

function show(ui: ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {ui}
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("people-test-" + crypto.randomUUID());
});

it("lists people with their title, team, description and how to reach them, and adds one", async () => {
  const calls = serve([["/people", { items: [samplePerson], total: 1 }]]);
  show(<People />);

  expect(await screen.findByText("Alice Rivera")).toBeInTheDocument();
  expect(
    screen.getByText(
      "Staff Engineer · Platform · 2 work items · 1 roadmap · 1 repository",
    ),
  ).toBeInTheDocument();
  expect(screen.getByText(samplePerson.description)).toBeInTheDocument();
  const reach = screen.getByLabelText("How to reach Alice Rivera");
  expect(
    within(reach).getByRole("link", { name: "alice@contoso.com" }),
  ).toHaveAttribute("href", "mailto:alice@contoso.com");
  expect(
    within(reach).getByRole("link", { name: "+1 (312) 555-0100 ext. 12" }),
  ).toHaveAttribute("href", "tel:+13125550100");
  expect(within(reach).getByText("@alice on Slack")).toBeInTheDocument();
  expect(within(reach).getByText("Chicago (US Central)")).toBeInTheDocument();

  await userEvent.type(
    screen.getByRole("textbox", { name: "Person name" }),
    "Bob Chen",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Person title" }),
    "Product Manager ",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Person team" }),
    "Payments",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add person" }));

  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/people",
      method: "POST",
      body: { name: "Bob Chen", title: "Product Manager", team: "Payments" },
    }),
  );
});

it("dials a phone number's digits without its extension, and never text with too few", () => {
  expect(telephone("+1 (312) 555-0100 ext. 12")).toBe("tel:+13125550100");
  expect(telephone("+1 312 555 0100 x12")).toBe("tel:+13125550100");
  expect(telephone("312.555.0100")).toBe("tel:3125550100");
  expect(telephone("+44 20 7946 0958")).toBe("tel:+442079460958");
  // A trunk 0 written after the country code is not dialled from abroad; at home it is.
  expect(telephone("+44 (0)20 7946 0958")).toBe("tel:+442079460958");
  expect(telephone("(0)20 7946 0958")).toBe("tel:02079460958");
  // Letters or words would only dial the digits before them.
  expect(telephone("1-800-FLOWERS")).toBeNull();
  expect(telephone("+1 312 555 0100 (mobile)")).toBeNull();
  expect(telephone("ext 12")).toBeNull();
  expect(telephone("12")).toBeNull();
});

it("shows a person's fields and saves the one that changed", async () => {
  const calls = serve([[`/people/${PERSON_ID}`, detail]]);
  show(<PersonDetailView id={PERSON_ID} />);

  const email = await screen.findByRole("textbox", { name: "Email" });
  expect(email).toHaveValue("alice@contoso.com");
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(
    "Staff Engineer",
  );
  expect(screen.getByRole("textbox", { name: "Team" })).toHaveValue("Platform");
  expect(
    screen.getByRole("textbox", { name: "Location or time zone" }),
  ).toHaveValue("Chicago (US Central)");
  expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue(
    samplePerson.description,
  );
  expect(
    screen.getByRole("heading", { name: "Alice Rivera" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Staff Engineer · Platform")).toBeInTheDocument();

  await userEvent.clear(email);
  await userEvent.type(email, "alice.rivera@contoso.com");
  await userEvent.tab();
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: `/api/v1/people/${PERSON_ID}`,
      method: "PATCH",
      body: { revision: 3, email: "alice.rivera@contoso.com" },
    }),
  );
});

it("saves two fields edited one after the other on consecutive revisions of the person", async () => {
  let revision = 3;
  const patches: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if ((init?.method ?? "GET") === "GET")
      return new Response(JSON.stringify({ ...detail, revision }), {
        status: 200,
      });
    patches.push(JSON.parse(String(init?.body)));
    revision += 1;
    return new Response(JSON.stringify({ ...samplePerson, revision }), {
      status: 200,
    });
  });
  show(<PersonDetailView id={PERSON_ID} />);
  const title = await screen.findByRole("textbox", { name: "Title" });
  await userEvent.clear(title);
  await userEvent.type(title, "Principal Engineer");
  const email = screen.getByRole("textbox", { name: "Email" });
  // Leaving Title for Email saves Title; leaving Email saves Email on the revision Title made.
  await userEvent.click(email);
  await userEvent.clear(email);
  await userEvent.type(email, "alice@fabrikam.com");
  await userEvent.tab();
  await waitFor(() =>
    expect(patches).toEqual([
      { revision: 3, title: "Principal Engineer" },
      { revision: 4, email: "alice@fabrikam.com" },
    ]),
  );
});

it("keeps a refused email as a draft and says why", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) =>
    (init?.method ?? "GET") === "GET"
      ? new Response(JSON.stringify(detail), { status: 200 })
      : new Response(
          JSON.stringify({
            error: {
              code: "validation",
              message: "Email must be an address such as name@example.com.",
              fields: { email: "invalid" },
            },
          }),
          { status: 422 },
        ),
  );
  show(<PersonDetailView id={PERSON_ID} />);
  const email = await screen.findByRole("textbox", { name: "Email" });
  await userEvent.clear(email);
  await userEvent.type(email, "alice");
  await userEvent.tab();
  expect(
    await screen.findByText(
      "Not saved: Email must be an address such as name@example.com. Draft kept.",
    ),
  ).toBeInTheDocument();
  expect(email).toHaveValue("alice");
});

it("shows an archived person's ties without taking new ones, and still unlinks", async () => {
  const calls = serve([
    [`/people/${PERSON_ID}`, { ...detail, archived: true }],
  ]);
  show(<PersonDetailView id={PERSON_ID} />);
  expect(
    await screen.findByText(
      "Archived person. Recover them to tie them to more work.",
    ),
  ).toBeInTheDocument();
  for (const button of ["Tie work item", "Tie roadmap", "Tie repository"])
    expect(screen.queryByRole("button", { name: button })).toBeNull();
  expect(
    screen.getByRole("textbox", { name: "Role on ingest" }),
  ).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Unlink ingest" }));
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/person-links/unlink",
      method: "POST",
      body: {
        personId: PERSON_ID,
        targetType: "repository",
        targetId: REPO_ID,
      },
    }),
  );
});

it("shows a number it cannot dial as text, and names a chat handle and a place", async () => {
  serve([
    [
      "/people",
      {
        items: [{ ...samplePerson, email: "", phone: "1-800-FLOWERS" }],
        total: 1,
      },
    ],
  ]);
  show(<People />);
  const reach = await screen.findByRole("group", {
    name: "How to reach Alice Rivera",
  });
  expect(within(reach).queryByRole("link")).toBeNull();
  expect(within(reach).getByText("1-800-FLOWERS")).toBeInTheDocument();
  expect(reach).toHaveTextContent(
    "1-800-FLOWERSChat: @alice on SlackLocation: Chicago (US Central)",
  );
});

it("saves a role edited just before Unlink, then unlinks, so the save cannot tie the person again", async () => {
  const writes: { url: string; body: unknown }[] = [];
  let finishSave = () => undefined as void;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if ((init?.method ?? "GET") === "GET")
      return new Response(JSON.stringify(detail), { status: 200 });
    writes.push({ url, body: JSON.parse(String(init?.body)) });
    // The role's save is still on its way when Unlink is pressed.
    if (url.endsWith("/person-links"))
      await new Promise<void>((resolve) => (finishSave = resolve));
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  show(<PersonDetailView id={PERSON_ID} />);
  const role = await screen.findByRole("textbox", { name: "Role on ingest" });
  await userEvent.clear(role);
  await userEvent.type(role, "Owner");
  await userEvent.click(screen.getByRole("button", { name: "Unlink ingest" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].url).toBe("/api/v1/person-links");
  // Nothing more goes out while the save is pending: the unlink waits for it.
  await new Promise((resolve) => setTimeout(resolve, 100));
  expect(writes).toHaveLength(1);
  finishSave();
  await waitFor(() =>
    expect(writes.map((w) => w.url)).toEqual([
      "/api/v1/person-links",
      "/api/v1/person-links/unlink",
    ]),
  );
});

it("ties work items, roadmaps and repositories from a person's page, and edits and removes ties", async () => {
  const calls = serve([
    [`/people/${PERSON_ID}`, detail],
    [
      "/work-items",
      {
        items: [
          sampleItem(ITEM_ID, "Define SLOs"),
          sampleItem(OTHER_ITEM_ID, "Roll out alerts"),
        ],
        total: 2,
      },
    ],
    [
      "/roadmaps",
      {
        items: [
          sampleRoadmap(ROADMAP_ID, "Reliability roadmap"),
          sampleRoadmap(OTHER_ROADMAP_ID, "Hiring plan"),
        ],
        total: 2,
      },
    ],
    [
      "/repositories",
      {
        items: [
          sampleRepository(REPO_ID, "ingest"),
          sampleRepository(OTHER_REPO_ID, "web"),
        ],
        total: 2,
      },
    ],
  ]);
  show(<PersonDetailView id={PERSON_ID} />);

  expect(
    await screen.findByRole("textbox", { name: "Role on Reliability roadmap" }),
  ).toHaveValue("Owner");
  expect(
    screen.getByRole("textbox", { name: "Role on Define SLOs" }),
  ).toHaveValue("Reviewer");
  expect(screen.getByRole("textbox", { name: "Role on ingest" })).toHaveValue(
    "Maintainer",
  );

  // Each picker offers only what is not tied yet; picking a match ties it.
  const tie = async (button: string, search: string, pick: RegExp) => {
    await userEvent.click(screen.getByRole("button", { name: button }));
    const matches = await screen.findByRole("list", {
      name: search + " matches",
    });
    expect(within(matches).getAllByRole("button")).toHaveLength(1);
    await userEvent.click(within(matches).getByRole("button", { name: pick }));
  };
  const tied = (targetType: string, targetId: string) => ({
    url: "/api/v1/person-links",
    method: "POST",
    body: { personId: PERSON_ID, targetType, targetId, relationship: "" },
  });
  await tie("Tie work item", "Work to tie", /Roll out alerts/);
  await waitFor(() =>
    expect(calls.at(-1)).toEqual(tied("item", OTHER_ITEM_ID)),
  );
  await tie("Tie roadmap", "Roadmap to tie", /Hiring plan/);
  await waitFor(() =>
    expect(calls.at(-1)).toEqual(tied("roadmap", OTHER_ROADMAP_ID)),
  );
  await tie("Tie repository", "Repository to tie", /web/);
  await waitFor(() =>
    expect(calls.at(-1)).toEqual(tied("repository", OTHER_REPO_ID)),
  );

  // A role saves when its field loses focus.
  const role = screen.getByRole("textbox", { name: "Role on ingest" });
  await userEvent.clear(role);
  await userEvent.type(role, "Owner");
  await userEvent.tab();
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/person-links",
      method: "POST",
      body: {
        personId: PERSON_ID,
        targetType: "repository",
        targetId: REPO_ID,
        relationship: "Owner",
      },
    }),
  );

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

it("ties an existing person to a target via PeopleLinks, offering only people not tied yet", async () => {
  const other: Person = {
    ...samplePerson,
    id: "55555555-5555-4555-8555-555555555555",
    name: "Bob Chen",
    title: "",
    team: "Payments",
  };
  const calls = serve([
    ["/people", { items: [samplePerson, other], total: 2 }],
  ]);
  show(
    <PeopleLinks
      targetType="repository"
      targetId={REPO_ID}
      people={[
        {
          id: "link-p1",
          personId: other.id,
          name: other.name,
          personTitle: "",
          personTeam: "Payments",
          relationship: "Reviewer",
          archived: false,
          createdAt: "2026-10-29T00:00:00Z",
        },
      ]}
    />,
  );

  expect(screen.getByText("Payments")).toBeInTheDocument();
  expect(
    await screen.findByRole("option", {
      name: "Alice Rivera (Staff Engineer · Platform)",
    }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /Bob Chen/ })).toBeNull();
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Choose person" }),
    PERSON_ID,
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Relationship or role" }),
    "Maintainer",
  );
  await userEvent.click(screen.getByRole("button", { name: "Tie person" }));

  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/person-links",
      method: "POST",
      body: {
        personId: PERSON_ID,
        targetType: "repository",
        targetId: REPO_ID,
        relationship: "Maintainer",
      },
    }),
  );
});

it("creates a person by name alone from PeopleLinks, and an archived target keeps its ties without taking new ones", async () => {
  const calls = serve([["/people", { items: [], total: 0 }]]);
  const { unmount } = show(
    <PeopleLinks targetType="roadmap" targetId={ROADMAP_ID} people={[]} />,
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Choose person" }),
    "__new__",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "New person name" }),
    "Dana Kim",
  );
  await userEvent.click(screen.getByRole("button", { name: "Tie person" }));
  await waitFor(() =>
    expect(calls.slice(-2)).toEqual([
      { url: "/api/v1/people", method: "POST", body: { name: "Dana Kim" } },
      {
        url: "/api/v1/person-links",
        method: "POST",
        body: {
          personId: "written",
          targetType: "roadmap",
          targetId: ROADMAP_ID,
          relationship: "",
        },
      },
    ]),
  );
  unmount();

  show(
    <PeopleLinks
      targetType="repository"
      targetId={REPO_ID}
      canTie={false}
      people={[
        {
          id: "link-p1",
          personId: PERSON_ID,
          name: "Alice Rivera",
          personTitle: "Staff Engineer",
          personTeam: "",
          relationship: "Maintainer",
          archived: false,
          createdAt: "2026-10-29T00:00:00Z",
        },
      ]}
    />,
  );
  expect(
    screen.getByRole("textbox", { name: "Role for Alice Rivera" }),
  ).toBeEnabled();
  expect(
    screen.getByRole("button", { name: "Unlink Alice Rivera" }),
  ).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Tie person" })).toBeNull();
});

it("adds and renders web links, agent chats, and GitHub commits/PRs", async () => {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (
      (init?.method ?? "GET") === "GET" &&
      String(input).includes("/repositories")
    )
      return new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
      });
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
    provider: "Anthropic",
    model: "claude-opus-5-5",
    repos: ["acme/worklog"],
    stepIds: [],
  };

  show(
    <>
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
    </>,
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
    screen.getByRole("combobox", { name: "Agent chat provider" }),
    "OpenAI",
  );
  await userEvent.type(
    screen.getByRole("combobox", { name: "Agent chat model" }),
    "gpt-5.5",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Agent chat repositories" }),
    "acme/api, acme/web,, acme/api",
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
        provider: "OpenAI",
        model: "gpt-5.5",
        repos: ["acme/api", "acme/web"],
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
