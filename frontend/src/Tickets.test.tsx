import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Tickets } from "./Tickets";
import type { DiscussionParticipants } from "./discussionTypes";
import type { Ticket, TicketDetail } from "./apiTypes";

const ID = "11111111-1111-4111-8111-111111111111";
const agent = {
  id: "agent-1",
  label: "Implementer",
  kind: "agent" as const,
  active: true,
  avatar: "teal-visor-antenna",
};
const participants: DiscussionParticipants = {
  items: [
    {
      id: "local-user",
      label: "You",
      kind: "human",
      active: true,
      avatar: null,
    },
    agent,
  ],
  self: { id: "local-user", label: "You", kind: "human" },
};
function ticket(fields: Partial<Ticket> = {}): Ticket {
  return {
    id: ID,
    number: 1,
    key: "T-1",
    title: "Backup holds a lock",
    description: "Seen at midnight",
    resolution: "",
    truncated: false,
    kind: "incident",
    priority: "urgent",
    status: "open",
    labels: ["sqlite"],
    repositoryIds: [],
    itemId: null,
    item: null,
    assigneeId: null,
    assignee: null,
    author: {
      id: agent.id,
      label: agent.label,
      kind: "agent",
      avatar: agent.avatar,
    },
    commentCount: 1,
    archived: false,
    revision: 2,
    createdAt: "2026-11-02T08:00:00Z",
    updatedAt: "2026-11-02T08:00:00Z",
    closedAt: null,
    lastActivityAt: "2026-11-02T09:00:00Z",
    ...fields,
  };
}
function detail(fields: Partial<TicketDetail> = {}): TicketDetail {
  return {
    ...ticket(),
    comments: {
      items: [
        {
          id: "comment-1",
          ticketId: ID,
          text: "Happens during the **nightly** snapshot.",
          createdAt: "2026-11-02T09:00:00Z",
          author: {
            id: agent.id,
            label: agent.label,
            kind: "agent",
            avatar: agent.avatar,
          },
        },
      ],
      total: 1,
    },
    history: [],
    ...fields,
  };
}
function show(component: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      {component}
    </QueryClientProvider>,
  );
}
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
type Write = { method: string; path: string; body: unknown };
/** Answers reads from `reads` by path prefix and records every write. */
function serve(
  reads: Record<string, () => unknown>,
  write: (w: Write) => Response = () => json(ticket()),
) {
  const writes: Write[] = [];
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    if (method !== "GET") {
      const w = { method, path, body: JSON.parse(String(init?.body)) };
      writes.push(w);
      return write(w);
    }
    paths.push(path);
    const match = Object.keys(reads)
      .sort((a, b) => b.length - a.length)
      .find((prefix) => path.startsWith(prefix));
    return json(match ? reads[match]() : { items: [], total: 0 });
  });
  return { writes, paths };
}
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("ticket-test-" + crypto.randomUUID());
});

it("lists active tickets by priority, narrows them by filters and files a new one", async () => {
  const { writes, paths } = serve({
    "/tickets": () => ({ items: [ticket()], total: 1 }),
    "/discussions/participants": () => participants,
  });
  show(<Tickets />);
  const link = await screen.findByRole("link", {
    name: "T-1 Backup holds a lock",
  });
  expect(link).toHaveAttribute("href", "#tickets/" + ID);
  const row = link.closest("article")!;
  expect(within(row as HTMLElement).getByText("Incident")).toBeInTheDocument();
  expect(within(row as HTMLElement).getByText("Urgent")).toBeInTheDocument();
  expect(within(row as HTMLElement).getByText("sqlite")).toBeInTheDocument();
  expect(paths).toContain(
    "/tickets?status=active&sort=priority&archived=false&limit=50&offset=0",
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Filter tickets by status" }),
    "done",
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Filter tickets by assignee" }),
    "agent-1",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Find tickets" }),
    "lock",
  );
  await waitFor(() =>
    expect(paths.at(-1)).toBe(
      "/tickets?status=done&sort=priority&archived=false&limit=50&offset=0&q=lock&assigneeId=agent-1",
    ),
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "New ticket title" }),
    "Import drops the last row",
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "New ticket priority" }),
    "high",
  );
  await userEvent.click(screen.getByRole("button", { name: "File ticket" }));
  await waitFor(() =>
    expect(writes).toEqual([
      {
        method: "POST",
        path: "/tickets",
        body: {
          title: "Import drops the last row",
          description: "",
          kind: "bug",
          priority: "high",
          status: "open",
          resolution: "",
          itemId: null,
        },
      },
    ]),
  );
  expect(screen.getByRole("textbox", { name: "New ticket title" })).toHaveValue(
    "",
  );
});

