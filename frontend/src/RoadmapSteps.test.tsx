import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
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
