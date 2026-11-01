import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RoadmapDetail } from "./Roadmaps";
import type { AgentChat, Roadmap, Step } from "./types";

const ROADMAP = "22222222-2222-4222-8222-222222222222";
function step(id: string, title: string, parentId: string | null): Step {
  return {
    id,
    title,
    description: "",
    parentId,
    position: 0,
    status: "open",
    revision: 1,
    retired: false,
  };
}
const road: Roadmap = {
  id: ROADMAP,
  revision: 1,
  title: "Plan",
  overview: "",
  steps: [
    step("aaaaaaaa-0000-4000-8000-000000000001", "Parent", null),
    step(
      "aaaaaaaa-0000-4000-8000-000000000002",
      "Child",
      "aaaaaaaa-0000-4000-8000-000000000001",
    ),
    step("aaaaaaaa-0000-4000-8000-000000000003", "Solo", null),
  ],
  versions: [],
  notes: [],
  attachments: [],
  history: [],
};
let served = road;
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("roadmap-steps-" + crypto.randomUUID());
  served = road;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    Response.json(
      String(input).endsWith("/roadmaps/" + ROADMAP)
        ? served
        : { items: [], total: 0 },
    ),
  );
});
const PARENT = "aaaaaaaa-0000-4000-8000-000000000001";
const CHILD = "aaaaaaaa-0000-4000-8000-000000000002";
const view = (step?: string) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RoadmapDetail id={ROADMAP} step={step} />
    </QueryClientProvider>,
  );

