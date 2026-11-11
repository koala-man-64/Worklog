import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, setDataContext } from "./api";
import { commands } from "./commands";
import type { Detail } from "./apiTypes";
import { aNote, anAttachment, anItem } from "./fixtures";
import { ItemDetail, Work } from "./Work";

const state = vi.hoisted(() => ({
  item: null as unknown,
  list: null as unknown,
  paths: [] as string[],
}));
vi.mock("./hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./hooks")>()),
  useData: (path: string) => {
    state.paths.push(path);
    return {
      data: path.startsWith("/work-items/")
        ? state.item
        : path.startsWith("/work-items?") && state.list
          ? state.list
          : path.startsWith("/repositories/")
            ? { id: path.split("/")[2], name: "Service", archived: false }
            : { items: [], total: 0 },
    };
  },
}));

const ID = "11111111-1111-4111-8111-111111111111";
// When the fixture's work item was added and last changed.
const STAMPS = {
  createdAt: "2026-10-27T15:04:00+00:00",
  updatedAt: "2026-10-27T15:04:00+00:00",
};
function item(fields: Partial<Detail>): Detail {
  return {
    ...anItem({
      id: ID,
      revision: 3,
      title: "Release checks",
      adoUrl: "https://dev.azure.com/a",
      ...STAMPS,
    }),
    linkedRoadmap: null,
    linkedStep: null,
    notes: [],
    attachments: [],
    links: [],
    chats: [],
    github: [],
    references: [],
    history: [],
    dailyEntries: [],
    rank: 2,
    rankedCount: 5,
    ...fields,
  };
}
const writes = () =>
  vi
    .mocked(globalThis.fetch)
    .mock.calls.filter(([, init]) => (init?.method ?? "GET") !== "GET")
    .map(([url, init]) => [String(url), JSON.parse(String(init?.body))]);
const respond = () =>
  vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
// A row's revision tells which item a move was checked against.
const row = (
  id: string,
  title: string,
  rank: number | null,
  status = "open",
) => ({
  id,
  title,
  rank,
  status,
  description: "",
  revision: 10 + (rank ?? 0),
  archived: false,
  adoUrl: null,
  stepId: null,
});
const newClient = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });
function view(client: QueryClient) {
  return (
    <QueryClientProvider client={client}>
      <ItemDetail id={ID} />
    </QueryClientProvider>
  );
}
const field = () => screen.getByRole("textbox", { name: "Azure DevOps link" });
beforeEach(() => {
  vi.restoreAllMocks();
  // Acknowledged revision chains are module-level; a fresh data context keeps tests independent.
  setDataContext("work-test-" + crypto.randomUUID());
  state.item = item({});
  state.list = null;
  state.paths = [];
});
const workList = () =>
  render(
    <QueryClientProvider client={newClient()}>
      <Work />
    </QueryClientProvider>,
  );
