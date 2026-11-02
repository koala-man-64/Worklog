import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { AVATAR_COLORS } from "./Avatar";
import { Settings } from "./Settings";
import type { AvatarCatalog } from "./discussionTypes";
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
      registeredBy: null,
      registeredActive: 0,
    },
  ],
  agents: [
    {
      id: "profile-alpha",
      label: "Agent Alpha profile",
      avatar: "teal-visor-antenna",
    },
  ],
};
const catalog: AvatarCatalog = {
  items: [
    { name: "teal-visor-antenna", available: false },
    { name: "amber-dots-ears", available: true },
    { name: "pink-dots-ears", available: true },
  ],
  suggested: "amber-dots-ears",
  current: null,
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
        : path === "/discussions/avatars"
          ? catalog
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

it("issues a credential with the suggested avatar or one the person picks", async () => {
  const posted: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = new URL(String(input), "http://worklog.test").pathname.replace(
      "/api/v1",
      "",
    );
    if (init?.method === "POST") posted.push(JSON.parse(String(init.body)));
    const result =
      init?.method === "POST"
        ? { id: "credential-new", agentId: "profile-new", token: "secret" }
        : path === "/discussions/avatars"
          ? catalog
          : settings;
    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  const user = userEvent.setup();
  const { container } = render(
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
      <Settings />
    </QueryClientProvider>,
  );

  const picker = await screen.findByRole("group", { name: "Avatar" });
  const preview = () =>
    picker.querySelector("svg.avatar circle")?.getAttribute("fill");
  expect(preview()).toBe(AVATAR_COLORS.amber);
  expect(
    container.querySelector(".discussion-profile-list svg.avatar circle"),
  ).toHaveAttribute("fill", AVATAR_COLORS.teal);

  await user.selectOptions(
    screen.getByRole("combobox", { name: "Colour" }),
    "teal",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Eyes" }),
    "visor",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Top" }),
    "antenna",
  );
  expect(preview()).toBe(AVATAR_COLORS.teal);
  expect(screen.getByText(/already shows this avatar/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Create credential" }),
  ).toBeDisabled();

  await user.selectOptions(
    screen.getByRole("combobox", { name: "Colour" }),
    "pink",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Eyes" }),
    "dots",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Top" }),
    "ears",
  );
  await user.type(
    screen.getByRole("textbox", { name: "Credential label" }),
    "Reviewer",
  );
  await user.click(screen.getByRole("button", { name: "Create credential" }));
  await waitFor(() =>
    expect(posted).toEqual([{ label: "Reviewer", avatar: "pink-dots-ears" }]),
  );
});

it("names the agent that registered a credential and what revoking takes with it", async () => {
  const registered: SettingsData = {
    ...settings,
    credentials: [
      { ...settings.credentials[0], registeredActive: 1 },
      {
        id: "credential-registered",
        label: "Parser fix",
        agentId: "profile-parser",
        createdAt: "2026-11-01T10:00:00Z",
        revoked: false,
        registeredBy: { id: "profile-alpha", label: "Agent Alpha profile" },
        registeredActive: 0,
      },
    ],
    agents: [
      ...settings.agents,
      { id: "profile-parser", label: "Parser fix", avatar: "amber-dots-ears" },
    ],
  };
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = new URL(String(input), "http://worklog.test").pathname;
    if (init?.method === "POST") calls.push(path);
    return new Response(
      JSON.stringify(
        path.endsWith("/discussions/avatars") ? catalog : registered,
      ),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Settings />
    </QueryClientProvider>,
  );
  const registrar = await screen.findByRole("link", {
    name: "Agent Alpha profile",
  });
  expect(registrar).toHaveAttribute("href", "#agents/profile-alpha");
  expect(registrar.parentElement).toHaveTextContent(
    "Registered by Agent Alpha profile",
  );
  // Only the registered credential names a registering agent.
  expect(screen.getAllByText(/^Registered by/)).toHaveLength(1);
  expect(
    screen.getByText(/revoking this credential revokes them too/),
  ).toHaveTextContent("Registered 1 active agent;");
  expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
  await user.click(
    screen.getByRole("button", { name: "Revoke with 1 registered" }),
  );
  await waitFor(() =>
    expect(calls).toEqual(["/api/v1/credentials/credential-old/revoke"]),
  );
});
