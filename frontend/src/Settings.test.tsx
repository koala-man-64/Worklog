import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { AVATAR_COLORS } from "./Avatar";
import { Settings } from "./Settings";
import type { AvatarCatalog, Settings as SettingsData } from "./apiTypes";

const settings: SettingsData = {
  timezone: "America/Chicago",
  today: "2026-11-01",
  version: "test",
  requireProposalApproval: true,
  agentRepositoryRegistration: true,
  storage: {
    databaseBytes: 0,
    backupBytes: 0,
    backupCount: 0,
    backupKinds: {},
    diskFreeBytes: 0,
    pruning: { keep: 10, confirmed: false, candidates: [], candidateBytes: 0 },
    warning: null,
  },
  backups: [],
  credentials: [
    {
      id: "credential-old",
      label: "Agent Alpha",
      agentId: "profile-alpha",
      createdAt: "2026-10-01T10:00:00Z",
      revoked: false,
      registeredBy: null,
      chatId: null,
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

it("rotates a credential while retaining its agent identity", async () => {
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
    screen.getByText(/Rotated Agent Alpha; its agent identity remains/),
  ).toHaveTextContent("identity remains Agent Alpha profile");
  await waitFor(() =>
    expect(calls).toContainEqual({
      path: "/credentials/credential-old/rotate",
      method: "POST",
      body: {},
    }),
  );
  expect(screen.getByText("Identity profile-alpha")).toBeInTheDocument();
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

it("leaves the avatar to the server until the person picks one, even when the suggestion was taken", async () => {
  const posted: unknown[] = [];
  // The suggestion was drawn when the form loaded; another agent has taken it since.
  const stale: AvatarCatalog = {
    ...catalog,
    items: [
      ...catalog.items.filter((entry) => entry.name !== "amber-dots-ears"),
      { name: "amber-dots-ears", available: false },
    ],
  };
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
          ? stale
          : settings;
    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  const user = userEvent.setup();
  render(
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
  expect(picker).toHaveTextContent(/Suggested/);
  expect(screen.queryByText(/already shows this avatar/)).toBeNull();
  await user.type(
    screen.getByRole("textbox", { name: "Credential label" }),
    "Builder",
  );
  const create = screen.getByRole("button", { name: "Create credential" });
  expect(create).toBeEnabled();
  await user.click(create);
  await waitFor(() => expect(posted).toEqual([{ label: "Builder" }]));
});

it("lists what pruning would remove, prunes once confirmed and keeps what the person marks", async () => {
  const backup = (name: string, prunable: boolean) => ({
    name,
    size: 1000,
    createdAt: "2026-11-01T10:00:00Z",
    kind: "manual",
    kept: false,
    prunable,
  });
  const old = "worklog-manual-20261001T000000000000Z-00000001.sqlite3";
  const recent = "worklog-manual-20261101T000000000000Z-00000002.sqlite3";
  const current: SettingsData = {
    ...settings,
    backups: [backup(recent, false), backup(old, true)],
    storage: {
      ...settings.storage,
      backupBytes: 2000,
      backupCount: 2,
      backupKinds: { manual: { count: 2, bytes: 2000 } },
      diskFreeBytes: 5_000_000,
      pruning: {
        keep: 10,
        confirmed: false,
        candidates: [old],
        candidateBytes: 1000,
      },
    },
  };
  const posted: { path: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = new URL(String(input), "http://worklog.test").pathname.replace(
      "/api/v1",
      "",
    );
    if (init?.method === "POST")
      posted.push({ path, body: JSON.parse(String(init.body)) });
    const result =
      path === "/backups/pruning"
        ? { removed: [old] }
        : path.endsWith("/keep")
          ? { name: recent, kept: true }
          : path === "/discussions/avatars"
            ? catalog
            : current;
    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  const user = userEvent.setup();
  render(
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
  expect(
    await screen.findByText(/would remove 1 older ones/),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Backups by kind")).toHaveTextContent(
    "2 manual",
  );
  expect(screen.getByText("Would be removed")).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: `Keep ${recent}` }));
  await user.click(
    screen.getByRole("button", { name: "Prune old manual backups" }),
  );
  await waitFor(() =>
    expect(posted).toEqual([
      { path: `/backups/${recent}/keep`, body: { kept: true } },
      { path: "/backups/pruning", body: {} },
    ]),
  );
  expect(
    await screen.findByText("Removed 1 old manual backups."),
  ).toBeInTheDocument();
});

it("turns proposal approval off and back on", async () => {
  let current = settings;
  const patches: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = new URL(String(input), "http://worklog.test").pathname;
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      patches.push(body);
      current = { ...current, ...body };
    }
    return new Response(
      JSON.stringify(path.endsWith("/discussions/avatars") ? catalog : current),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  });
  const user = userEvent.setup();
  render(
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
  const approval = await screen.findByRole("checkbox", {
    name: "Require my approval for agent proposals",
  });
  expect(approval).toBeChecked();
  expect(screen.getByText(/waits in Review until you approve/)).toBeVisible();

  await user.click(approval);
  await waitFor(() => expect(approval).not.toBeChecked());
  expect(patches).toEqual([{ requireProposalApproval: false }]);
  expect(
    screen.getByText(/applied as soon as its agent submits it/),
  ).toBeVisible();

  await waitFor(() => expect(approval).toBeEnabled());
  await user.click(approval);
  await waitFor(() => expect(approval).toBeChecked());
  expect(patches).toEqual([
    { requireProposalApproval: false },
    { requireProposalApproval: true },
  ]);
});

it("turns agent repository registration off", async () => {
  let current = settings;
  const patches: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = new URL(String(input), "http://worklog.test").pathname;
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      patches.push(body);
      current = { ...current, ...body };
    }
    return new Response(
      JSON.stringify(path.endsWith("/discussions/avatars") ? catalog : current),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  });
  const user = userEvent.setup();
  render(
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
  const registration = await screen.findByRole("checkbox", {
    name: "Agents may register repositories",
  });
  expect(registration).toBeChecked();
  expect(screen.getByText(/shows it as unreviewed/)).toBeVisible();
  await user.click(registration);
  await waitFor(() => expect(registration).not.toBeChecked());
  expect(patches).toEqual([{ agentRepositoryRegistration: false }]);
  expect(
    screen.getByText(/proposes new ones for you to approve/),
  ).toBeVisible();
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
        chatId: "7d3c1f2a-5b6e-4f10-9a8b-1c2d3e4f5a6b",
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
    "Registered by Agent Alpha profile for chat 7d3c1f2a-5b6e-4f10-9a8b-1c2d3e4f5a6b",
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
