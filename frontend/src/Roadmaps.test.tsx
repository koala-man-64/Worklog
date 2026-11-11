import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { contextStorageKey, setDataContext } from "./api";
import { RoadmapDetail, Roadmaps, StructureEditor } from "./Roadmaps";
import type { Item, Roadmap, Step } from "./apiTypes";
import { anItem, aRoadmap, aStep } from "./fixtures";

const ROADMAP = "22222222-2222-4222-8222-222222222222";
function step(id: string, title: string, position: number): Step {
  return aStep({ id, title, position });
}
const A = step("aaaaaaaa-0000-4000-8000-000000000001", "Alpha", 0);
const B = step("aaaaaaaa-0000-4000-8000-000000000002", "Beta", 1);
const C = step("aaaaaaaa-0000-4000-8000-000000000003", "Gamma", 2);
function roadmap(revision: number, steps: Step[]): Roadmap {
  return aRoadmap({ id: ROADMAP, revision, title: "Plan", steps });
}
const draftKey = () => contextStorageKey("roadmap-structure", ROADMAP);
const storeDraft = (draft: object) =>
  localStorage.setItem(draftKey(), JSON.stringify(draft));
function view(road: Roadmap) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <StructureEditor roadmap={road} onDone={() => undefined} />
    </QueryClientProvider>
  );
}
const stepTitle = (n: number) =>
  screen.queryByRole("textbox", { name: `Step ${n} title` });
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("roadmap-test-" + crypto.randomUUID());
});
it("flags a draft whose base revision is behind the roadmap and blocks saving", () => {
  storeDraft({
    title: "Plan",
    overview: "",
    steps: [A],
    revision: 3,
    baseStepIds: [A.id],
  });
  render(view(roadmap(4, [A])));
  expect(screen.getByText(/based on revision 3/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Save new version" }),
  ).toBeDisabled();
});
it("discards a stale draft and loads the current structure", async () => {
  storeDraft({
    title: "Old title",
    overview: "",
    steps: [A],
    revision: 3,
    baseStepIds: [A.id],
  });
  render(view(roadmap(4, [A, B])));
  await userEvent.click(screen.getByRole("button", { name: "Discard draft" }));
  expect(localStorage.getItem(draftKey())).toBeNull();
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Plan");
  expect(stepTitle(2)).toHaveValue("Beta");
  expect(screen.queryByText(/based on revision/)).toBeNull();
});
it("rebases onto the current revision, keeping steps added elsewhere", async () => {
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  // The user retired Gamma and renamed Alpha; Beta was added on the server since.
  storeDraft({
    title: "Plan",
    overview: "",
    steps: [{ ...A, title: "Alpha edited" }],
    revision: 3,
    baseStepIds: [A.id, C.id],
  });
  render(view(roadmap(4, [A, B, C])));
  await userEvent.click(
    screen.getByRole("button", { name: "Rebase onto current" }),
  );
  expect(stepTitle(1)).toHaveValue("Alpha edited");
  expect(stepTitle(2)).toHaveValue("Beta");
  expect(stepTitle(3)).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Save new version" }),
  );
  await waitFor(() => expect(network).toHaveBeenCalled());
  const body = JSON.parse(String(network.mock.calls.at(-1)?.[1]?.body));
  expect(body.revision).toBe(4);
  expect(body.steps.map((s: Step) => s.title)).toEqual([
    "Alpha edited",
    "Beta",
  ]);
});
it("rebases keeping the current text of what the draft left as it found it", async () => {
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  // The user renamed Alpha. Since, Alpha was renamed elsewhere too (the user's name wins), an
  // agent's step.update gave Beta a description, Gamma moved under Alpha, and the roadmap was
  // retitled.
  storeDraft({
    title: "Plan",
    overview: "",
    steps: [{ ...A, title: "Alpha edited" }, B, C],
    revision: 3,
    baseStepIds: [A.id, B.id, C.id],
    base: {
      title: "Plan",
      overview: "",
      steps: Object.fromEntries(
        [A, B, C].map((s) => [
          s.id,
          { title: s.title, description: s.description, parentId: s.parentId },
        ]),
      ),
    },
  });
  render(
    view({
      ...roadmap(5, [
        { ...A, title: "Alpha from elsewhere", revision: 2 },
        { ...B, description: "AB#1559 is the predecessor", revision: 2 },
        { ...C, parentId: A.id },
      ]),
      title: "Plan, retitled",
    }),
  );
  expect(
    screen.getByText(/a title, description or parent you didn't change/),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Rebase onto current" }),
  );
  expect(stepTitle(1)).toHaveValue("Alpha edited");
  expect(
    screen.getByRole("textbox", { name: "Step 2 description" }),
  ).toHaveValue("AB#1559 is the predecessor");
  expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(
    "Plan, retitled",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Save new version" }),
  );
  await waitFor(() => expect(network).toHaveBeenCalled());
  const body = JSON.parse(String(network.mock.calls.at(-1)?.[1]?.body));
  expect(body.revision).toBe(5);
  expect(body.title).toBe("Plan, retitled");
  expect(
    body.steps.map((s: Step) => [s.title, s.description, s.parentId]),
  ).toEqual([
    ["Alpha edited", "", null],
    ["Beta", "AB#1559 is the predecessor", null],
    ["Gamma", "", A.id],
  ]);
});
it("rebases leaving a step retired elsewhere retired, unless the draft changed it", async () => {
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  // Since the draft started, Beta and Gamma were retired elsewhere; the draft renamed Gamma.
  storeDraft({
    title: "Plan",
    overview: "",
    steps: [A, B, { ...C, title: "Gamma kept" }],
    revision: 3,
    baseStepIds: [A.id, B.id, C.id],
    base: {
      title: "Plan",
      overview: "",
      steps: Object.fromEntries(
        [A, B, C].map((s) => [
          s.id,
          { title: s.title, description: s.description, parentId: s.parentId },
        ]),
      ),
    },
  });
  render(
    view(
      roadmap(4, [A, { ...B, retired: true }, { ...C, retired: true }]),
    ),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Rebase onto current" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Save new version" }),
  );
  await waitFor(() => expect(network).toHaveBeenCalled());
  const body = JSON.parse(String(network.mock.calls.at(-1)?.[1]?.body));
  expect(body.steps.map((s: Step) => s.title)).toEqual(["Alpha", "Gamma kept"]);
});
it("keeps a legacy draft's own text, and says so", () => {
  storeDraft({ title: "Plan", overview: "", steps: [A], revision: 3, baseStepIds: [A.id] });
  render(view(roadmap(4, [{ ...A, description: "From elsewhere" }])));
  expect(
    screen.getByText(/Rebase keeps your draft's version of every step/),
  ).toBeInTheDocument();
});
it("asks about server-only steps when a legacy draft has no recorded base", async () => {
  storeDraft({ title: "Plan", overview: "", steps: [A], revision: 3 });
  render(view(roadmap(4, [A, B, C])));
  expect(screen.getByRole("checkbox", { name: /Beta/ })).toBeChecked();
  await userEvent.click(screen.getByRole("checkbox", { name: /Gamma/ }));
  await userEvent.click(
    screen.getByRole("button", { name: "Rebase onto current" }),
  );
  expect(stepTitle(2)).toHaveValue("Beta");
  expect(stepTitle(3)).toBeNull();
});
it("records the base revision and step ids with a new draft", async () => {
  render(view(roadmap(4, [A, B])));
  await userEvent.type(stepTitle(1)!, "!");
  const stored = JSON.parse(localStorage.getItem(draftKey())!);
  expect(stored.revision).toBe(4);
  expect(stored.baseStepIds).toEqual([A.id, B.id]);
  // And the text it started from, which a rebase compares with.
  expect(stored.base.title).toBe("Plan");
  expect(stored.base.steps[A.id]).toEqual({
    title: "Alpha",
    description: "",
    parentId: null,
  });
});
it("edits one step's text alone, with the step's own revision", async () => {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const json = (data: unknown) =>
      new Response(JSON.stringify(data), { status: 200 });
    if (method !== "GET") {
      calls.push({ url, method, body: JSON.parse(String(init?.body)) });
      return json({ ...B, title: "Beta, renamed", revision: 3, roadmapId: ROADMAP });
    }
    if (url.includes(`/roadmaps/${ROADMAP}`))
      return json(roadmap(4, [A, { ...B, revision: 2 }, C]));
    return json({ items: [], total: 0 });
  });
  render(withClient(<RoadmapDetail id={ROADMAP} step={B.id} />));
  await userEvent.click(
    await screen.findByRole("button", { name: "Edit step" }),
  );
  const form = screen.getByRole("form", { name: "Edit step Beta" });
  const save = within(form).getByRole("button", { name: "Save step" });
  expect(save).toBeDisabled();
  const title = within(form).getByRole("textbox", { name: "Step title" });
  await userEvent.clear(title);
  await userEvent.type(title, "Beta, renamed ");
  await userEvent.type(
    within(form).getByRole("textbox", { name: "Description (Markdown)" }),
    "AB#1559",
  );
  await userEvent.click(save);
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0].url).toContain(`/api/v1/steps/${B.id}`);
  expect(calls[0].method).toBe("PATCH");
  expect(calls[0].body).toEqual({
    revision: 2,
    title: "Beta, renamed",
    description: "AB#1559",
  });
  // Saved, the form gives way to the step's heading: one or the other, never both.
  expect(
    await screen.findByRole("button", { name: "Edit step" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("form", { name: "Edit step Beta" })).toBeNull();
});
it("shows the step's new text before replacing it, when it changed meanwhile", async () => {
  const patches: unknown[] = [];
  let current = roadmap(4, [A, { ...B, revision: 2 }, C]);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const json = (data: unknown, status = 200) =>
      new Response(JSON.stringify(data), { status });
    if (method !== "GET") {
      const body = JSON.parse(String(init?.body));
      patches.push(body);
      if (body.revision !== 3)
        return json(
          {
            error: {
              code: "conflict",
              message: "This record changed. Reload and review your draft.",
            },
          },
          409,
        );
      return json({ ...B, ...body, revision: 4, roadmapId: ROADMAP });
    }
    if (url.includes(`/roadmaps/${ROADMAP}`)) return json(current);
    return json({ items: [], total: 0 });
  });
  render(withClient(<RoadmapDetail id={ROADMAP} step={B.id} />));
  await userEvent.click(
    await screen.findByRole("button", { name: "Edit step" }),
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Description (Markdown)" }),
    "Mine",
  );
  // An agent's step.update lands first.
  current = roadmap(5, [A, { ...B, description: "ADO rev 7", revision: 3 }, C]);
  await userEvent.click(screen.getByRole("button", { name: "Save step" }));
  expect(
    await screen.findByText(/text changed since you opened it/),
  ).toBeInTheDocument();
  expect(screen.getByText("ADO rev 7")).toBeInTheDocument();
  // What was typed here stays, and saving again replaces the new text knowingly.
  expect(
    screen.getByRole("textbox", { name: "Description (Markdown)" }),
  ).toHaveValue("Mine");
  await userEvent.click(screen.getByRole("button", { name: "Save step" }));
  await waitFor(() => expect(patches).toHaveLength(2));
  expect(patches).toEqual([
    { revision: 2, title: "Beta", description: "Mine" },
    { revision: 3, title: "Beta", description: "Mine" },
  ]);
});
it("offers a rebase, keeping the draft and note, when a save conflicts", async () => {
  let current = roadmap(4, [A, B]);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const json = (data: unknown, status = 200) =>
      new Response(JSON.stringify(data), { status });
    if ((init?.method ?? "GET") !== "GET")
      return json(
        {
          error: {
            code: "conflict",
            message: "This record changed. Reload and review your draft.",
          },
        },
        409,
      );
    if (url.includes(`/roadmaps/${ROADMAP}`)) return json(current);
    return json({ items: [], total: 0 });
  });
  render(withClient(<RoadmapDetail id={ROADMAP} />));
  await userEvent.click(
    await screen.findByRole("button", { name: "Edit structure" }),
  );
  await userEvent.type(stepTitle(1)!, "!");
  await userEvent.type(
    screen.getByRole("textbox", { name: "Version note" }),
    "Rename alpha",
  );
  // The roadmap moves on elsewhere, e.g. progress set by an approved proposal.
  current = roadmap(5, [A, B, C]);
  await userEvent.click(
    screen.getByRole("button", { name: "Save new version" }),
  );
  expect(
    await screen.findByText(
      /based on revision 4, but the roadmap is now at revision 5/,
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Rebase onto current" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Save new version" }),
  ).toBeDisabled();
  expect(stepTitle(1)).toHaveValue("Alpha!");
  expect(screen.getByRole("textbox", { name: "Version note" })).toHaveValue(
    "Rename alpha",
  );
});
it("reorders steps without mutating the cached roadmap", async () => {
  const road = roadmap(4, [step(A.id, "Alpha", 0), step(B.id, "Beta", 1)]);
  render(view(road));
  await userEvent.click(screen.getByRole("button", { name: "Move step 2 up" }));
  expect(stepTitle(1)).toHaveValue("Beta");
  expect(road.steps.map((s) => [s.title, s.position])).toEqual([
    ["Alpha", 0],
    ["Beta", 1],
  ]);
});
it("adds, attaches, reassigns, and unlinks work items on a roadmap", async () => {
  const existingItem: Item = anItem({
    id: "bbbbbbbb-0000-4000-8000-000000000001",
    revision: 2,
    title: "Existing item",
  });
  const linkedItem = anItem({
    id: "bbbbbbbb-0000-4000-8000-000000000002",
    title: "Roadmap item",
    roadmapId: ROADMAP,
  });
  const roadData: Roadmap = {
    ...roadmap(4, [A, B]),
    workItems: [linkedItem],
  };
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (
      (init?.method ?? "GET") === "GET" &&
      String(input).includes("/repositories")
    )
      return new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
      });
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && url.includes(`/roadmaps/${ROADMAP}`))
      return new Response(JSON.stringify(roadData), { status: 200 });
    if (method === "GET" && url.includes("/work-items"))
      return new Response(
        JSON.stringify({ items: [existingItem, linkedItem], total: 2 }),
        { status: 200 },
      );
    if (
      method === "GET" &&
      (url.includes("/people") ||
        url.includes("/roadmaps?") ||
        url.includes("/agent-links?") ||
        url.includes("/discussions/participants") ||
        url.includes("/releases?"))
    )
      return new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
      });
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(JSON.stringify({ id: "new", revision: 1 }), {
      status: 200,
    });
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RoadmapDetail id={ROADMAP} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Roadmap item")).toBeInTheDocument();

  // Create a new work item directly on the roadmap
  await userEvent.type(
    screen.getByRole("textbox", { name: "New roadmap work item" }),
    "Fresh roadmap task",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add work item" }));
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/work-items",
      method: "POST",
      body: {
        title: "Fresh roadmap task",
        description: "",
        roadmapId: ROADMAP,
        stepId: null,
        worked: false,
      },
    }),
  );

  // Attach an existing work item to step Alpha
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Choose existing work" }),
    existingItem.id,
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Step for existing work item" }),
    A.id,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Attach work item" }),
  );
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: `/api/v1/work-items/${existingItem.id}`,
      method: "PATCH",
      body: {
        revision: existingItem.revision,
        roadmapId: ROADMAP,
        stepId: A.id,
      },
    }),
  );

  // Unlink a work item from the roadmap
  await userEvent.click(
    screen.getByRole("button", { name: "Unlink Roadmap item" }),
  );
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: `/api/v1/work-items/${linkedItem.id}`,
      method: "PATCH",
      body: {
        revision: linkedItem.revision,
        roadmapId: null,
        stepId: null,
      },
    }),
  );
});
const TOP = "33333333-3333-4333-8333-333333333331";
const CHILD = "33333333-3333-4333-8333-333333333332";
const OTHER = "33333333-3333-4333-8333-333333333333";
const DEEP = "33333333-3333-4333-8333-333333333334";
const nestedList = [
  { id: TOP, title: "Platform", parentId: null, revision: 1 },
  { id: ROADMAP, title: "Plan", parentId: TOP, revision: 4 },
  { id: CHILD, title: "Storage", parentId: ROADMAP, revision: 1 },
  { id: OTHER, title: "Other", parentId: null, revision: 1 },
  { id: DEEP, title: "Deep", parentId: OTHER, revision: 1 },
];
function mockNested(roadData?: Roadmap) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const json = (data: unknown) =>
      new Response(JSON.stringify(data), { status: 200 });
    if (method === "GET" && roadData && url.includes(`/roadmaps/${ROADMAP}`))
      return json(roadData);
    if (method === "GET" && url.includes("/roadmaps?archived=false"))
      return json({ items: nestedList, total: nestedList.length });
    if (method === "GET") return json({ items: [], total: 0 });
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return json({ id: "new", revision: 1 });
  });
  return calls;
}
function withClient(node: React.ReactNode) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {node}
    </QueryClientProvider>
  );
}
it("lists sub-roadmaps nested under their parent roadmap", async () => {
  mockNested();
  render(withClient(<Roadmaps />));
  const storage = await screen.findByRole("link", { name: "Storage" });
  const plan = screen.getByRole("link", { name: "Plan" });
  const platformItem = screen
    .getByRole("link", { name: "Platform" })
    .closest("li")!;
  expect(platformItem).toContainElement(plan);
  expect(plan.closest("li")).toContainElement(storage);
  expect(platformItem).not.toContainElement(
    screen.getByRole("link", { name: "Other" }),
  );
});
it("shows where a roadmap sits, adds sub-roadmaps, and moves it", async () => {
  const calls = mockNested({
    ...roadmap(4, []),
    parentId: TOP,
    ancestors: [{ id: TOP, title: "Platform", archived: false }],
    children: [
      { id: CHILD, title: "Storage", archived: false },
      { id: DEEP, title: "Retired idea", archived: true },
    ],
  });
  render(withClient(<RoadmapDetail id={ROADMAP} />));
  const crumbs = await screen.findByRole("navigation", {
    name: "Roadmap location",
  });
  expect(
    within(crumbs).getByRole("link", { name: "Platform" }),
  ).toHaveAttribute("href", "#roadmaps/" + TOP);
  expect(screen.getByRole("link", { name: "Storage" })).toHaveAttribute(
    "href",
    "#roadmaps/" + CHILD,
  );
  expect(screen.getByText("Archived")).toBeInTheDocument();

  // Neither the roadmap itself nor anything below it can become its parent.
  const parent = screen.getByRole("combobox", { name: "Nested under" });
  await waitFor(() =>
    expect(
      within(parent)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Top level", "Platform", "Other", "  Deep"]),
  );
  expect(parent).toHaveValue(TOP);
  await userEvent.selectOptions(parent, OTHER);
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: `/api/v1/roadmaps/${ROADMAP}/move`,
      method: "POST",
      body: { revision: 4, parentId: OTHER },
    }),
  );

  await userEvent.type(
    screen.getByRole("textbox", { name: "New sub-roadmap title" }),
    "Backups",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Add sub-roadmap" }),
  );
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      url: "/api/v1/roadmaps",
      method: "POST",
      body: { title: "Backups", overview: "", steps: [], parentId: ROADMAP },
    }),
  );
});
it("keeps an archived parent selectable instead of showing the top level", async () => {
  mockNested({
    ...roadmap(4, []),
    parentId: "33333333-3333-4333-8333-999999999999",
    ancestors: [
      {
        id: "33333333-3333-4333-8333-999999999999",
        title: "Old program",
        archived: true,
      },
    ],
  });
  render(withClient(<RoadmapDetail id={ROADMAP} />));
  const parent = await screen.findByRole("combobox", { name: "Nested under" });
  expect(parent).toHaveValue("33333333-3333-4333-8333-999999999999");
  expect(
    within(parent).getByRole("option", { name: "Old program (archived)" }),
  ).toBeInTheDocument();
});
it("collapses a parent roadmap's sub-roadmaps in the list and remembers it", async () => {
  mockNested();
  const { unmount } = render(withClient(<Roadmaps />));
  await screen.findByRole("link", { name: "Storage" });
  await userEvent.click(
    screen.getByRole("button", { name: "Collapse Platform" }),
  );
  expect(screen.queryByRole("link", { name: "Plan" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Storage" })).toBeNull();
  expect(screen.getByText("(1 sub-roadmap hidden)")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Deep" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Expand Platform" }),
  ).toHaveAttribute("aria-expanded", "false");
  unmount();
  render(withClient(<Roadmaps />));
  await screen.findByRole("link", { name: "Other" });
  expect(screen.queryByRole("link", { name: "Plan" })).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Expand Platform" }),
  );
  expect(screen.getByRole("link", { name: "Storage" })).toBeInTheDocument();
});
