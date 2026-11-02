import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { AVATAR_COLORS } from "./Avatar";
import { Discussions } from "./Discussions";
import type {
  AcknowledgementRequest,
  DiscussionDetail,
  DiscussionList,
  DiscussionMessage,
  DiscussionParticipants,
  DiscussionRole,
  DiscussionRoles,
  DiscussionSummary,
} from "./discussionTypes";

const human = {
  id: "local-user",
  label: "You",
  kind: "human" as const,
  active: true,
  avatar: null,
};
const agentA = {
  id: "agent-a",
  label: "Agent Alpha",
  kind: "agent" as const,
  active: true,
  avatar: "teal-visor-antenna",
};
const agentB = {
  id: "agent-b",
  label: "Agent Beta",
  kind: "agent" as const,
  active: true,
  avatar: "amber-dots-ears",
};
const participants: DiscussionParticipants = {
  items: [human, agentA, agentB],
  self: { id: human.id, label: human.label, kind: human.kind },
};

function summary(fields: Partial<DiscussionSummary> = {}): DiscussionSummary {
  return {
    id: "discussion-1",
    title: "Project notes",
    archived: false,
    revision: 1,
    createdAt: "2026-11-01T10:00:00Z",
    updatedAt: "2026-11-01T10:00:00Z",
    contexts: [],
    repos: [],
    pendingCount: 1,
    ...fields,
  };
}

function requestState(
  fields: Partial<AcknowledgementRequest> = {},
): AcknowledgementRequest {
  return {
    id: "message-1",
    mode: "any_agent",
    state: "pending",
    targets: [
      {
        id: "open-target-1",
        kind: "open_agent",
        recipientId: "",
        label: "Any other agent",
        ownerId: null,
        ownershipGeneration: null,
        acknowledged: false,
        acknowledgedBy: null,
      },
    ],
    receipts: [],
    actionableTargetIds: [],
    canCancel: true,
    canRelease: true,
    ...fields,
  };
}

function message(fields: Partial<DiscussionMessage> = {}): DiscussionMessage {
  return {
    id: "message-1",
    discussionId: "discussion-1",
    text: "Please confirm the new convention.",
    authorId: human.id,
    authorLabel: human.label,
    authorKind: "human",
    createdAt: "2026-11-01T10:00:00Z",
    chatId: null,
    onBehalfOfRoleId: null,
    ownershipGeneration: null,
    request: requestState(),
    ...fields,
  };
}

const assignedRole: DiscussionRole = {
  id: "role-reviewer",
  contextType: "item",
  contextId: "item-22222222",
  label: "Reviewer",
  ownerId: agentB.id,
  ownerLabel: agentB.label,
  revision: 1,
  generation: 3,
  offer: null,
};
const unassignedRole: DiscussionRole = {
  id: "role-steward",
  contextType: "roadmap",
  contextId: "roadmap-33333333",
  contextTitle: "Launch readiness",
  label: "Data steward",
  ownerId: null,
  ownerLabel: null,
  revision: 1,
  generation: 0,
  offer: null,
};

type Call = { path: string; method: string; body: Record<string, unknown> };

