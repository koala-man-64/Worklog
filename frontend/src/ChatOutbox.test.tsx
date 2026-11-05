import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { contextStorageKey, setDataContext } from "./api";
import { ChatOutbox, deliveryState } from "./ChatOutbox";
import type {
  Outbox,
  OutboxDelivery,
  OutboxMessage,
  OutboxRecipient,
  OutboxRun,
} from "./outboxTypes";

const CHAT = { id: "chat-1", chatId: "session-1" };
const implementer: OutboxRecipient = {
  runId: "run-1",
  runName: "Parser fixes",
  provider: "codex",
  startedAt: "2026-11-01T10:00:00+00:00",
  agentId: "agent-1",
  agentLabel: "Implementer",
  avatar: null,
  binding: "registered",
};
// A run of another agent that reports this chat's ID without being bound to it.
const claimant: OutboxRun = {
  runId: "run-2",
  runName: "Claims the chat",
  provider: "claude",
  startedAt: "2026-11-01T10:01:00+00:00",
  agentId: "agent-2",
  agentLabel: "Other agents",
  avatar: null,
};
const pending: OutboxDelivery = {
  runId: "run-1",
  runName: "Parser fixes",
  agentId: "agent-1",
  agentLabel: "Implementer",
  avatar: null,
  status: "pending",
  reason: null,
  expiresAt: "2026-11-01T12:00:00+00:00",
  deliveredAt: null,
  expiredAt: null,
};
function message(
  text: string,
  deliveries: OutboxDelivery[] = [pending],
): OutboxMessage {
  return {
    id: "message-" + text,
    chatId: CHAT.id,
    text,
    authorId: "local-user",
    authorLabel: "You",
    createdAt: "2026-11-01T10:05:00+00:00",
    deliveries,
  };
}
function outbox(fields: Partial<Outbox> = {}): Outbox {
  return {
    chat: { ...CHAT, archived: false },
    recipients: [implementer],
    unconfirmed: [],
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
    ...fields,
  };
}

type Call = { method: string; path: string; body: unknown };
function serve(
  reads: () => Outbox,
  write: () => Response = () => new Response("{}", { status: 200 }),
) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    calls.push({
      method,
      path,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    });
    return method === "GET"
      ? new Response(JSON.stringify(reads()), { status: 200 })
      : write();
  });
  return calls;
}
function show() {
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
      <ChatOutbox chat={CHAT} refreshMs={60_000} time={(iso) => iso ?? ""} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  setDataContext("outbox-test-" + crypto.randomUUID());
});

it("names who a message reaches, sends it once and clears the draft", async () => {
  let current = outbox();
  const calls = serve(
    () => current,
    () => {
      current = outbox({
        items: [message("Use the revised format")],
        total: 1,
      });
      return new Response(JSON.stringify(current.items[0]), { status: 200 });
    },
  );
  show();
  const reaches = await screen.findByRole("list", {
    name: "Agents a message reaches",
  });
  expect(reaches).toHaveTextContent("Implementer");
  expect(reaches).toHaveTextContent("Parser fixes");
  const box = screen.getByRole("textbox", {
    name: "Message to this chat's agents",
  });
  await userEvent.type(box, "Use the revised format");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(box).toHaveValue(""));
  const post = calls.find((call) => call.method === "POST");
  expect(post).toEqual({
    method: "POST",
    path: "/chats/chat-1/messages",
    body: { text: "Use the revised format" },
  });
  const sent = await screen.findByRole("list", { name: "Messages you sent" });
  expect(sent).toHaveTextContent("Use the revised format");
  expect(
    within(sent).getByText("Implementer: Waiting for pickup"),
  ).toBeInTheDocument();
  expect(
    localStorage.getItem(contextStorageKey("chat-message-draft", CHAT.id)),
  ).toBeNull();
});

it("lists a run that only claims the chat apart, and confirms its agent", async () => {
  let current = outbox({ unconfirmed: [claimant] });
  const calls = serve(
    () => current,
    () => {
      current = outbox({
        recipients: [implementer, { ...claimant, binding: "confirmed" }],
      });
      return new Response("{}", { status: 200 });
    },
  );
  show();
  const waiting = await screen.findByRole("list", {
    name: "Agents waiting for your confirmation",
  });
  expect(waiting).toHaveTextContent("Other agents");
  expect(
    screen.getByRole("list", { name: "Agents a message reaches" }),
  ).not.toHaveTextContent("Other agents");
  expect(screen.getByText(/so they receive nothing/)).toBeInTheDocument();
  await userEvent.click(
    within(waiting).getByRole("button", {
      name: "Confirm Other agents works in this chat",
    }),
  );
  expect(calls.find((call) => call.method === "POST")).toEqual({
    method: "POST",
    path: "/chats/chat-1/confirmations",
    body: { agentId: "agent-2" },
  });
  await waitFor(() =>
    expect(
      screen.getByRole("list", { name: "Agents a message reaches" }),
    ).toHaveTextContent("Other agents"),
  );
  expect(
    screen.getByRole("list", { name: "Agents a message reaches" }),
  ).toHaveTextContent("confirmed by you");
  expect(
    screen.queryByRole("list", { name: "Agents waiting for your confirmation" }),
  ).not.toBeInTheDocument();
});

