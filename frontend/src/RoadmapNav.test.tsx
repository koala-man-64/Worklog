import { beforeEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RoadmapNav } from "./RoadmapNav";

const A = "22222222-2222-4222-8222-222222222221";
const B = "22222222-2222-4222-8222-222222222222";

function mockRoadmaps(items: { id: string; title: string }[]) {
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

it("renders nothing when there are no roadmaps", async () => {
  mockRoadmaps([]);
  const { container } = render(view());
  await vi.waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});
