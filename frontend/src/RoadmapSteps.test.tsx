import { beforeEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RoadmapDetail } from "./Roadmaps";
import type { Roadmap, Step } from "./types";

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
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("roadmap-steps-" + crypto.randomUUID());
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    Response.json(
      String(input).endsWith("/roadmaps/" + ROADMAP)
        ? road
        : { items: [], total: 0 },
    ),
  );
});
const view = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RoadmapDetail id={ROADMAP} />
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
  await userEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  expect(screen.queryByText("Child")).toBeNull();
  expect(screen.getByRole("button", { name: "Collapse all" })).toBeDisabled();
  await userEvent.click(expand);
  expect(screen.getByText("Child")).toBeVisible();
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
