import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Settings } from "./Settings";
import type { Settings as SettingsData } from "./types";

const settings: SettingsData = {
  timezone: "America/Chicago",
  today: "2026-11-01",
  version: "test",
  storage: { databaseBytes: 0, backupBytes: 0, warning: null },
  backups: [],
  credentials: [
    {
      id: "credential-old",
      label: "Agent Alpha",
      agentId: "profile-alpha",
      createdAt: "2026-10-01T10:00:00Z",
      revoked: false,
    },
  ],
  agents: [{ id: "profile-alpha", label: "Agent Alpha profile" }],
};

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  setDataContext("settings-test");
});

it("rotates a credential while retaining its stable discussion profile", async () => {
  const calls: { path: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = new URL(String(input), "http://worklog.test").pathname.replace(
      "/api/v1",
      "",
    );
    const method = init?.method ?? "GET";
    const body =
      typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ path, method, body });
    const result =
      method === "POST"
        ? {
            id: "credential-new",
            agentId: "profile-alpha",
            label: "Agent Alpha",
            token: "one-time-secret",
          }
        : settings;
    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>,
  );

  await user.click(await screen.findByRole("button", { name: "Rotate token" }));
  expect(await screen.findByText("one-time-secret")).toBeInTheDocument();
  expect(
    screen.getByText(/Rotated Agent Alpha; its discussion identity remains/),
  ).toHaveTextContent("identity remains Agent Alpha profile");
  await waitFor(() =>
    expect(calls).toContainEqual({
      path: "/credentials/credential-old/rotate",
      method: "POST",
      body: {},
    }),
  );
  expect(screen.getByText("Profile profile-alpha")).toBeInTheDocument();
});