function serve({
  initialSummary = summary(),
  initialMessages = [message()],
  initialRoles = [assignedRole, unassignedRole],
}: {
  initialSummary?: DiscussionSummary;
  initialMessages?: DiscussionMessage[];
  initialRoles?: DiscussionRole[];
} = {}) {
  let currentSummary = initialSummary;
  let currentMessages = [...initialMessages];
  let currentRoles = [...initialRoles];
  const calls: Call[] = [];
  const fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = new URL(String(input), "http://worklog.test");
      const path = url.pathname.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const body =
        typeof init?.body === "string"
          ? (JSON.parse(init.body) as Record<string, unknown>)
          : {};
      calls.push({ path, method, body });
      const json = (value: unknown) =>
        new Response(JSON.stringify(value), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });

      if (method === "GET") {
        if (path === "/discussions") {
          const view = url.searchParams.get("view");
          const shown =
            view === "awaiting"
              ? currentSummary.pendingCount > 0
                ? [currentSummary]
                : []
              : view === "archived"
                ? currentSummary.archived
                  ? [currentSummary]
                  : []
                : currentSummary.archived
                  ? []
                  : [currentSummary];
          return json({
            items: shown,
            total: shown.length,
          } satisfies DiscussionList);
        }
        if (path === "/discussions/participants") return json(participants);
        if (path === "/discussions/roles")
          return json({
            items: currentRoles,
            total: currentRoles.length,
          } satisfies DiscussionRoles);
        if (path === "/chats") return json({ items: [], total: 0 });
        if (path === "/discussions/discussion-1")
          return json({
            discussion: currentSummary,
            messages: currentMessages,
            total: currentMessages.length,
          } satisfies DiscussionDetail);
        return json({ items: [], total: 0 });
      }

      if (method === "POST" && path === "/discussions") {
        const created = summary({
          id: "created-discussion",
          title: String(body.title),
          pendingCount: body.acknowledgement ? 1 : 0,
        });
        const createdMessage = message({
          id: "created-message",
          discussionId: created.id,
          text: String(body.text),
          request: body.acknowledgement
            ? requestState({
                id: "created-message",
                mode: (
                  body.acknowledgement as {
                    mode: "all" | "any" | "any_agent" | "anyone";
                  }
                ).mode,
              })
            : null,
        });
        currentSummary = created;
        currentMessages = [createdMessage];
        return json({ discussion: created, message: createdMessage });
      }

      if (method === "POST" && path === "/discussions/discussion-1/messages") {
        const reply = message({
          id: "message-2",
          text: String(body.text),
          request: null,
          createdAt: "2026-11-01T10:05:00Z",
        });
        currentMessages = [...currentMessages, reply];
        currentSummary = {
          ...currentSummary,
          updatedAt: reply.createdAt,
          revision: currentSummary.revision + 1,
        };
        return json(reply);
      }

      if (method === "POST" && path === "/discussions/discussion-1/archive") {
        currentSummary = {
          ...currentSummary,
          archived: Boolean(body.archived),
          revision: currentSummary.revision + 1,
        };
        return json(currentSummary);
      }

      if (
        method === "POST" &&
        path === "/discussions/messages/message-1/release"
      ) {
        const updated = message({
          request: requestState({
            state: "released_by_human",
            reason: String(body.reason),
            terminalActorLabel: "You",
          }),
        });
        currentMessages = currentMessages.map((entry) =>
          entry.id === updated.id ? updated : entry,
        );
        currentSummary = {
          ...currentSummary,
          pendingCount: 0,
          updatedAt: "2026-11-01T10:06:00Z",
        };
        return json(updated);
      }

      if (
        method === "POST" &&
        path === "/discussions/messages/message-1/cancel"
      ) {
        const updated = message({
          request: requestState({
            state: "cancelled",
            terminalActorLabel: "You",
          }),
        });
        currentMessages = currentMessages.map((entry) =>
          entry.id === updated.id ? updated : entry,
        );
        currentSummary = { ...currentSummary, pendingCount: 0 };
        return json(updated);
      }

      if (
        method === "POST" &&
        path.startsWith("/discussions/roles/") &&
        path.endsWith("/reassign")
      ) {
        const owner = participants.items.find(
          (entry) => entry.id === body.ownerId,
        );
        const id = path.split("/")[3];
        currentRoles = currentRoles.map((role) =>
          role.id === id
            ? {
                ...role,
                ownerId: (body.ownerId as string | null) ?? null,
                ownerLabel: owner?.label ?? null,
                revision: role.revision + 1,
                generation: role.generation + 1,
                offer: null,
              }
            : role,
        );
        return json(currentRoles.find((role) => role.id === id));
      }

      return json({});
    });
  return { calls, fetchSpy };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Discussions />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  setDataContext("discussion-test-" + crypto.randomUUID());
});

it("posts plain text with a derived title and explicit named recipients, including an unassigned role", async () => {
  const user = userEvent.setup();
  const api = serve();
  renderPage();

  await screen.findByRole("region", { name: "Discussion list" });
  await user.click(screen.getByRole("button", { name: "New post" }));
  await user.type(
    screen.getByRole("textbox", { name: "Post message" }),
    "Pack release files.\nKeep their hashes with the bundle.",
  );
  await user.type(
    screen.getByLabelText("Repository labels (optional)"),
    "worklog, docs",
  );
  await user.click(
    screen.getByRole("checkbox", {
      name: "Request acknowledgement (received and understood)",
    }),
  );
  await user.selectOptions(
    screen.getByLabelText("Who may acknowledge?"),
    "all",
  );
  await user.click(screen.getByRole("checkbox", { name: "Agent Alpha" }));
  await user.click(
    screen.getByRole("checkbox", {
      name: /Data steward.*Launch readiness.*unassigned/,
    }),
  );
  await user.click(screen.getByRole("button", { name: "Post discussion" }));

  await screen.findByRole("heading", { name: "Pack release files." });
  const created = api.calls.find(
    (call) => call.path === "/discussions" && call.method === "POST",
  );
  expect(created?.body).toMatchObject({
    title: "Pack release files.",
    text: "Pack release files.\nKeep their hashes with the bundle.",
    repos: ["worklog", "docs"],
    acknowledgement: {
      mode: "all",
      targets: [
        { kind: "agent", id: agentA.id },
        { kind: "role", id: unassignedRole.id },
      ],
    },
  });
});

