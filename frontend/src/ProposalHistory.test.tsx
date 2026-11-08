import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { AgentProposals, AppliedProposals } from "./ProposalHistory";
import type { ProposalEntry } from "./apiTypes";

const applied: ProposalEntry = {
  number: 2,
  id: "proposal-2",
  title: "Finish the fix",
  status: "approved",
  autoApproved: false,
  actorLabel: "Reviewer",
  agentId: "agent-2",
  createdAt: "2026-11-03T09:00:00Z",
  submittedAt: "2026-11-03T09:05:00Z",
  appliedAt: "2026-11-03T10:42:00Z",
  updatedAt: "2026-11-03T10:42:00Z",
  operationCount: 4,
  changes: [
    { number: 1, action: "task.create" },
    { number: 3, action: "task.create" },
    { number: 4, action: "item.update" },
  ],
};
const automatic: ProposalEntry = {
  ...applied,
  number: 1,
  id: "proposal-1",
  title: "Start the fix",
  autoApproved: true,
  actorLabel: "You",
  agentId: null,
  operationCount: 1,
  changes: [{ number: 1, action: "item.update" }],
};
function show(component: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {component}
    </QueryClientProvider>,
  );
}
const json = (value: unknown) =>
  new Response(JSON.stringify(value), { status: 200 });
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("proposal-history-test-" + crypto.randomUUID());
});

it("lists what was applied to a record, numbered, with who proposed it and what it changed there", async () => {
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(json({ items: [applied, automatic], total: 2 }));
  show(<AppliedProposals path="/work-items/item-1" noun="work item" />);
  const summary = await screen.findByText("Applied proposals (2)");
  expect(String(network.mock.calls[0][0])).toContain(
    "/api/v1/work-items/item-1/proposals?limit=50&offset=0",
  );
  await userEvent.click(summary);
  const list = screen.getByRole("list", { name: "Applied proposals" });
  const [latest, first] = within(list).getAllByRole("listitem");
  expect(latest).toHaveTextContent(/^2\.Finish the fix/);
  expect(
    within(latest).getByRole("link", { name: "Finish the fix" }),
  ).toHaveAttribute("href", "#review/proposal-2");
  expect(
    within(latest).getByRole("link", { name: "Reviewer" }),
  ).toHaveAttribute("href", "#agents/agent-2");
  expect(latest).toHaveTextContent(
    /Reviewer · Applied .* · Changes 1, 3, 4 of 4: task\.create, item\.update$/,
  );
  // Each one was applied, so only an automatic approval is called out.
  expect(within(latest).queryByText("approved")).not.toBeInTheDocument();
  expect(first).toHaveTextContent(/^1\.Start the fix/);
  expect(within(first).getByText("approved automatically")).toBeInTheDocument();
  expect(within(first).queryByRole("link", { name: "You" })).toBeNull();
  expect(first).toHaveTextContent(
    /You · Applied .* · Change 1 of 1: item\.update$/,
  );
});

it("says when nothing was applied to a record yet", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    json({ items: [], total: 0 }),
  );
  show(<AppliedProposals path="/roadmaps/road-1" noun="roadmap" />);
  await userEvent.click(await screen.findByText("Applied proposals (0)"));
  expect(
    screen.getByText("No agent proposal has changed this roadmap yet."),
  ).toBeInTheDocument();
});

it("lists everything an agent proposed with where each one got to, a page at a time", async () => {
  const entries = [
    {
      ...applied,
      number: 4,
      id: "p-4",
      title: "Thinking",
      status: "draft",
      submittedAt: null,
      appliedAt: null,
      operationCount: 0,
    },
    {
      ...applied,
      number: 3,
      id: "p-3",
      title: "Waiting",
      status: "submitted",
      appliedAt: null,
      operationCount: 1,
    },
    {
      ...applied,
      number: 2,
      id: "p-2",
      title: "Rename it",
      status: "rejected",
      appliedAt: null,
    },
    { ...applied, number: 1, id: "p-1", title: "Start", autoApproved: true },
  ].map(({ changes: _, ...entry }) => entry);
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input) =>
      json({
        items: String(input).includes("offset=50") ? [] : entries,
        total: 51,
      }),
    );
  show(<AgentProposals id="agent-2" />);
  const section = await screen.findByRole("region", { name: "Proposals" });
  const list = await within(section).findByRole("list", {
    name: "Proposals from this agent",
  });
  const rows = within(list).getAllByRole("listitem");
  expect(rows.map((row) => row.textContent?.split("·")[0])).toEqual([
    expect.stringMatching(/^4\.Thinkingdraft\s*Drafted /),
    expect.stringMatching(/^3\.Waitingsubmitted\s*Submitted /),
    expect.stringMatching(/^2\.Rename itrejected\s*Rejected /),
    expect.stringMatching(/^1\.Startapproved automatically\s*Applied /),
  ]);
  expect(rows[0]).toHaveTextContent(/· 0 proposed changes$/);
  expect(rows[1]).toHaveTextContent(/· 1 proposed change$/);
  // An agent's own page doesn't repeat its name on every row.
  expect(within(list).queryByRole("link", { name: "Reviewer" })).toBeNull();
  await userEvent.click(
    within(section).getByRole("button", { name: "Next proposals" }),
  );
  expect(String(network.mock.calls.at(-1)?.[0])).toContain(
    "/api/v1/agents/agent-2/proposals?limit=50&offset=50",
  );
});
