import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { contextStorageKey, setDataContext } from "./api";
import { StructureEditor } from "./Roadmaps";
import type { Roadmap, Step } from "./types";

const ROADMAP = "22222222-2222-4222-8222-222222222222";
function step(id: string, title: string, position: number): Step {
  return {
    id,
    title,
    description: "",
    parentId: null,
    position,
    status: "open",
    revision: 1,
    retired: false,
  };
}
const A = step("aaaaaaaa-0000-4000-8000-000000000001", "Alpha", 0);
const B = step("aaaaaaaa-0000-4000-8000-000000000002", "Beta", 1);
const C = step("aaaaaaaa-0000-4000-8000-000000000003", "Gamma", 2);
function roadmap(revision: number, steps: Step[]): Roadmap {
  return {
    id: ROADMAP,
    revision,
    title: "Plan",
    overview: "",
    steps,
    versions: [],
    notes: [],
    attachments: [],
    history: [],
  };
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
