import { afterEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { RepositoryBadges } from "./RepositoryControls";

afterEach(() => vi.restoreAllMocks());

it("shows a chat's old label only where no repository linked there has its name", async () => {
  setDataContext("data-1");
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(
        JSON.stringify({ id: "repo-1", name: "Worklog", archived: false }),
        { status: 200 },
      ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RepositoryBadges ids={["repo-1"]} legacy={["Worklog", "parser"]} />
    </QueryClientProvider>,
  );
  // Until the linked repository's name is read, no label claims to be unlinked.
  expect(screen.queryByText(/Not linked/)).toBeNull();
  expect(await screen.findByRole("link", { name: "Worklog" })).toHaveAttribute(
    "href",
    "#repositories/repo-1",
  );
  // Schema v24 linked the label that named the repository; the other names none linked here.
  expect(await screen.findByText("parser · Not linked")).toBeInTheDocument();
  expect(screen.queryByText("Worklog · Not linked")).toBeNull();
});

it("shows nothing for a chat with no repositories and no labels", () => {
  const { container } = render(
    <QueryClientProvider client={new QueryClient()}>
      <RepositoryBadges ids={[]} legacy={[]} />
    </QueryClientProvider>,
  );
  expect(container).toBeEmptyDOMElement();
});
