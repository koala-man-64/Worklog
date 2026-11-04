import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, setDataContext } from "./api";
import { commands } from "./commands";
import type { Detail } from "./types";
import { ItemDetail, Work } from "./Work";

const state = vi.hoisted(() => ({
  item: null as unknown,
  paths: [] as string[],
}));
vi.mock("./hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./hooks")>()),
  useData: (path: string) => {
    state.paths.push(path);
    return {
      data: path.startsWith("/work-items/")
        ? state.item
        : { items: [], total: 0 },
    };
  },
}));

const ID = "11111111-1111-4111-8111-111111111111";
function item(fields: Partial<Detail>): Detail {
  return {
    id: ID,
    revision: 3,
    archived: false,
    title: "Release checks",
    description: "",
    status: "open",
    adoUrl: "https://dev.azure.com/a",
    stepId: null,
    linkedStep: null,
    notes: [],
    tasks: [],
    attachments: [],
    references: [],
    history: [],
    dailyEntries: [],
    ...fields,
  };
}
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
  state.paths = [];
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
  expect(states()).toEqual([]);
  await userEvent.click(toggle("Blocked"));
  await userEvent.click(toggle("Doing"));
  expect(toggle("Doing")).toHaveAttribute("aria-pressed", "true");
  // The states keep one order whichever was pressed first, so the query stays the same.
  expect(states()).toEqual(["doing", "blocked"]);
  await userEvent.click(toggle("Doing"));
  expect(toggle("Doing")).toHaveAttribute("aria-pressed", "false");
  expect(states()).toEqual(["blocked"]);
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
      {
        id: "a1",
        revision: 2,
        archived: true,
        name: "old.txt",
        mime: "text/plain",
        size: 3,
        sha256: "",
        createdAt: "2026-11-01T08:00:00+00:00",
      },
      {
        id: "a2",
        revision: 1,
        name: "evidence.txt",
        mime: "text/plain",
        size: 14,
        sha256: "",
        createdAt: "2026-11-02T10:00:00+00:00",
      },
    ],
    references: [
      {
        id: "r1",
        revision: 1,
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
      {
        id: "pad",
        revision: 2,
        archived: false,
        title: "Scratchpad",
        text: "Running notes",
        ownerType: "item",
        ownerId: ID,
      },
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
it("links a work item directly to a roadmap without requiring a step", async () => {
  state.item = item({
    roadmapId: "22222222-2222-4222-8222-222222222222",
    linkedRoadmap: {
      id: "22222222-2222-4222-8222-222222222222",
      title: "Platform roadmap",
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
