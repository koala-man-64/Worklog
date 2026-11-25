import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { contextStorageKey, getDataContext, setDataContext } from "./api";
import type { AgentChat } from "./apiTypes";
import { StartSession, type SessionLaunch } from "./StartSession";
import { sessionStore } from "./storage";

const ITEM_ID = "11111111-1111-4111-8111-111111111111";
const LAUNCH_ID = "22222222-2222-4222-8222-222222222222";
const CHAT: AgentChat = {
  id: "33333333-3333-4333-8333-333333333333",
  ownerType: "item",
  ownerId: ITEM_ID,
  chatId: "desktop-chat-123",
  title: "Release checks",
  createdDate: "2026-11-13",
  archived: false,
  revision: 1,
  createdAt: "2026-11-13T12:00:00+00:00",
  updatedAt: "2026-11-13T12:00:00+00:00",
  provider: "codex",
  model: "",
  repos: [],
  stepIds: [],
  repositoryIds: [],
};

function launch(fields: Partial<SessionLaunch> = {}): SessionLaunch {
  return {
    id: LAUNCH_ID,
    provider: "codex",
    status: "ready",
    chat: CHAT,
    message: "Prepared.",
    ...fields,
  };
}

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

function mount(props: Partial<React.ComponentProps<typeof StartSession>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <StartSession
        itemId={ITEM_ID}
        title="Release checks"
        description="Confirm the gates."
        archived={false}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { client, unmount: view.unmount };
}

beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("start-session-test-" + crypto.randomUUID());
});