it("collapses and expands a step's sub-steps, only for steps that have them", async () => {
  view();
  expect(await screen.findByText("Child")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Collapse Solo" })).toBeNull();
  const toggle = screen.getByRole("button", { name: "Collapse Parent" });
  expect(toggle).toHaveAttribute("aria-expanded", "true");

  await userEvent.click(toggle);
  expect(screen.queryByText("Child")).toBeNull();
  expect(screen.getByText(/1 sub-step hidden/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Expand Parent" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );

  await userEvent.click(screen.getByRole("button", { name: "Expand Parent" }));
  expect(screen.getByText("Child")).toBeVisible();
});

it("collapses and expands every parent at once", async () => {
  view();
  await screen.findByText("Child");
  const expand = screen.getByRole("button", { name: "Expand all" });
  expect(expand).toBeDisabled();
  expect(screen.getByRole("button", { name: "Collapse done" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  expect(screen.queryByText("Child")).toBeNull();
  expect(screen.getByRole("button", { name: "Collapse all" })).toBeDisabled();
  await userEvent.click(expand);
  expect(screen.getByText("Child")).toBeVisible();
});

it("collapses the sections whose steps are all done, keeping the rest as they are", async () => {
  const SHIPPED = "aaaaaaaa-0000-4000-8000-000000000004";
  served = {
    ...road,
    steps: [
      ...road.steps,
      { ...step(SHIPPED, "Shipped", null), status: "done" },
      {
        ...step("aaaaaaaa-0000-4000-8000-000000000005", "Released", SHIPPED),
        status: "done",
      },
    ],
  };
  const first = view();
  expect(await screen.findByText("Released")).toBeVisible();
  const collapseDone = screen.getByRole("button", { name: "Collapse done" });
  await userEvent.click(collapseDone);
  expect(screen.queryByText("Released")).toBeNull();
  expect(screen.getByText("Child")).toBeVisible();
  expect(collapseDone).toBeDisabled();

  await userEvent.click(screen.getByRole("button", { name: "Expand Shipped" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Collapse Parent" }),
  );
  await userEvent.click(collapseDone);
  expect(screen.queryByText("Released")).toBeNull();
  expect(screen.queryByText("Child")).toBeNull();

  first.unmount();
  view();
  expect(
    await screen.findByRole("button", { name: "Expand Shipped" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Expand Parent" })).toBeVisible();
});

it("applies prominent state classes to step containers, status dots, and status selects", async () => {
  view();
  await screen.findByText("Child");
  const select = screen.getByRole("combobox", { name: "Progress for Solo" });
  expect(select).toHaveClass("status-select", "open");
  expect(select).toHaveAttribute("data-status", "open");
  const soloStep = screen.getByText("Solo").closest(".step");
  expect(soloStep).toHaveClass("step", "status-open");
  expect(soloStep?.querySelector(".status-dot.open")).not.toBeNull();
});

it("shows the selected step's details beside the outline", async () => {
  view(CHILD);
  const details = await screen.findByRole("region", { name: "Step details" });
  expect(within(details).getByRole("heading", { name: "Child" })).toBeVisible();
  expect(within(details).getByRole("link", { name: "Parent" })).toHaveAttribute(
    "href",
    `#roadmaps/${ROADMAP}/${PARENT}`,
  );
  const outline = screen.getByRole("region", { name: "Plan outline" });
  expect(within(outline).getByRole("link", { name: "Child" })).toHaveAttribute(
    "aria-current",
    "true",
  );
  expect(
    within(outline).getByRole("link", { name: "Solo" }),
  ).not.toHaveAttribute("aria-current");
});

it("falls back to the roadmap overview for a step that is not in the plan", async () => {
  view("bbbbbbbb-0000-4000-8000-000000000009");
  const details = await screen.findByRole("region", {
    name: "Roadmap details",
  });
  expect(within(details).getByText(/not in the current plan/)).toBeVisible();
  expect(
    within(details).getByRole("heading", { name: "Overview" }),
  ).toBeVisible();
});

it("remembers collapsed steps when the roadmap is opened again", async () => {
  const first = view();
  await userEvent.click(
    await screen.findByRole("button", { name: "Collapse Parent" }),
  );
  first.unmount();
  view();
  expect(
    await screen.findByRole("button", { name: "Expand Parent" }),
  ).toBeVisible();
  expect(screen.queryByText("Child")).toBeNull();
});

it("expands the collapsed parents of a step opened by link", async () => {
  const first = view();
  await userEvent.click(
    await screen.findByRole("button", { name: "Collapse Parent" }),
  );
  first.unmount();
  view(CHILD);
  const outline = await screen.findByRole("region", { name: "Plan outline" });
  expect(within(outline).getByRole("link", { name: "Child" })).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Collapse Parent" }),
  ).toHaveAttribute("aria-expanded", "true");
});

function chat(id: string, title: string, stepIds: string[]): AgentChat {
  return {
    id,
    revision: 3,
    ownerType: "roadmap",
    ownerId: ROADMAP,
    chatId: "conv-" + id.slice(-1),
    title,
    createdDate: "2026-10-31",
    provider: "",
    model: "",
    repos: [],
    stepIds,
  };
}
const API_CHAT = {
  ...chat("cccccccc-0000-4000-8000-000000000001", "Build the API", [PARENT]),
  provider: "Anthropic",
  model: "claude-opus-5-5",
  repos: ["acme/api", "acme/web"],
};
const DOCS_CHAT = {
  ...chat("cccccccc-0000-4000-8000-000000000002", "Write the docs", []),
  provider: "OpenAI",
  model: "gpt-5.5",
};
function recordWrites() {
  const writes: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const method = init?.method ?? "GET";
    if (method !== "GET")
      writes.push({
        url: String(input),
        method,
        body: JSON.parse(String(init?.body)),
      });
    return Response.json(
      String(input).endsWith("/roadmaps/" + ROADMAP)
        ? served
        : { items: [], total: 0 },
    );
  });
  return writes;
}

it("shows who is working on which step in the outline and the roadmap's chats", async () => {
  served = { ...road, chats: [API_CHAT, DOCS_CHAT] };
  view();
  const outline = await screen.findByRole("region", { name: "Plan outline" });
  const parentRow = within(outline)
    .getByRole("link", { name: "Parent" })
    .closest(".step-row") as HTMLElement;
  expect(within(parentRow).getByText("1 agent chat")).toBeVisible();
  expect(
    within(outline).queryByText(/agent chats?$/, { selector: "small" }),
  ).toBe(within(parentRow).getByText("1 agent chat"));

  const details = screen.getByRole("region", { name: "Roadmap details" });
  const apiChat = within(details)
    .getByText("Build the API")
    .closest(".chat-line") as HTMLElement;
  expect(
    within(apiChat).getByText("Anthropic · claude-opus-5-5"),
  ).toBeVisible();
  expect(within(apiChat).getByText("acme/web")).toBeVisible();
  expect(within(apiChat).getByRole("link", { name: "Parent" })).toHaveAttribute(
    "href",
    `#roadmaps/${ROADMAP}/${PARENT}`,
  );
});

it("assigns, unassigns, and adds agent chats from a step", async () => {
  served = { ...road, chats: [API_CHAT, DOCS_CHAT] };
  const writes = recordWrites();
  const child = view(CHILD);
  let details = await screen.findByRole("region", { name: "Step details" });
  expect(
    within(details).getByText("No agent chat is working on this step."),
  ).toBeVisible();
  expect(
    within(details).getByText(/is working on the parent/),
  ).toHaveTextContent(
    "Build the API (Anthropic · claude-opus-5-5) is working on the parent step Parent.",
  );
  await userEvent.selectOptions(
    within(details).getByRole("combobox", { name: "Agent chat to assign" }),
    DOCS_CHAT.id,
  );
  await userEvent.click(
    within(details).getByRole("button", { name: "Assign chat" }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: "/api/v1/chats/" + DOCS_CHAT.id,
      method: "PATCH",
      body: { revision: 3, stepIds: [CHILD] },
    }),
  );
  child.unmount();

  view(PARENT);
  details = await screen.findByRole("region", { name: "Step details" });
  await userEvent.click(
    within(details).getByRole("button", { name: "Unassign Build the API" }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: "/api/v1/chats/" + API_CHAT.id,
      method: "PATCH",
      body: { revision: 3, stepIds: [] },
    }),
  );
  await userEvent.type(
    within(details).getByRole("textbox", { name: "Agent chat title" }),
    "Review the API",
  );
  await userEvent.type(
    within(details).getByRole("textbox", { name: "Agent chat ID" }),
    "a950ec9c",
  );
  await userEvent.type(
    within(details).getByRole("combobox", { name: "Agent chat model" }),
    "claude-opus-5-5",
  );
  await userEvent.type(
    within(details).getByRole("textbox", { name: "Agent chat repositories" }),
    "acme/api",
  );
  await userEvent.click(
    within(details).getByRole("button", { name: "Add chat to step" }),
  );
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
        model: "claude-opus-5-5",
        repos: ["acme/api"],
        stepIds: [PARENT],
      },
    }),
  );
});

it("edits a chat's provider, model, and repositories in place", async () => {
  served = { ...road, chats: [API_CHAT] };
  const writes = recordWrites();
  view();
  const details = await screen.findByRole("region", {
    name: "Roadmap details",
  });
  await userEvent.click(
    await within(details).findByRole("button", {
      name: "Edit chat Build the API",
    }),
  );
  const model = within(details).getByRole("combobox", {
    name: "Edited chat model",
  });
  expect(model).toHaveValue("claude-opus-5-5");
  await userEvent.clear(model);
  await userEvent.type(model, "claude-sonnet-5-5");
  await userEvent.clear(
    within(details).getByRole("textbox", { name: "Edited chat repositories" }),
  );
  await userEvent.click(
    within(details).getByRole("button", { name: "Save chat" }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      url: "/api/v1/chats/" + API_CHAT.id,
      method: "PATCH",
      body: {
        revision: 3,
        title: "Build the API",
        chatId: API_CHAT.chatId,
        provider: "Anthropic",
        model: "claude-sonnet-5-5",
        repos: [],
        createdDate: "2026-10-31",
      },
    }),
  );
});