it("removes a confirmation the person gave", async () => {
  const calls = serve(() =>
    outbox({ recipients: [implementer, { ...claimant, binding: "confirmed" }] }),
  );
  show();
  const reaches = await screen.findByRole("list", {
    name: "Agents a message reaches",
  });
  // An agent bound by registering with the chat has no confirmation to remove.
  expect(
    within(reaches).getAllByRole("button").map((b) => b.getAttribute("aria-label")),
  ).toEqual(["Remove your confirmation of Other agents"]);
  await userEvent.click(within(reaches).getByRole("button"));
  await waitFor(() =>
    expect(calls.find((call) => call.method === "POST")).toEqual({
      method: "POST",
      path: "/chats/chat-1/confirmations/remove",
      body: { agentId: "agent-2" },
    }),
  );
});

it("cannot send when the only runs on the chat are unconfirmed", async () => {
  serve(() => outbox({ recipients: [], unconfirmed: [claimant] }));
  show();
  expect(
    await screen.findByText(/No agent bound to this chat has an open run/),
  ).toBeInTheDocument();
  await userEvent.type(
    screen.getByRole("textbox", { name: "Message to this chat's agents" }),
    "Anyone there?",
  );
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
});

it("cannot send when no open agent run reports the chat", async () => {
  serve(() => outbox({ recipients: [] }));
  show();
  expect(
    await screen.findByText(/No open agent run reports this chat's ID/),
  ).toBeInTheDocument();
  expect(
    screen.getByText("worklog agent runs start --chat session-1"),
  ).toBeInTheDocument();
  await userEvent.type(
    screen.getByRole("textbox", { name: "Message to this chat's agents" }),
    "Anyone there?",
  );
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
});

it("offers no composer for an archived chat", async () => {
  serve(() => outbox({ chat: { ...CHAT, archived: true }, recipients: [] }));
  show();
  expect(await screen.findByText(/This chat is archived/)).toHaveTextContent(
    "Worklog never recovers a chat to deliver one",
  );
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

it("keeps the draft when sending fails and after a reload", async () => {
  serve(
    () => outbox(),
    () =>
      new Response(
        JSON.stringify({
          error: {
            code: "no_recipients",
            message:
              "No open agent run reports this chat's ID, so the message would reach no one",
          },
        }),
        { status: 409 },
      ),
  );
  const view = show();
  const box = await screen.findByRole("textbox", {
    name: "Message to this chat's agents",
  });
  await screen.findByRole("list", { name: "Agents a message reaches" });
  await userEvent.type(box, "Keep this");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "would reach no one",
  );
  expect(box).toHaveValue("Keep this");
  view.unmount();
  show();
  expect(
    await screen.findByRole("textbox", {
      name: "Message to this chat's agents",
    }),
  ).toHaveValue("Keep this");
});

it("says what became of each delivery", () => {
  const time = (iso: string | null) => (iso ? "10:30" : "");
  expect(deliveryState(pending, time)).toBe("Waiting for pickup");
  expect(
    deliveryState(
      { ...pending, status: "delivered", deliveredAt: "2026-11-01T10:30:00Z" },
      time,
    ),
  ).toBe("Delivered 10:30");
  for (const [reason, text] of [
    ["run_ended", "the run ended"],
    ["human_closed", "you ended the run"],
    ["credential_revoked", "its credential was revoked"],
    ["credential_rotated", "its credential was replaced"],
    ["credential_retired", "its agent retired"],
    ["chat_archived", "the chat was archived"],
    ["chat_changed", "the chat's ID changed"],
    ["restore", "Worklog was restored from a backup"],
    ["unconfirmed", "its agent isn't confirmed in this chat"],
    ["not_picked_up", "not picked up within 2 hours"],
  ] as const)
    expect(deliveryState({ ...pending, status: "expired", reason }, time)).toBe(
      "Not delivered: " + text,
    );
});