describe("StartSession", () => {
  it("validates that the folder is absolute and the prompt is not blank", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    expect(
      screen.getByText(
        /Preparation sends your prompt to the selected provider/,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        /Codex preparation is read-only.*workspace context may still load/,
      ),
    ).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Task prompt" })).toHaveValue(
      "Release checks\n\nConfirm the gates.",
    );
    expect(
      screen.getByRole("textbox", { name: "Working folder" }),
    ).toHaveProperty("maxLength", 2000);
    expect(screen.getByRole("textbox", { name: "Task prompt" })).toHaveProperty(
      "maxLength",
      12000,
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(
      await screen.findByText("Enter a working folder path."),
    ).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();

    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "relative/path",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(await screen.findByText(/absolute local Windows path/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();

    await userEvent.clear(
      screen.getByRole("textbox", { name: "Working folder" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "\\\\server\\share",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(await screen.findByText(/absolute local Windows path/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();

    await userEvent.clear(
      screen.getByRole("textbox", { name: "Working folder" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.clear(screen.getByRole("textbox", { name: "Task prompt" }));
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(await screen.findByText("Enter a task prompt.")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sends the selected provider, trimmed working folder, and exact prompt, then refreshes linked data", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response(launch()));
    const { client } = mount();
    client.setQueryData([getDataContext(), `/work-items/${ITEM_ID}`], {
      title: "old",
    });
    client.setQueryData([getDataContext(), "/chats?archived=false"], {
      items: [],
    });

    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Provider" }),
      "claude",
    );
    expect(
      screen.getByText(/Claude tools are disabled during preparation/),
    ).toBeTruthy();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "  C:\\Projects\\repo  ",
    );
    const taskPrompt = "Ship the release.\n\nCheck the last CI run.";
    await userEvent.clear(screen.getByRole("textbox", { name: "Task prompt" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Task prompt" }),
      taskPrompt,
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(fetch.mock.calls[0][0]).toBe(
      `/api/v1/work-items/${ITEM_ID}/sessions`,
    );
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({
      provider: "claude",
      cwd: "C:\\Projects\\repo",
      prompt: taskPrompt,
    });
    expect(
      await screen.findByText(
        /Open request sent\. Send Continue in the desktop app/,
      ),
    ).toBeTruthy();
    await waitFor(() => {
      expect(
        client.getQueryState([getDataContext(), `/work-items/${ITEM_ID}`])
          ?.isInvalidated,
      ).toBe(true);
      expect(
        client.getQueryState([getDataContext(), "/chats?archived=false"])
          ?.isInvalidated,
      ).toBe(true);
    });
  });

  it("keeps start controls disabled while the launch request is pending", async () => {
    let resolveRequest!: (result: Response) => void;
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(
        () => new Promise((resolve) => (resolveRequest = resolve)),
      );
    mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Preparing…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    fireEvent.submit(
      screen.getByRole("button", { name: "Preparing…" }).closest("form")!,
    );
    expect(fetch).toHaveBeenCalledTimes(1);
    resolveRequest(response(launch()));
    expect(
      await screen.findByText(
        /Open request sent\. Send Continue in the desktop app/,
      ),
    ).toBeTruthy();
  });

  it("shows a preparation failure and allows a fresh session", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response(
        launch({
          status: "failed",
          chat: null,
          message: "The provider could not prepare a chat.",
        }),
      ),
    );
    mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    expect(
      await screen.findByText("The provider could not prepare a chat."),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Start another session" }),
    ).toBeTruthy();
  });

  it("can retry opening an already linked chat after handoff failure", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    fetch
      .mockResolvedValueOnce(
        response(
          launch({ status: "failed", message: "The desktop handoff failed." }),
        ),
      )
      .mockResolvedValueOnce(
        response(launch({ status: "ready", message: "Opened." })),
      );
    mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    expect(await screen.findByText("The desktop handoff failed.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Open in app" }));
    expect(
      await screen.findByText(
        /Open request sent\. Send Continue in the desktop app/,
      ),
    ).toBeTruthy();
    expect(fetch.mock.calls[1][0]).toBe(
      `/api/v1/session-launches/${LAUNCH_ID}/open`,
    );
    expect(fetch.mock.calls[1][1]?.method).toBe("POST");
  });

  it("retries linking a persisted provider session without creating another", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    fetch
      .mockResolvedValueOnce(
        response(
          launch({
            status: "failed",
            chat: null,
            sessionId: "provider-session-42",
            message: "Worklog could not link the provider session.",
          }),
        ),
      )
      .mockResolvedValueOnce(
        response(launch({ status: "ready", message: "Linked and opened." })),
      );
    const first = mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    expect(
      await screen.findByText("Worklog could not link the provider session."),
    ).toBeTruthy();
    expect(screen.getByText("provider-session-42")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Retry linking and opening" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Start another session" }),
    ).toBeNull();

    first.unmount();
    mount();
    expect(screen.getByText("provider-session-42")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Retry linking and opening" }),
    ).toBeTruthy();

    await userEvent.click(
      screen.getByRole("button", { name: "Retry linking and opening" }),
    );
    expect(
      await screen.findByText(/Chat prepared and linked\. Open request sent\./),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open in app" })).toBeTruthy();
    expect(fetch.mock.calls[1][0]).toBe(
      `/api/v1/session-launches/${LAUNCH_ID}/open`,
    );
    expect(fetch.mock.calls[1][1]?.method).toBe("POST");
    expect(
      fetch.mock.calls.filter(
        ([url, init]) =>
          String(url) === `/api/v1/work-items/${ITEM_ID}/sessions` &&
          init?.method === "POST",
      ),
    ).toHaveLength(1);
  });

  it("disables starting a session for an archived work item", () => {
    mount({ archived: true });
    expect(
      screen.getByRole("button", { name: "Start session" }),
    ).toBeDisabled();
  });

  it("polls a starting launch until the linked chat is ready", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    let resolveStatus!: (result: Response) => void;
    fetch
      .mockResolvedValueOnce(
        response(launch({ status: "starting", chat: null })),
      )
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveStatus = resolve)),
      );
    mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Preparing a linked chat…")).toBeTruthy();
    resolveStatus(response(launch({ status: "ready" })));
    expect(
      await screen.findByText(
        /Open request sent\. Send Continue in the desktop app/,
      ),
    ).toBeTruthy();
    expect(fetch.mock.calls[1][0]).toBe(
      `/api/v1/session-launches/${LAUNCH_ID}`,
    );
  });

  it("restores a pending launch after remount and keeps a ready chat reopenable", async () => {
    const pendingReads: Array<(result: Response) => void> = [];
    let reads = 0;
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = String(input);
        if (path === `/api/v1/work-items/${ITEM_ID}/sessions`)
          return response(launch({ status: "starting", chat: null }));
        if (path === `/api/v1/session-launches/${LAUNCH_ID}`) {
          reads += 1;
          return new Promise<Response>((resolve) => pendingReads.push(resolve));
        }
        if (path === `/api/v1/session-launches/${LAUNCH_ID}/open`)
          return response(launch({ message: "Open request sent." }));
        throw new Error(`Unexpected request: ${path} ${init?.method ?? "GET"}`);
      });
    const first = mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Preparing a linked chat…")).toBeTruthy();
    const key = contextStorageKey("session-launch", ITEM_ID);
    expect(JSON.parse(sessionStore.read(key).value ?? "null")).toEqual(
      expect.objectContaining({ id: LAUNCH_ID, status: "starting" }),
    );
    expect(
      screen.queryByRole("button", { name: "Start another session" }),
    ).toBeNull();

    first.unmount();
    pendingReads[0](response(launch({ status: "starting", chat: null })));
    const second = mount();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(fetch.mock.calls[2][0]).toBe(
      `/api/v1/session-launches/${LAUNCH_ID}`,
    );
    expect(reads).toBe(2);
    expect(screen.queryByRole("button", { name: "Start session" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Start another session" }),
    ).toBeNull();

    pendingReads[1](response(launch()));
    expect(
      await screen.findByText(/Chat prepared and linked\. Open request sent\./),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open in app" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Start another session" }),
    ).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Open in app" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
    expect(fetch.mock.calls[3][0]).toBe(
      `/api/v1/session-launches/${LAUNCH_ID}/open`,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Start another session" }),
    );
    expect(sessionStore.read(key).value).toBeNull();
    expect(screen.getByRole("button", { name: "Start" })).toBeTruthy();
    second.unmount();
  });

  it("stops polling on a transient status error and retries the same launch", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    fetch
      .mockResolvedValueOnce(
        response(launch({ status: "starting", chat: null })),
      )
      .mockResolvedValueOnce(
        response(
          {
            error: { message: "The launch status is temporarily unavailable." },
          },
          500,
        ),
      )
      .mockResolvedValueOnce(response(launch()));
    mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    expect(
      await screen.findByText("The launch status is temporarily unavailable."),
    ).toBeTruthy();
    expect(
      screen.queryByText(/launch receipt is no longer available/),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Retry status check" }),
    ).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Retry status check" }),
    );
    expect(
      await screen.findByText(/Chat prepared and linked\. Open request sent\./),
    ).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(fetch.mock.calls[2][0]).toBe(
      `/api/v1/session-launches/${LAUNCH_ID}`,
    );
  });

  it("persists an expired status receipt and requires explicit clearing", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    fetch
      .mockResolvedValueOnce(
        response(launch({ status: "starting", chat: null })),
      )
      .mockResolvedValueOnce(
        response({ error: { message: "The launch receipt expired." } }, 404),
      );
    const first = mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    expect(
      await screen.findByText(/launch receipt is no longer available/),
    ).toBeTruthy();
    expect(screen.getByText(/Check Codex history first/)).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: "Clear expired launch to prepare another",
      }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Retry status check" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Start another session" }),
    ).toBeNull();

    const receiptKey = contextStorageKey("session-launch", ITEM_ID);
    const expiredKey = contextStorageKey("session-launch-expired", ITEM_ID);
    expect(JSON.parse(sessionStore.read(receiptKey).value ?? "null")).toEqual(
      expect.objectContaining({ id: LAUNCH_ID, status: "starting" }),
    );
    expect(sessionStore.read(expiredKey).value).toBe("true");
    await new Promise((resolve) => setTimeout(resolve, 1100));
    expect(fetch).toHaveBeenCalledTimes(2);

    first.unmount();
    mount();
    expect(
      await screen.findByText(/launch receipt is no longer available/),
    ).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(2);
    await userEvent.click(
      screen.getByRole("button", {
        name: "Clear expired launch to prepare another",
      }),
    );
    expect(sessionStore.read(receiptKey).value).toBeNull();
    expect(sessionStore.read(expiredKey).value).toBeNull();
    expect(screen.getByRole("button", { name: "Start" })).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("keeps a tracked chat visible when opening an expired receipt returns 404", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    fetch
      .mockResolvedValueOnce(response(launch()))
      .mockResolvedValueOnce(
        response({ error: { message: "The launch receipt expired." } }, 404),
      );
    const first = mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Working folder" }),
      "C:\\Projects\\repo",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(
      await screen.findByText(/Chat prepared and linked\. Open request sent\./),
    ).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Open in app" }));

    expect(
      await screen.findByText(/launch receipt is no longer available/),
    ).toBeTruthy();
    expect(screen.getByText("Release checks")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Open in app" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Start another session" }),
    ).toBeNull();
    expect(
      sessionStore.read(contextStorageKey("session-launch-expired", ITEM_ID))
        .value,
    ).toBe("true");
    expect(
      JSON.parse(
        sessionStore.read(contextStorageKey("session-launch", ITEM_ID)).value ??
          "null",
      ).chat.id,
    ).toBe(CHAT.id);
    expect(fetch.mock.calls[1][0]).toBe(
      `/api/v1/session-launches/${LAUNCH_ID}/open`,
    );
    expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(
      false,
    );

    first.unmount();
    mount();
    expect(
      await screen.findByText(/launch receipt is no longer available/),
    ).toBeTruthy();
    expect(screen.getByText("Release checks")).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("shows linked repository slot availability, queues full repos, and cancels queued dispatches", async () => {
    const REPO_ID = "repo-aa-jobs";
    const DISPATCH_ID = "4444444444444444444444444444444444444444444444444444444444444444";
    const writes: { path: string; method: string; body?: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (method !== "GET") {
        writes.push({
          path,
          method,
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
      }
      if (path === `/api/v1/repositories/${REPO_ID}/slots`) {
        return response({
          repositoryId: REPO_ID,
          repositoryName: "asset-allocation-jobs",
          policy: {
            repositoryId: REPO_ID,
            maxActiveLeases: 2,
            defaultTtlSeconds: 1800,
            baseCheckoutPath: "C:\\Projects\\asset-allocation-jobs",
            slotRootPath: "",
            defaultBranch: "main",
            baseReadonly: true,
            autoRescueDirty: true,
            revision: 1,
            updatedAt: "2026-11-24T00:00:00Z",
            configured: true,
          },
          activeCount: 2,
          queuedCount: 0,
          dirtyCount: 0,
          availableSlots: 0,
          slots: [],
          activeLeases: [],
          queuedLeases: [],
          recentLeases: [],
        });
      }
      if (path === `/api/v1/work-items/${ITEM_ID}/sessions`) {
        return response(
          launch({
            id: DISPATCH_ID,
            dispatchId: DISPATCH_ID,
            status: "queued",
            chat: null,
            repositoryId: REPO_ID,
            repositoryName: "asset-allocation-jobs",
            queuePosition: 1,
            activeHolders: [
              {
                leaseId: "lease-1",
                agentId: "a1",
                holderLabel: "Codex A",
                provider: "codex",
                slotIndex: 1,
                branch: "codex/job-1",
                worktreePath: "C:\\Projects\\.worklog-slots\\aa-jobs\\slot-1",
                mode: "shared",
                state: "active",
                expiresAt: "2026-11-24T01:00:00Z",
              },
            ],
            message: "Queued at #1.",
          }),
        );
      }
      if (path === `/api/v1/session-launches/${DISPATCH_ID}`) {
        return response(
          launch({
            id: DISPATCH_ID,
            dispatchId: DISPATCH_ID,
            status: "queued",
            chat: null,
            repositoryId: REPO_ID,
            repositoryName: "asset-allocation-jobs",
            queuePosition: 1,
            message: "Queued at #1.",
          }),
        );
      }
      if (path === `/api/v1/dispatch-queue/${DISPATCH_ID}/cancel`) {
        return response({
          id: DISPATCH_ID,
          status: "cancelled",
        });
      }
      return response({});
    });

    mount({ repositoryIds: [REPO_ID] });
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    expect(
      await screen.findByText(/2\/2 slots full — will queue at #1/),
    ).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Working folder" })).toBeNull();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Branch" }),
      "codex/batch-retry",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    expect(
      await screen.findByText(/Queued for/i),
    ).toBeTruthy();
    expect(writes[0]).toEqual({
      path: `/api/v1/work-items/${ITEM_ID}/sessions`,
      method: "POST",
      body: {
        provider: "codex",
        repositoryId: REPO_ID,
        branch: "codex/batch-retry",
        prompt: "Release checks\n\nConfirm the gates.",
      },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Cancel queued dispatch" }),
    );
    await waitFor(() =>
      expect(
        writes.some(
          (w) => w.path === `/api/v1/dispatch-queue/${DISPATCH_ID}/cancel`,
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("Queued dispatch cancelled.")).toBeTruthy();
  });

  it("renders a copyable Antigravity slot brief when status is ready_manual", async () => {
    const REPO_ID = "repo-ag";
    const DISPATCH_ID = "5555555555555555555555555555555555555555555555555555555555555555";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === `/api/v1/repositories/${REPO_ID}/slots`) {
        return response({
          repositoryId: REPO_ID,
          repositoryName: "ag-repo",
          policy: {
            repositoryId: REPO_ID,
            maxActiveLeases: 2,
            defaultTtlSeconds: 1800,
            baseCheckoutPath: "C:\\Projects\\ag-repo",
            slotRootPath: "",
            defaultBranch: "main",
            baseReadonly: false,
            autoRescueDirty: true,
            revision: 1,
            updatedAt: "2026-11-24T00:00:00Z",
            configured: true,
          },
          activeCount: 0,
          queuedCount: 0,
          dirtyCount: 0,
          availableSlots: 2,
          slots: [],
          activeLeases: [],
          queuedLeases: [],
          recentLeases: [],
        });
      }
      return response(
        launch({
          id: DISPATCH_ID,
          dispatchId: DISPATCH_ID,
          provider: "antigravity",
          status: "ready_manual",
          chat: null,
          slotIndex: 1,
          branch: "ag/slot-ui",
          worktreePath: "C:\\Projects\\.worklog-slots\\ag-repo\\slot-1",
          copyablePrompt: "Worklog Checkout Slot Assignment:\n- Slot: slot-1",
          message: "Slot reserved for Antigravity.",
        }),
      );
    });

    mount({ repositoryIds: [REPO_ID] });
    await userEvent.click(
      screen.getByRole("button", { name: "Start session" }),
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Provider" }),
      "antigravity",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start" }));

    const brief = await screen.findByRole("textbox", {
      name: "Antigravity slot brief",
    });
    expect(brief).toHaveValue(
      "Worklog Checkout Slot Assignment:\n- Slot: slot-1",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Copy slot brief" }),
    );
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        "Worklog Checkout Slot Assignment:\n- Slot: slot-1",
      ),
    );
  });
});