it("ranks the work list and moves a row past the one shown beside it", async () => {
  // A filtered page: ranks 1, 4 and 5 of 6 show, with done work after them.
  state.list = {
    items: [
      row("a", "Alpha", 1),
      row("b", "Bravo", 4),
      row("c", "Charlie", 5),
      row("d", "Delta", null, "done"),
    ],
    total: 4,
    rankedCount: 6,
  };
  respond();
  render(
    <QueryClientProvider client={newClient()}>
      <Work />
    </QueryClientProvider>,
  );
  expect(
    screen.getByRole("button", { name: "Change stack rank of Bravo, now 4" }),
  ).toHaveTextContent("#4");
  expect(
    screen.queryByRole("button", { name: /Change stack rank of Delta/ }),
  ).toBeNull();
  expect(screen.getByRole("button", { name: "Move Alpha up" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Move Delta up" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Move Delta down" }),
  ).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Move Bravo up" }));
  // Charlie has no ranked row below it on this page, so it moves one rank.
  await userEvent.click(
    screen.getByRole("button", { name: "Move Charlie down" }),
  );
  await waitFor(() => expect(writes()).toHaveLength(2));
  expect(writes()).toEqual([
    ["/api/v1/work-items/b/rank", { revision: 14, rank: 1 }],
    ["/api/v1/work-items/c/rank", { revision: 15, rank: 6 }],
  ]);
});
it("reads the row beside one at a page's edge before moving past it", async () => {
  state.list = {
    items: [row("a", "Alpha", 1), row("b", "Bravo", 3)],
    total: 3,
    rankedCount: 9,
  };
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(
      async (url) =>
        new Response(
          JSON.stringify(
            String(url).includes("/work-items?")
              ? { items: [row("c", "Charlie", 7)], total: 3 }
              : {},
          ),
          { status: 200 },
        ),
    );
  render(
    <QueryClientProvider client={newClient()}>
      <Work />
    </QueryClientProvider>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Move Bravo down" }),
  );
  await waitFor(() => expect(writes()).toHaveLength(1));
  const read = network.mock.calls.find(([url]) =>
    String(url).includes("/work-items?"),
  )!;
  expect(String(read[0])).toContain("limit=1&offset=2");
  expect(writes()).toEqual([
    ["/api/v1/work-items/b/rank", { revision: 13, rank: 7 }],
  ]);
});
it("moves a row to the rank typed into its picker", async () => {
  state.list = {
    items: [row("a", "Alpha", 1), row("b", "Bravo", 2)],
    total: 2,
    rankedCount: 2,
  };
  respond();
  render(
    <QueryClientProvider client={newClient()}>
      <Work />
    </QueryClientProvider>,
  );
  const open = screen.getByRole("button", {
    name: "Change stack rank of Bravo, now 2",
  });
  await userEvent.click(open);
  expect(open).toHaveAttribute("aria-expanded", "true");
  const input = screen.getByRole("spinbutton", {
    name: "New stack rank for Bravo",
  });
  expect(input).toHaveFocus();
  expect(screen.getByRole("button", { name: "Move" })).toBeDisabled();
  await userEvent.clear(input);
  await userEvent.type(input, "1");
  await userEvent.click(screen.getByRole("button", { name: "Move" }));
  await waitFor(() =>
    expect(writes()).toEqual([
      ["/api/v1/work-items/b/rank", { revision: 12, rank: 1 }],
    ]),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("spinbutton", { name: "New stack rank for Bravo" }),
    ).toBeNull(),
  );
});
it("shows the item's stack rank on its page, where the picker sets it", async () => {
  respond();
  render(view(newClient()));
  const open = screen.getByRole("button", {
    name: "Change stack rank of Release checks, now 2",
  });
  expect(open).toHaveTextContent("#2 of 5");
  await userEvent.click(open);
  const input = screen.getByRole("spinbutton", {
    name: "New stack rank for Release checks",
  });
  await userEvent.keyboard("{Escape}");
  expect(input).not.toBeInTheDocument();
  await userEvent.click(open);
  await userEvent.clear(
    screen.getByRole("spinbutton", {
      name: "New stack rank for Release checks",
    }),
  );
  await userEvent.type(
    screen.getByRole("spinbutton", {
      name: "New stack rank for Release checks",
    }),
    "5{Enter}",
  );
  await waitFor(() =>
    expect(writes()).toEqual([
      [`/api/v1/work-items/${ID}/rank`, { revision: 3, rank: 5 }],
    ]),
  );
});
it("shows done work without a stack rank", () => {
  state.item = item({ status: "done", rank: null, rankedCount: 4 });
  render(view(newClient()));
  expect(
    screen.queryByRole("button", { name: /Change stack rank/ }),
  ).toBeNull();
});
it("lays the work list out as a sheet, one row per item and one column per fact", () => {
  state.list = {
    total: 2,
    rankedCount: 5,
    items: [
      {
        ...item({ description: "Run the smoke suite", status: "doing" }),
        people: [
          {
            id: "l1",
            personId: "p1",
            name: "Priya",
            personTitle: "",
            personTeam: "",
            relationship: "owner",
            archived: false,
            createdAt: "",
          },
        ],
      },
      item({
        id: "22222222-2222-4222-8222-222222222222",
        title: "Rollback runbook",
        status: "done",
        rank: null,
      }),
    ],
  };
  workList();
  const sheet = screen.getByRole("table", { name: "Work items" });
  expect(
    within(sheet)
      .getAllByRole("columnheader")
      .map((head) => head.getAttribute("aria-label") ?? head.textContent),
  ).toEqual([
    "Rank",
    "Title",
    "Status",
    "Description",
    "People",
    "Repositories",
    "Order",
    "Archive",
  ]);
  const [first, second] = within(sheet).getAllByRole("row").slice(1);
  expect(
    within(first).getByRole("button", {
      name: "Change stack rank of Release checks, now 2",
    }),
  ).toHaveTextContent("#2");
  expect(within(first).getByRole("rowheader")).toHaveTextContent(
    "Release checks",
  );
  const title = within(first).getByRole("link", { name: "Release checks" });
  expect(title).toHaveAttribute("href", "#work/" + ID);
  // The sheet cuts a long title at two lines; the link keeps it whole and shows it on hover.
  expect(title).toHaveAttribute("title", "Release checks");
  const status = within(first).getByRole("combobox", { name: "Status" });
  expect(status).toHaveValue("doing");
  // Work can also wait on someone else; tickets and roadmap steps can't.
  expect(
    within(status)
      .getAllByRole("option")
      .map((option) => [option.getAttribute("value"), option.textContent]),
  ).toEqual([
    ["open", "Open"],
    ["doing", "Doing"],
    ["waiting", "Waiting"],
    ["blocked", "Blocked"],
    ["done", "Done"],
  ]);
  expect(within(first).getByText("Run the smoke suite")).toBeInTheDocument();
  expect(
    within(first).getByRole("link", { name: "Priya (owner)" }),
  ).toHaveAttribute("href", "#people/p1");
  expect(
    within(first).getByRole("button", { name: "Move Release checks up" }),
  ).toBeEnabled();
  expect(
    within(first).getByRole("button", { name: "Archive" }),
  ).toBeInTheDocument();
  // Done work has no stack rank, so its rank cell is empty and it keeps its place.
  expect(
    within(second).queryByRole("button", { name: /Change stack rank/ }),
  ).toBeNull();
  expect(
    within(second).getByRole("button", { name: "Move Rollback runbook down" }),
  ).toBeDisabled();
  expect(within(second).getByText("Ready for a next step")).toBeInTheDocument();
  expect(screen.getByText("2 work items")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Next work" })).toBeNull();
});
it("opens a row's rank picker in a row of its own under it", async () => {
  state.list = {
    items: [row("a", "Alpha", 1), row("b", "Bravo", 2)],
    total: 2,
    rankedCount: 2,
  };
  workList();
  await userEvent.click(
    screen.getByRole("button", { name: "Change stack rank of Alpha, now 1" }),
  );
  const picker = screen
    .getByRole("spinbutton", { name: "New stack rank for Alpha" })
    .closest("tr")!;
  expect(picker).toHaveClass("rank-row");
  expect(picker.previousElementSibling).toHaveTextContent("Alpha");
  expect(document.querySelectorAll(".work-row")).toHaveLength(2);
});
it("keeps the sheet's header row over an empty list", () => {
  workList();
  const sheet = screen.getByRole("table", { name: "Work items" });
  expect(
    within(sheet).getByRole("columnheader", { name: "Title" }),
  ).toBeInTheDocument();
  expect(
    within(sheet).getByText("No work items yet. Add your first one above."),
  ).toBeInTheDocument();
  expect(sheet.querySelectorAll(".work-row")).toHaveLength(0);
  expect(screen.getByText("0 work items")).toBeInTheDocument();
});
it("pages a long list from the sheet's foot", async () => {
  state.list = { total: 312, items: [item({})] };
  workList();
  const offset = () =>
    new URLSearchParams(
      state.paths
        .filter((path) => path.startsWith("/work-items?"))
        .at(-1)!
        .split("?")[1],
    ).get("offset");
  expect(screen.getByText("1 to 100 of 312")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Previous work" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Next work" }));
  expect(offset()).toBe("100");
  expect(screen.getByText("101 to 200 of 312")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Previous work" })).toBeEnabled();
});
it("filters the work list by any of the pressed states", async () => {
  render(
    <QueryClientProvider client={newClient()}>
      <Work />
    </QueryClientProvider>,
  );
  const states = () =>
    new URLSearchParams(
      state.paths
        .filter((path) => path.startsWith("/work-items?"))
        .at(-1)!
        .split("?")[1],
    ).getAll("status");
  const toggle = (name: string) =>
    within(screen.getByRole("group", { name: "Filter status" })).getByRole(
      "button",
      { name },
    );
  expect(
    within(screen.getByRole("group", { name: "Filter status" }))
      .getAllByRole("button")
      .map((button) => button.textContent),
  ).toEqual(["Open", "Doing", "Waiting", "Blocked", "Done"]);
  expect(states()).toEqual([]);
  await userEvent.click(toggle("Blocked"));
  await userEvent.click(toggle("Waiting"));
  await userEvent.click(toggle("Doing"));
  expect(toggle("Doing")).toHaveAttribute("aria-pressed", "true");
  // The states keep one order whichever was pressed first, so the query stays the same.
  expect(states()).toEqual(["doing", "waiting", "blocked"]);
  await userEvent.click(toggle("Doing"));
  expect(toggle("Doing")).toHaveAttribute("aria-pressed", "false");
  expect(states()).toEqual(["waiting", "blocked"]);
});
it("shows a link that changed elsewhere while the page is open", () => {
  const client = newClient();
  const { rerender } = render(view(client));
  expect(field()).toHaveValue("https://dev.azure.com/a");
  state.item = item({ adoUrl: "https://dev.azure.com/b", revision: 4 });
  rerender(view(client));
  expect(field()).toHaveValue("https://dev.azure.com/b");
});
it("sends nothing when the link field is left unedited", async () => {
  const save = vi.spyOn(commands, "updateItem");
  const network = vi.spyOn(globalThis, "fetch");
  render(view(newClient()));
  await userEvent.click(field());
  await userEvent.tab();
  expect(save).not.toHaveBeenCalled();
  expect(network).not.toHaveBeenCalled();
});
it("never overwrites a link that changed elsewhere when the user tabs through", async () => {
  const save = vi
    .spyOn(commands, "updateItem")
    .mockImplementation(async (_id, body) => ({
      ...item({ adoUrl: "https://dev.azure.com/mine" }),
      revision: body.revision + 1,
    }));
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  const writes = () =>
    save.mock.calls.length +
    network.mock.calls.filter(([, init]) => (init?.method ?? "GET") !== "GET")
      .length;
  const client = newClient();
  const { rerender } = render(view(client));
  // The user sets the link first, so the field has been edited.
  await userEvent.clear(field());
  await userEvent.type(field(), "https://dev.azure.com/mine");
  await userEvent.tab();
  await waitFor(() => expect(writes()).toBe(1));
  state.item = item({ adoUrl: "https://dev.azure.com/b", revision: 5 });
  rerender(view(client));
  await userEvent.click(field());
  await userEvent.tab();
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(writes()).toBe(1);
  expect(field()).toHaveValue("https://dev.azure.com/b");
});
it("keeps a stale link draft and shows the conflict panel instead of writing", async () => {
  const save = vi
    .spyOn(commands, "updateItem")
    .mockRejectedValue(new ApiError(409, "This record changed.", "conflict"));
  const client = newClient();
  const { rerender } = render(view(client));
  await userEvent.clear(field());
  await userEvent.type(field(), "https://dev.azure.com/mine");
  state.item = item({ adoUrl: "https://dev.azure.com/b", revision: 4 });
  rerender(view(client));
  await userEvent.tab();
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(ID, {
      revision: 3,
      adoUrl: "https://dev.azure.com/mine",
    }),
  );
  expect(
    await screen.findByText("The current saved value is:"),
  ).toBeInTheDocument();
  expect(field()).toHaveValue("https://dev.azure.com/mine");
});
it("shows the server's validation message for an invalid link", async () => {
  vi.spyOn(commands, "updateItem").mockRejectedValue(
    new ApiError(422, "ADO link must be an absolute HTTP(S) URL", "validation"),
  );
  render(view(newClient()));
  await userEvent.clear(field());
  await userEvent.type(field(), "not a link");
  await userEvent.tab();
  expect(
    await screen.findByText(/ADO link must be an absolute HTTP\(S\) URL/),
  ).toBeInTheDocument();
});
it("saves the link on the item's acknowledged revision after a title save", async () => {
  const save = vi
    .spyOn(commands, "updateItem")
    .mockImplementation(async (_id, body) => ({
      ...item({}),
      revision: body.revision + 1,
    }));
  render(view(newClient()));
  await userEvent.type(screen.getByRole("textbox", { name: "Title" }), " now");
  await userEvent.tab();
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(ID, {
      revision: 3,
      title: "Release checks now",
    }),
  );
  await userEvent.clear(field());
  await userEvent.type(field(), "https://dev.azure.com/c");
  await userEvent.tab();
  await waitFor(() =>
    expect(save).toHaveBeenLastCalledWith(ID, {
      revision: 4,
      adoUrl: "https://dev.azure.com/c",
    }),
  );
});
it("files a ticket for the work item from its page", async () => {
  const create = vi
    .spyOn(commands, "createTicket")
    .mockResolvedValue({} as Awaited<ReturnType<typeof commands.createTicket>>);
  render(view(newClient()));
  expect(
    screen.getByText("No tickets for this work item."),
  ).toBeInTheDocument();
  await userEvent.type(
    screen.getByRole("textbox", { name: "New ticket title" }),
    "Release check fails on Windows",
  );
  await userEvent.click(screen.getByRole("button", { name: "File ticket" }));
  await waitFor(() =>
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Release check fails on Windows",
        itemId: ID,
      }),
      expect.anything(),
    ),
  );
});
it("attaches a copy by default and saves only the path for a local file", async () => {
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  const writes = () =>
    network.mock.calls.filter(([, init]) => init?.method === "POST");
  render(view(newClient()));
  expect(screen.getByRole("button", { name: "Attach a copy" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await userEvent.upload(
    screen.getByLabelText("Attach a file"),
    new File(["Local evidence"], "evidence.txt", { type: "text/plain" }),
  );
  await waitFor(() => expect(writes()).toHaveLength(1));
  expect(writes()[0][0]).toBe(
    `/api/v1/attachments?ownerType=item&ownerId=${ID}&name=evidence.txt`,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Reference a local file" }),
  );
  expect(screen.queryByLabelText("Attach a file")).not.toBeInTheDocument();
  await userEvent.type(
    screen.getByRole("textbox", { name: "Local path" }),
    "C:\\Projects\\spec.docx",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add path" }));
  await waitFor(() => expect(writes()).toHaveLength(2));
  const [url, init] = writes()[1];
  expect(url).toBe("/api/v1/references");
  expect(JSON.parse(String(init?.body))).toEqual({
    itemId: ID,
    path: "C:\\Projects\\spec.docx",
  });
  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: "Local path" })).toHaveValue(""),
  );
});
it("lists attached copies and local paths together, oldest first", async () => {
  state.item = item({
    attachments: [
      anAttachment({
        id: "a1",
        ownerId: ID,
        revision: 2,
        archived: true,
        name: "old.txt",
        mime: "text/plain",
        size: 3,
        createdAt: "2026-11-01T08:00:00+00:00",
      }),
      anAttachment({
        id: "a2",
        ownerId: ID,
        name: "evidence.txt",
        mime: "text/plain",
        size: 14,
        createdAt: "2026-11-02T10:00:00+00:00",
      }),
    ],
    references: [
      {
        id: "r1",
        revision: 1,
        archived: false,
        itemId: ID,
        path: "C:\\Projects\\spec.docx",
        createdAt: "2026-11-02T09:00:00+00:00",
      },
    ],
  });
  const user = userEvent.setup();
  render(view(newClient()));
  const files = screen
    .getByRole("group", { name: "How to add a file" })
    .closest("section")!;
  expect(files).toHaveTextContent(
    /spec\.docx\s*Local path.*evidence\.txt\s*Attached copy · 14 B/,
  );
  expect(files).not.toHaveTextContent("old.txt");
  await user.click(within(files).getByRole("button", { name: "Copy path" }));
  expect(await navigator.clipboard.readText()).toBe("C:\\Projects\\spec.docx");
});
it("gives the work item one scratchpad instead of a list of notes", () => {
  state.item = item({
    notes: [
      aNote({
        id: "pad",
        revision: 2,
        title: "Scratchpad",
        text: "Running notes",
        ownerId: ID,
      }),
    ],
  });
  render(view(newClient()));
  expect(screen.getByLabelText("Scratchpad (Markdown)")).toHaveValue(
    "Running notes",
  );
  expect(screen.queryByRole("button", { name: "Add note" })).toBeNull();
});
it("files no tickets for archived work", () => {
  state.item = item({ archived: true });
  render(view(newClient()));
  expect(
    screen.getByRole("textbox", { name: "New ticket title" }),
  ).toBeDisabled();
});
it("captures work with or without repositories, folded to one button until used", async () => {
  const posts: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify(item({})));
    }
    return new Response(
      JSON.stringify({
        items: [
          {
            id: "repo-1",
            name: "Service",
            url: "https://github.com/a/service",
            archived: false,
          },
        ],
        total: 1,
      }),
    );
  });
  render(
    <QueryClientProvider client={newClient()}>
      <Work />
    </QueryClientProvider>,
  );
  const capture = screen.getByRole("group", { name: "New work repositories" });
  // Linking is optional: one button, not a list of every repository.
  expect(within(capture).getAllByRole("button")).toHaveLength(1);
  const title = screen.getByRole("textbox", { name: "New work item" });
  await userEvent.type(title, "No repository{Enter}");
  await waitFor(() =>
    expect(posts).toEqual([
      { title: "No repository", description: "", worked: false },
    ]),
  );
  await userEvent.click(
    within(capture).getByRole("button", { name: "Link repository" }),
  );
  await userEvent.click(await screen.findByRole("button", { name: /Service/ }));
  expect(
    within(capture).getByRole("link", { name: "Service" }),
  ).toHaveAttribute("href", "#repositories/repo-1");
  await userEvent.type(title, "With a repository{Enter}");
  await waitFor(() =>
    expect(posts.at(-1)).toEqual({
      title: "With a repository",
      description: "",
      worked: false,
      repositoryIds: ["repo-1"],
    }),
  );
});
it("links and unlinks repositories in the item's Context on its revision", async () => {
  state.item = item({ repositoryIds: ["repo-1"] });
  const patches: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (init?.method === "PATCH") patches.push(JSON.parse(String(init.body)));
    return new Response(JSON.stringify(item({ revision: 4 })));
  });
  render(view(newClient()));
  const context = screen
    .getByRole("heading", { name: "Context" })
    .closest("section")!;
  const repositories = within(context).getByRole("group", {
    name: "Repositories",
  });
  expect(
    within(repositories).getByRole("link", { name: "Service" }),
  ).toHaveAttribute("href", "#repositories/repo-1");
  await userEvent.click(
    within(repositories).getByRole("button", { name: "Unlink Service" }),
  );
  await waitFor(() =>
    expect(patches).toEqual([{ revision: 3, repositoryIds: [] }]),
  );
});
it("keeps an archived item's repositories as they are", () => {
  state.item = item({ archived: true, repositoryIds: ["repo-1"] });
  render(view(newClient()));
  const repositories = screen.getByRole("group", { name: "Repositories" });
  expect(
    within(repositories).getByRole("button", { name: "Unlink Service" }),
  ).toBeDisabled();
  expect(
    within(repositories).getByRole("button", { name: "Link repository" }),
  ).toBeDisabled();
});
it("links a work item directly to a roadmap without requiring a step", async () => {
  state.item = item({
    roadmapId: "22222222-2222-4222-8222-222222222222",
    linkedRoadmap: {
      id: "22222222-2222-4222-8222-222222222222",
      title: "Platform roadmap",
      archived: false,
    },
  });
  render(view(newClient()));
  expect(
    screen.getByRole("link", { name: "Platform roadmap" }),
  ).toHaveAttribute("href", "#roadmaps/22222222-2222-4222-8222-222222222222");
  expect(screen.getByRole("combobox", { name: "Roadmap" })).toHaveValue(
    "22222222-2222-4222-8222-222222222222",
  );
});
