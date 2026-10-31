import { beforeEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { contextStorageKey, setDataContext } from "./api";
import { RoadmapNav } from "./RoadmapNav";

const A = "22222222-2222-4222-8222-222222222221";
const B = "22222222-2222-4222-8222-222222222222";

function mockRoadmaps(
  items: { id: string; title: string; parentId?: string | null }[],
) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    Response.json({ items, total: items.length }),
  );
}
function view(selected?: string) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RoadmapNav selected={selected} />
    </QueryClientProvider>
  );
}
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("roadmap-nav-" + crypto.randomUUID());
});

it("links each roadmap and marks only the selected one as current", async () => {
  mockRoadmaps([
    { id: A, title: "Platform" },
    { id: B, title: "" },
  ]);
  render(view(B));
  const platform = await screen.findByRole("link", { name: "Platform" });
  expect(platform).toHaveAttribute("href", "#roadmaps/" + A);
  expect(platform).not.toHaveAttribute("aria-current");
  const untitled = screen.getByRole("link", { name: "Untitled" });
  expect(untitled).toHaveAttribute("href", "#roadmaps/" + B);
  expect(untitled).toHaveAttribute("aria-current", "page");
});

it("nests sub-roadmaps under their parents and lifts orphans to the top", async () => {
  const C = "22222222-2222-4222-8222-222222222223";
  mockRoadmaps([
    { id: A, title: "Platform", parentId: null },
    { id: B, title: "Storage", parentId: A },
    {
      id: C,
      title: "Orphan",
      parentId: "22222222-2222-4222-8222-999999999999",
    },
  ]);
  render(view(B));
  const storage = await screen.findByRole("link", { name: "Storage" });
  expect(storage).toHaveAttribute("aria-current", "page");
  const top = screen.getByRole("list", { name: "Roadmaps" });
  expect(
    [...top.children].map((li) => li.querySelector("a")?.textContent),
  ).toEqual(["Platform", "Orphan"]);
  const nested = storage.closest("ul");
  expect(nested).not.toBe(top);
  expect(nested?.parentElement?.querySelector("a")).toHaveTextContent(
    "Platform",
  );
});

it("renders nothing when there are no roadmaps", async () => {
  mockRoadmaps([]);
  const { container } = render(view());
  await vi.waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

it("hides the list but keeps it addressable when closed", async () => {
  mockRoadmaps([{ id: A, title: "Platform" }]);
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RoadmapNav open={false} />
    </QueryClientProvider>,
  );
  await vi.waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  const list = await screen.findByRole("list", { hidden: true });
  expect(list).toHaveAttribute("id", "roadmap-subnav");
  expect(list).not.toBeVisible();
});

it("collapses a parent's sub-roadmaps and remembers it", async () => {
  mockRoadmaps([
    { id: A, title: "Platform" },
    { id: B, title: "Storage", parentId: A },
  ]);
  const { unmount } = render(view());
  await screen.findByRole("link", { name: "Storage" });
  await userEvent.click(
    screen.getByRole("button", { name: "Collapse Platform" }),
  );
  expect(screen.queryByRole("link", { name: "Storage" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Expand Platform" }),
  ).toHaveAttribute("aria-expanded", "false");
  unmount();
  render(view());
  await screen.findByRole("link", { name: "Platform" });
  expect(screen.queryByRole("link", { name: "Storage" })).toBeNull();
});

it("shows the selected roadmap inside a collapsed parent without forgetting the choice", async () => {
  const C = "22222222-2222-4222-8222-222222222223";
  const key = contextStorageKey("roadmaps-collapsed", "nav");
  localStorage.setItem(key, JSON.stringify([A, B]));
  mockRoadmaps([
    { id: A, title: "Platform" },
    { id: B, title: "Storage", parentId: A },
    { id: C, title: "Disks", parentId: B },
  ]);
  const { unmount } = render(view(C));
  const disks = await screen.findByRole("link", { name: "Disks" });
  expect(disks).toHaveAttribute("aria-current", "page");
  expect(JSON.parse(localStorage.getItem(key)!)).toEqual([A, B]);
  unmount();
  render(view());
  await screen.findByRole("link", { name: "Platform" });
  expect(screen.queryByRole("link", { name: "Storage" })).toBeNull();
});
