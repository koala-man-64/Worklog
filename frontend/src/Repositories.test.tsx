import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Repositories } from "./Repositories";
import { Coordination } from "./Coordination";
import { RepositoryPicker } from "./RepositoryControls";
import type { AgentRun, Participation, Repository } from "./repositoryTypes";

const repo: Repository = {
  id: "repo-1",
  name: "Service",
  url: "https://github.com/a/service",
  remoteIdentity: "github.com/a/service",
  duplicateOf: null,
  description: "Shared API",
  revision: 1,
  archived: false,
};
const run: AgentRun = {
  id: "run-1",
  agentId: "credential",
  credentialId: "credential",
  clientRunKey: "key",
  displayName: "API reviewer",
  provider: "OpenAI",
  externalChatId: "chat",
  revision: 2,
  endedAt: null,
  endReason: null,
};
const report: Participation = {
  id: "p-1",
  runId: run.id,
  run,
  description: "Reviewing transactions",
  itemId: null,
  contexts: [
    { repositoryId: repo.id, worktreePath: "C:/work/api", branch: "codex/api" },
  ],
  revision: 1,
  lastSeenAt: "2026-11-01T10:00:00Z",
  stale: true,
  releasedAt: null,
  releaseReason: null,
};
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
beforeEach(() => {
  vi.restoreAllMocks();
  setDataContext("repo-test-" + crypto.randomUUID());
});

it("creates a repository by keyboard and archives its acknowledged revision", async () => {
  let items: Repository[] = [];
  const writes: { path: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      writes.push({ path, body });
      if (path.endsWith("/archive")) items = [];
      else items = [repo];
      return json(repo);
    }
    return json({ items, total: items.length });
  });
  show(<Repositories />);
  await userEvent.type(
    screen.getByRole("textbox", { name: "Repository name" }),
    "Service",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Repository remote URL" }),
    repo.url,
  );
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByRole("link", { name: "Service" })).toHaveAttribute(
    "href",
    "#repositories/repo-1",
  );
  await userEvent.click(screen.getByRole("button", { name: "Archive" }));
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      path: "/api/v1/repositories/repo-1/archive",
      body: { revision: 1 },
    }),
  );
  expect(writes[0].body).toEqual({
    name: "Service",
    url: repo.url,
    description: "",
  });
});

it("names the record that already holds a remote instead of adding another", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "POST") return json({ ...repo, created: false });
    if (String(input).endsWith("/repositories/repo-1")) return json(repo);
    return json({
      items: [
        repo,
        {
          ...repo,
          id: "repo-2",
          name: "Service over SSH",
          url: "git@github.com:a/service.git",
          duplicateOf: repo.id,
        },
      ],
      total: 2,
    });
  });
  show(<Repositories />);
  // A record an upgrade kept beside another for the same remote says so.
  const flagged = (await screen.findByText("Service over SSH")).closest(
    "article",
  )!;
  expect(
    await within(flagged).findByRole("link", { name: "Service" }),
  ).toHaveAttribute("href", "#repositories/repo-1");
  expect(flagged).toHaveTextContent("two records for one repository");
  await userEvent.type(
    screen.getByRole("textbox", { name: "Repository name" }),
    "Service again",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Repository remote URL" }),
    "https://github.com/A/Service.git",
  );
  await userEvent.keyboard("{Enter}");
  const notice = await screen.findByRole("status");
  expect(notice).toHaveTextContent(
    "That remote is already registered as Service.",
  );
  expect(within(notice).getByRole("link", { name: "Service" })).toHaveAttribute(
    "href",
    "#repositories/repo-1",
  );
});

it("keeps overlapping reports, marks staleness separately, and ends a run only after confirmation", async () => {
  const writes: { path: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      writes.push({ path, body: JSON.parse(String(init.body)) });
      return json({ ...run, endedAt: "2026-11-01T12:00:00Z" });
    }
    if (path.includes("/participations?"))
      return json({
        items: [
          report,
          {
            ...report,
            id: "p-2",
            stale: false,
            run: { ...run, id: "run-2", displayName: "API builder" },
          },
        ],
        total: 2,
      });
    if (path.includes("/coordination-updates?"))
      return json({
        items: [
          {
            id: "u",
            participationId: "p-1",
            runId: run.id,
            authorLabel: "API reviewer",
            kind: "blocker",
            text: "Awaiting schema review",
            itemId: null,
            contexts: [{ repositoryId: repo.id }],
            createdAt: "2026-11-01T10:00:00Z",
          },
        ],
        total: 1,
      });
    return json({ items: [repo], total: 1 });
  });
  show(<Coordination repositoryId={repo.id} />);
  expect(await screen.findByText("Stale report")).toBeVisible();
  expect(screen.getByRole("heading", { name: "API builder" })).toBeVisible();
  expect(screen.getAllByText("C:/work/api")).toHaveLength(2);
  expect(screen.getByText("Awaiting schema review")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "End run API reviewer" }),
  );
  expect(writes).toHaveLength(0);
  await userEvent.click(
    screen.getByRole("button", { name: "Confirm end run" }),
  );
  await waitFor(() =>
    expect(writes).toEqual([
      { path: "/api/v1/agent-runs/run-1/end", body: { revision: 2 } },
    ]),
  );
});

