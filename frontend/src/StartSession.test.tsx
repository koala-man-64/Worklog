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
      screen.getByText(/Codex runs read-only during preparation/),
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

  it("stops polling on a status read error and retries the same launch", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    fetch
      .mockResolvedValueOnce(
        response(launch({ status: "starting", chat: null })),
      )
      .mockResolvedValueOnce(
        response({ error: { message: "The launch receipt expired." } }, 404),
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

    expect(await screen.findByText("The launch receipt expired.")).toBeTruthy();
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
});