it("changes fields on the ticket's revision, explains why Done needs a resolution, and adds a comment", async () => {
  const { writes } = serve(
    {
      "/tickets/": () => detail(),
      "/discussions/participants": () => participants,
    },
    (w) =>
      w.path.endsWith("/comments")
        ? json({
            id: "comment-2",
            ticketId: ID,
            text: "x",
            createdAt: "",
            author: agent,
          })
        : (w.body as { status?: string }).status === "done"
          ? json(
              {
                error: {
                  code: "validation",
                  message: "Write a resolution before marking the ticket done",
                },
              },
              422,
            )
          : json(ticket({ revision: 3 })),
  );
  show(<Tickets selected={ID} />);
  expect(
    await screen.findByRole("heading", { name: "T-1 Backup holds a lock" }),
  ).toBeInTheDocument();
  expect(screen.getByText("nightly")).toBeInTheDocument();
  // Only work items wait: a ticket keeps its four states.
  expect(
    within(screen.getByRole("combobox", { name: "Status" }))
      .getAllByRole("option")
      .map((option) => option.getAttribute("value")),
  ).toEqual(["open", "doing", "blocked", "done"]);
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Status" }),
    "done",
  );
  expect(
    await screen.findByText(
      "Write a resolution before marking the ticket done",
    ),
  ).toBeInTheDocument();
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Priority" }),
    "low",
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Assignee" }),
    "agent-1",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Add a comment" }),
    "Reproduced with an empty file.",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add comment" }));
  await waitFor(() => expect(writes).toHaveLength(4));
  expect(writes).toEqual([
    {
      method: "PATCH",
      path: "/tickets/" + ID,
      body: { revision: 2, status: "done" },
    },
    {
      method: "PATCH",
      path: "/tickets/" + ID,
      body: { revision: 2, priority: "low" },
    },
    {
      method: "PATCH",
      path: "/tickets/" + ID,
      body: { revision: 2, assigneeId: "agent-1" },
    },
    {
      method: "POST",
      path: `/tickets/${ID}/comments`,
      body: { text: "Reproduced with an empty file." },
    },
  ]);
});

it("keeps an archived ticket read-only until it is recovered", async () => {
  const { writes } = serve({
    "/tickets/": () => detail({ archived: true }),
    "/discussions/participants": () => participants,
  });
  show(<Tickets selected={ID} />);
  expect(await screen.findByText(/Archived ticket/)).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Status" })).toBeDisabled();
  expect(
    screen.queryByRole("textbox", { name: "Add a comment" }),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Recover ticket" }));
  await waitFor(() =>
    expect(writes).toEqual([
      { method: "POST", path: `/tickets/${ID}/recover`, body: { revision: 2 } },
    ]),
  );
});

it("links and unlinks work, and edits labels as a whole set", async () => {
  const work = { id: "item-1", title: "Release checks", status: "open" };
  const { writes } = serve({
    "/tickets/": () => detail(),
    "/discussions/participants": () => participants,
    "/work-items": () => ({ items: [work], total: 1 }),
  });
  show(<Tickets selected={ID} />);
  await screen.findByRole("heading", { name: "T-1 Backup holds a lock" });
  await userEvent.type(
    screen.getByRole("textbox", { name: "New label" }),
    "CI{Enter}",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Remove label sqlite" }),
  );
  await userEvent.selectOptions(
    await screen.findByRole("combobox", { name: "Choose existing work" }),
    "item-1",
  );
  await userEvent.click(screen.getByRole("button", { name: "Link work item" }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes.map((w) => w.body)).toEqual([
    { revision: 2, labels: ["sqlite", "ci"] },
    { revision: 2, labels: [] },
    { revision: 2, itemId: "item-1" },
  ]);
});

it("loads a ticket's full change records only when its history is opened", async () => {
  const change = {
    id: "event-1",
    kind: "ticket.create",
    actorId: agent.id,
    actorLabel: agent.label,
    actorKind: "agent" as const,
    createdAt: "2026-11-02T08:00:00Z",
  };
  const { paths } = serve({
    "/tickets/": () => detail({ history: [change] }),
    "/discussions/participants": () => participants,
    "/history": () => ({
      items: [{ ...change, after: { title: "Recorded in full" } }],
    }),
  });
  show(<Tickets selected={ID} />);
  await screen.findByRole("heading", { name: "T-1 Backup holds a lock" });
  expect(paths.some((p) => p.startsWith("/history"))).toBe(false);
  await userEvent.click(screen.getByText("Activity history (1)"));
  expect(await screen.findByText(/Recorded in full/)).toBeInTheDocument();
  expect(paths).toContain("/history?entityId=" + ID);
});