it("distinguishes same-name repositories and preserves unresolved linked IDs", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    json({
      items: String(input).includes("archived=true")
        ? []
        : [
            repo,
            { ...repo, id: "repo-2", url: "https://github.com/b/service" },
          ],
      total: 2,
    }),
  );
  const change = vi.fn();
  show(<RepositoryPicker value={["missing"]} onChange={change} />);
  const group = screen.getByRole("group", { name: "Linked repositories" });
  await waitFor(() =>
    expect(within(group).getAllByRole("checkbox")).toHaveLength(3),
  );
  await userEvent.click(within(group).getAllByRole("checkbox")[1]);
  expect(change).toHaveBeenCalledWith(["missing", "repo-2"]);
  expect(screen.getByText(/Repository \(missing\)/)).toBeVisible();
});

it("renders released reports and feed failures without pretending they are empty", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.includes("/participations?"))
      return json({
        items: path.includes("includeReleased=true")
          ? [
              {
                ...report,
                releasedAt: "2026-11-01T11:00:00Z",
                releaseReason: "run-ended",
              },
            ]
          : [],
        total: 0,
      });
    if (path.includes("/coordination-updates?"))
      return json({ error: { message: "Feed unavailable" } }, 503);
    return json({ items: [repo], total: 1 });
  });
  show(<Coordination repositoryId={repo.id} />);
  expect(await screen.findByText("Feed unavailable")).toBeVisible();
  expect(
    screen.queryByText("No updates reported yet."),
  ).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("checkbox", { name: "Show released reports" }),
  );
  expect(await screen.findByText("Released", { exact: true })).toBeVisible();
});

it("searches past the first page without dropping an archived selection", async () => {
  const reads: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    reads.push(path);
    if (path.endsWith("/old"))
      return json({ ...repo, id: "old", name: "Retired API", archived: true });
    if (path.includes("q=target") || path.includes("offset=50"))
      return json({
        items: [{ ...repo, id: "later", name: "Target API" }],
        total: 51,
      });
    return json({ items: [repo], total: 51 });
  });
  const change = vi.fn();
  show(<RepositoryPicker value={["old"]} onChange={change} />);
  expect(
    await screen.findByRole("checkbox", { name: /Retired API \(archived\)/ }),
  ).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "Next repository choices" }),
  );
  expect(await screen.findByText("Target API")).toBeVisible();
  await userEvent.type(
    screen.getByRole("textbox", { name: "Linked repositories search" }),
    "target",
  );
  await waitFor(() =>
    expect(reads.some((path) => path.includes("offset=0&q=target"))).toBe(true),
  );
  await userEvent.click(screen.getByRole("checkbox", { name: /Target API/ }));
  expect(change).toHaveBeenCalledWith(["old", "later"]);
  expect(screen.getByRole("checkbox", { name: /Retired API/ })).toBeChecked();
});

it("shows immutable update context names and paths instead of the repository's current name", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input).includes("/coordination-updates?"))
      return json({
        items: [
          {
            id: "old-update",
            runId: run.id,
            participationId: report.id,
            authorLabel: "Reviewer",
            kind: "handoff",
            text: "Original checkout",
            itemId: null,
            contexts: [
              {
                repositoryId: repo.id,
                repositoryName: "Original repository name",
                branch: "old-branch",
                worktreePath: "C:/original",
              },
            ],
            createdAt: "2026-11-01T10:00:00Z",
          },
        ],
        total: 1,
      });
    if (String(input).includes("/repositories/")) return json(repo);
    return json({ items: [], total: 0 });
  });
  show(<Coordination repositoryId={repo.id} />);
  await userEvent.click(
    await screen.findByText("Reported context when posted"),
  );
  expect(
    screen.getByRole("link", { name: "Original repository name" }),
  ).toBeVisible();
  expect(screen.getByText("C:/original")).toBeVisible();
  expect(screen.getByText("old-branch")).toBeVisible();
});
