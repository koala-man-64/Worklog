import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Files } from "./Files";
function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {children}
    </QueryClientProvider>
  );
}
it("offers only an attached copy where local paths are not kept", () => {
  // Paths belong to work items alone; roadmaps, steps and proposals keep copies.
  render(<Files ownerType="roadmap" ownerId="r1" files={[]} />, { wrapper });
  expect(screen.getByLabelText("Attach a file")).toBeInTheDocument();
  expect(
    screen.queryByRole("group", { name: "How to add a file" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("textbox", { name: "Local path" }),
  ).not.toBeInTheDocument();
});