it("keeps human replies separate from acknowledgement and records a human release reason", async () => {
  const user = userEvent.setup();
  const api = serve();
  renderPage();
  await user.click(await screen.findByRole("link", { name: /Project notes/ }));
  await screen.findByRole("heading", { name: "Project notes" });
  const requestCard = screen.getByRole("region", {
    name: "Acknowledgement request",
  });
  expect(within(requestCard).getByText("Awaiting")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", {
      name: "Acknowledge: received and understood",
    }),
  ).not.toBeInTheDocument();

  await user.type(
    screen.getByRole("textbox", { name: "Reply message" }),
    "I reviewed the convention.",
  );
  await user.click(screen.getByRole("button", { name: "Send reply" }));
  await screen.findByText("I reviewed the convention.");
  await waitFor(() =>
    expect(
      api.calls.find(
        (call) => call.path === "/discussions/discussion-1/messages",
      )?.body,
    ).toMatchObject({
      text: "I reviewed the convention.",
      acknowledgement: null,
    }),
  );
  expect(within(requestCard).getByText("Awaiting")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Release wait…" }));
  await user.type(
    screen.getByRole("textbox", { name: "Reason for releasing this wait" }),
    "The information is no longer needed.",
  );
  await user.click(screen.getByRole("button", { name: "Release wait" }));
  await screen.findByText("Released by human");
  expect(
    api.calls.find(
      (call) => call.path === "/discussions/messages/message-1/release",
    )?.body,
  ).toEqual({ reason: "The information is no longer needed." });
});

it("shows each author's avatar beside their messages", async () => {
  const user = userEvent.setup();
  serve({
    initialMessages: [
      message(),
      message({
        id: "message-2",
        text: "Alpha here.",
        authorId: agentA.id,
        authorLabel: agentA.label,
        authorKind: "agent",
        request: null,
      }),
      message({
        id: "message-3",
        text: "Beta here.",
        authorId: agentB.id,
        authorLabel: agentB.label,
        authorKind: "agent",
        request: null,
      }),
    ],
  });
  renderPage();
  await user.click(await screen.findByRole("link", { name: /Project notes/ }));
  const avatarOf = async (text: string) =>
    (await screen.findByText(text))
      .closest(".discussion-message")
      ?.querySelector(".discussion-message-head svg.avatar");
  expect(await avatarOf("Please confirm the new convention.")).toHaveClass(
    "human",
  );
  expect(
    (await avatarOf("Alpha here."))?.querySelector("circle"),
  ).toHaveAttribute("fill", AVATAR_COLORS.teal);
  expect(
    (await avatarOf("Beta here."))?.querySelector("circle"),
  ).toHaveAttribute("fill", AVATAR_COLORS.amber);
});

it("keeps archived pending threads visible in Awaiting and replyable", async () => {
  const user = userEvent.setup();
  const archived = summary({ archived: true, title: "Archived but waiting" });
  const api = serve({ initialSummary: archived });
  renderPage();
  await user.click(screen.getByRole("button", { name: "Awaiting" }));
  await user.click(
    await screen.findByRole("link", { name: /Archived but waiting/ }),
  );
  expect(
    await screen.findByText("Archived · replies are open"),
  ).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Reply message" })).toBeEnabled();
  await user.type(
    screen.getByRole("textbox", { name: "Reply message" }),
    "A later human reply.",
  );
  await user.click(screen.getByRole("button", { name: "Send reply" }));
  await screen.findByText("A later human reply.");
  expect(
    api.fetchSpy.mock.calls.some(([input]) =>
      String(input).includes("view=awaiting"),
    ),
  ).toBe(true);
});

it("shows a pending handoff and lets the human reassign with a reason", async () => {
  const user = userEvent.setup();
  const offeredRole = {
    ...assignedRole,
    offer: {
      id: "offer-1",
      fromId: agentA.id,
      toId: agentB.id,
      toLabel: agentB.label,
      note: "Please take the review lane.",
    },
  };
  const api = serve({ initialRoles: [offeredRole] });
  renderPage();
  await user.click(await screen.findByText("Discussion roles (1)"));
  expect(
    await screen.findByText("Handoff offered to Agent Beta"),
  ).toBeInTheDocument();
  const roleCard = screen.getByTestId(`discussion-role-${offeredRole.id}`);
  expect(roleCard).toHaveTextContent("Owner: Agent Beta");
  await user.click(screen.getByText("Reassign owner"));
  await user.selectOptions(screen.getByLabelText("New owner"), agentA.id);
  await user.type(
    screen.getByLabelText("Reason for reassignment"),
    "Return the review lane to the release agent.",
  );
  await user.click(screen.getByRole("button", { name: "Reassign role" }));
  await waitFor(() => expect(roleCard).toHaveTextContent("Owner: Agent Alpha"));
  expect(
    api.calls.find(
      (call) => call.path === `/discussions/roles/${offeredRole.id}/reassign`,
    )?.body,
  ).toMatchObject({
    revision: offeredRole.revision,
    ownerId: agentA.id,
    reason: "Return the review lane to the release agent.",
  });
});

it("retains a draft after remounting in the same data context", async () => {
  const user = userEvent.setup();
  serve();
  const view = renderPage();
  await user.click(screen.getByRole("button", { name: "New post" }));
  await user.type(
    screen.getByRole("textbox", { name: "Post message" }),
    "Saved in the browser.",
  );
  view.unmount();
  renderPage();
  await user.click(await screen.findByRole("button", { name: "New post" }));
  expect(
    await screen.findByRole("textbox", { name: "Post message" }),
  ).toHaveValue("Saved in the browser.");
});
