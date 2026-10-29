import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, setDataContext } from "./api";
import { commands } from "./commands";
import type { Detail } from "./types";
import { ItemDetail } from "./Work";

const state = vi.hoisted(() => ({ item: null as unknown }));
vi.mock("./hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./hooks")>()),
  useData: (path: string) => ({
    data: path.startsWith("/work-items/")
      ? state.item
      : { items: [], total: 0 },
  }),
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
