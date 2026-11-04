import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Repositories } from "./Repositories";
import { Coordination } from "./Coordination";
import { RepositoryPicker } from "./RepositoryControls";
import type { AgentRun, Participation, Repository } from "./repositoryTypes";
import type { Attachment } from "./types";

const repo: Repository = {
  id: "repo-1",
  name: "Service",
  url: "https://github.com/a/service",
  remoteIdentity: "github.com/a/service",
  duplicateOf: null,
  mergedInto: null,
  description: "Shared API",
  registeredBy: null,
  reviewed: true,
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

it("merges a flagged duplicate into the record that holds its remote only after confirmation", async () => {
  const duplicate = {
    ...repo,
    id: "repo-2",
    name: "Service over SSH",
    url: "git@github.com:a/service.git",
    duplicateOf: repo.id,
    revision: 4,
  };
  const writes: { path: string; body: unknown }[] = [];
  const merged = { ...duplicate, archived: true, mergedInto: repo.id };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      writes.push({ path, body: JSON.parse(String(init.body)) });
      return json(merged);
    }
    // Once merged, the refetched record replaces the merge controls.
    if (path.endsWith("/repositories/repo-2"))
      return json(writes.length ? merged : duplicate);
    if (path.endsWith("/repositories/repo-1")) return json(repo);
    if (path.includes("/repositories?"))
      return json({ items: [repo, duplicate], total: 2 });
    return json({ items: [], total: 0 });
  });
  show(<Repositories selected="repo-2" />);
  // A flagged duplicate opens the merge with the record that holds its remote chosen.
  expect(
    await screen.findByRole("combobox", { name: "Merge into" }),
  ).toHaveValue("repo-1");
  await userEvent.click(
    await screen.findByRole("button", { name: "Merge into Service…" }),
  );
  expect(writes).toHaveLength(0);
  expect(
    screen.getByText("Merge Service over SSH into Service?"),
  ).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Confirm merge" }));
  await waitFor(() =>
    expect(writes).toEqual([
      {
        path: "/api/v1/repositories/repo-2/merge",
        body: { revision: 4, intoId: "repo-1", intoRevision: 1 },
      },
    ]),
  );
  await waitFor(() => expect(location.hash).toBe("#repositories/repo-1"));
});

it("shows a merged record read-only, naming the record that answers for it", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    String(input).endsWith("/repositories/repo-2")
      ? json({
          ...repo,
          id: "repo-2",
          name: "Old",
          archived: true,
          mergedInto: repo.id,
        })
      : String(input).endsWith("/repositories/repo-1")
        ? json(repo)
        : json({ items: [], total: 0 }),
  );
  show(<Repositories selected="repo-2" />);
  expect(await screen.findByRole("heading", { name: "Old" })).toBeVisible();
  expect(await screen.findByRole("link", { name: "Service" })).toHaveAttribute(
    "href",
    "#repositories/repo-1",
  );
  expect(screen.getByText(/now answers for its remote/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /repository$/ })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Repository name" })).toBeNull();
  expect(screen.queryByText("Merge into another repository")).toBeNull();
  // Its files went to the record that answers for it.
  expect(screen.queryByRole("heading", { name: "Files" })).toBeNull();
});

const runbook: Attachment = {
  id: "file-1",
  name: "runbook.md",
  mime: "text/markdown",
  size: 2048,
  sha256: "",
  revision: 1,
  archived: false,
  createdAt: "2026-11-04T10:00:00+00:00",
};
const repoFiles = "/api/v1/attachments?ownerType=repository&ownerId=repo-1";
async function filesSection() {
  return (
    await screen.findByRole("heading", { name: "Files", level: 2 })
  ).closest("section")!;
}

it("attaches a file to a repository, offers it for download and recovers an archived one", async () => {
  const design = {
    ...runbook,
    id: "file-2",
    name: "design.pdf",
    revision: 2,
    archived: true,
  };
  let active: Attachment[] = [];
  const writes: { path: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      writes.push({ path, body: init.body });
      if (path.startsWith(repoFiles)) active = [runbook];
      return json(path.startsWith(repoFiles) ? runbook : design);
    }
    if (path.endsWith("/repositories/repo-1")) return json(repo);
    if (path === repoFiles)
      return json({ items: active, total: active.length });
    if (path === repoFiles + "&archived=true")
      return json({ items: [design], total: 1 });
    return json({ items: [], total: 0 });
  });
  show(<Repositories selected={repo.id} />);
  const section = await filesSection();
  expect(
    await within(section).findByText("No files attached to this repository."),
  ).toBeVisible();
  await userEvent.upload(
    within(section).getByLabelText("Attach a file"),
    new File(["# Runbook"], "runbook.md", { type: "text/markdown" }),
  );
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].path).toBe(repoFiles + "&name=runbook.md");
  expect(writes[0].body).toBeInstanceOf(File);
  expect(
    await within(section).findByRole("link", { name: "runbook.md" }),
  ).toHaveAttribute("href", "/api/v1/attachments/file-1?download=1");
  expect(
    within(section).queryByText("No files attached to this repository."),
  ).toBeNull();
  // Archived files wait in the section's own recovery list.
  await userEvent.click(
    within(section).getByText("Archived supporting records (1)"),
  );
  await userEvent.click(
    within(section).getByRole("button", { name: "Recover" }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      path: "/api/v1/attachments/file-2/recover",
      body: JSON.stringify({ revision: 2 }),
    }),
  );
});

it("keeps an archived repository's files and their archive, but takes no new ones", async () => {
  const writes: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      writes.push(path);
      return json(runbook);
    }
    if (path.endsWith("/repositories/repo-1"))
      return json({ ...repo, archived: true });
    if (path === repoFiles) return json({ items: [runbook], total: 1 });
    // Archived before the repository was, it stays where search sends you.
    if (path === repoFiles + "&archived=true")
      return json({
        items: [{ ...runbook, id: "file-2", name: "old.md", archived: true }],
        total: 1,
      });
    return json({ items: [], total: 0 });
  });
  show(<Repositories selected={repo.id} />);
  const section = await filesSection();
  expect(
    await within(section).findByRole("link", { name: "runbook.md" }),
  ).toHaveAttribute("href", "/api/v1/attachments/file-1?download=1");
  expect(within(section).queryByLabelText("Attach a file")).toBeNull();
  expect(
    screen.getByText(/Existing links, files and reports remain visible/),
  ).toBeVisible();
  expect(
    await within(section).findByText("Archived supporting records (1)"),
  ).toBeVisible();
  await userEvent.click(
    within(section).getByRole("button", { name: "Archive" }),
  );
  await waitFor(() =>
    expect(writes).toEqual(["/api/v1/attachments/file-1/archive"]),
  );
});

it("shows who registered a repository and lets the person confirm an agent's registration", async () => {
  const registered = {
    ...repo,
    id: "repo-3",
    name: "Parser",
    registeredBy: {
      kind: "agent" as const,
      label: "Parser agent",
      agentId: "agent-7",
    },
    reviewed: false,
    revision: 2,
  };
  const writes: { path: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "POST") {
      writes.push({ path: String(input), body: JSON.parse(String(init.body)) });
      return json({ ...registered, reviewed: true, revision: 3 });
    }
    return json({ items: [repo, registered], total: 2 });
  });
  show(<Repositories />);
  const row = (await screen.findByText("Parser")).closest("article")!;
  expect(row).toHaveTextContent("Registered by Parser agent · unreviewed");
  expect(
    within(row).getByRole("link", { name: "Parser agent" }),
  ).toHaveAttribute("href", "#agents/agent-7");
  expect(within(row).getByRole("link", { name: "Edit" })).toHaveAttribute(
    "href",
    "#repositories/repo-3",
  );
  // The person's own registrations carry no review prompt.
  const own = screen.getByText("Service").closest("article")!;
  expect(within(own).queryByRole("button", { name: "Confirm" })).toBeNull();
  await userEvent.click(within(row).getByRole("button", { name: "Confirm" }));
  await waitFor(() =>
    expect(writes).toEqual([
      { path: "/api/v1/repositories/repo-3/confirm", body: { revision: 2 } },
    ]),
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

it("shows linked repositories as chips that unlink, and reads no list until asked", async () => {
  const reads: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    reads.push(path);
    if (path.endsWith("/repositories/old"))
      return json({ ...repo, id: "old", name: "Retired API", archived: true });
    return json({ error: { message: "Not found" } }, 404);
  });
  const change = vi.fn();
  show(<RepositoryPicker value={["missing", "old"]} onChange={change} />);
  const group = screen.getByRole("group", { name: "Repositories" });
  expect(
    await within(group).findByRole("link", { name: "Retired API (archived)" }),
  ).toHaveAttribute("href", "#repositories/old");
  // A link whose record can't be read still shows, and can still be removed.
  expect(
    within(group).getByRole("link", { name: "Repository (missing)" }),
  ).toBeVisible();
  await userEvent.click(
    within(group).getByRole("button", { name: "Unlink Retired API" }),
  );
  expect(change).toHaveBeenCalledWith(["missing"]);
  expect(reads.some((path) => path.includes("/repositories?"))).toBe(false);
});

it("links a repository from a search, telling same-name repositories apart by remote", async () => {
  const reads: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    reads.push(path);
    if (path.endsWith("/repositories/repo-1")) return json(repo);
    return json({
      items: [
        repo,
        { ...repo, id: "repo-2", url: "https://github.com/b/service" },
      ],
      total: 2,
    });
  });
  const change = vi.fn();
  show(<RepositoryPicker value={["repo-1"]} onChange={change} />);
  const group = screen.getByRole("group", { name: "Repositories" });
  const open = within(group).getByRole("button", { name: "Link repository" });
  await userEvent.click(open);
  expect(
    within(group).getByRole("textbox", { name: "Repositories search" }),
  ).toHaveFocus();
  const matches = await within(group).findByRole("list", {
    name: "Repositories matches",
  });
  // The linked record isn't offered again; its namesake shows its remote.
  expect(within(matches).getAllByRole("button")).toHaveLength(1);
  await userEvent.click(
    within(matches).getByRole("button", { name: /github\.com\/b\/service/ }),
  );
  expect(change).toHaveBeenCalledWith(["repo-1", "repo-2"]);
  expect(within(group).queryByRole("textbox")).toBeNull();
  expect(open).toHaveFocus();
  expect(reads).toContain("/api/v1/repositories?limit=20&q=");
});

it("says when every match is already linked", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    String(input).endsWith("/repositories/repo-1")
      ? json(repo)
      : json({ items: [repo], total: 1 }),
  );
  show(<RepositoryPicker value={["repo-1"]} onChange={vi.fn()} />);
  await userEvent.click(
    screen.getByRole("button", { name: "Link repository" }),
  );
  expect(
    await screen.findByText("Every match is already linked."),
  ).toBeVisible();
});

it("closes the search on Escape, and a floating one on a click elsewhere", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    json({ items: [], total: 0 }),
  );
  show(
    <>
      <RepositoryPicker
        label="New work repositories"
        folded
        value={[]}
        onChange={vi.fn()}
      />
      <p>Elsewhere</p>
    </>,
  );
  const open = screen.getByRole("button", { name: "Link repository" });
  await userEvent.click(open);
  expect(open).toHaveAttribute("aria-expanded", "true");
  // With nothing registered, it says where to register one.
  expect(
    await screen.findByRole("link", { name: "Add a repository" }),
  ).toHaveAttribute("href", "#repositories");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(open).toHaveFocus();
  await userEvent.click(open);
  expect(screen.getByRole("textbox")).toBeVisible();
  await userEvent.click(screen.getByText("Elsewhere"));
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(open).toHaveAttribute("aria-expanded", "false");
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

it("narrows a long list by text and picks the first match with Enter, without submitting the form it sits in", async () => {
  const reads: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    reads.push(path);
    if (path.endsWith("/repositories/old"))
      return json({ ...repo, id: "old", name: "Retired API", archived: true });
    if (path.includes("q=target"))
      return json({
        items: [{ ...repo, id: "later", name: "Target API" }],
        total: 1,
      });
    return json({ items: [repo], total: 51 });
  });
  const change = vi.fn();
  const submit = vi.fn((e: React.FormEvent) => e.preventDefault());
  show(
    <form onSubmit={submit}>
      <RepositoryPicker
        label="New work repositories"
        folded
        value={["old"]}
        onChange={change}
      />
    </form>,
  );
  const group = screen.getByRole("group", { name: "New work repositories" });
  // Folded into a one-line form, the label names the picker without showing.
  expect(within(group).getByText("New work repositories")).toHaveClass(
    "visually-hidden",
  );
  await userEvent.click(
    within(group).getByRole("button", { name: "Link repository" }),
  );
  expect(
    await screen.findByText("Showing 1 of 51. Keep typing to narrow."),
  ).toBeVisible();
  await userEvent.type(
    screen.getByRole("textbox", { name: "New work repositories search" }),
    "target",
  );
  expect(
    await screen.findByRole("button", { name: /Target API/ }),
  ).toBeVisible();
  expect(reads).toContain("/api/v1/repositories?limit=20&q=target");
  await userEvent.keyboard("{Enter}");
  expect(change).toHaveBeenCalledWith(["old", "later"]);
  expect(submit).not.toHaveBeenCalled();
  // The archived link it already had stays.
  expect(
    within(group).getByRole("link", { name: "Retired API (archived)" }),
  ).toBeVisible();
});

it("closes a search that something else disabled, and leaves it closed", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    json({ items: [repo], total: 1 }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const picker = (disabled: boolean) => (
    <QueryClientProvider client={client}>
      <RepositoryPicker value={[]} onChange={vi.fn()} disabled={disabled} />
    </QueryClientProvider>
  );
  const { rerender } = render(picker(false));
  await userEvent.click(
    screen.getByRole("button", { name: "Link repository" }),
  );
  expect(screen.getByRole("textbox")).toHaveFocus();
  rerender(picker(true));
  expect(screen.queryByRole("textbox")).toBeNull();
  // Enabled again, it doesn't reopen and take focus back.
  rerender(picker(false));
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(
    screen.getByRole("button", { name: "Link repository" }),
  ).toHaveAttribute("aria-expanded", "false");
});

const shipApi = {
  id: "w1",
  title: "Ship API",
  description: "",
  status: "doing" as const,
  adoUrl: null,
  stepId: null,
  archived: false,
  revision: 5,
  repositoryIds: [repo.id, "repo-9"],
};

it("links work to a repository from its page and unlinks it, each on the item's own revision", async () => {
  const writes: { path: string; body: unknown }[] = [];
  const parser = {
    ...shipApi,
    id: "w2",
    title: "Fix parser",
    status: "open" as const,
    revision: 2,
    repositoryIds: [],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "PATCH") {
      writes.push({ path, body: JSON.parse(String(init.body)) });
      return json({});
    }
    if (path.endsWith("/repositories/repo-1")) return json(repo);
    if (path.includes("/work-items?repositoryId="))
      return json({ items: [shipApi], total: 1 });
    if (path.includes("/work-items?view=all"))
      return json({ items: [shipApi, parser], total: 2 });
    return json({ items: [], total: 0 });
  });
  show(<Repositories selected={repo.id} />);
  const section = (
    await screen.findByRole("heading", { name: "Linked work" })
  ).closest("section")!;
  await userEvent.click(
    await within(section).findByRole("button", { name: "Unlink Ship API" }),
  );
  await waitFor(() =>
    expect(writes).toEqual([
      {
        path: "/api/v1/work-items/w1",
        body: { revision: 5, repositoryIds: ["repo-9"] },
      },
    ]),
  );
  const link = within(section).getByRole("button", { name: "Link work item" });
  await waitFor(() => expect(link).toBeEnabled());
  await userEvent.click(link);
  const matches = await within(section).findByRole("list", {
    name: "Work to link matches",
  });
  // Work already linked here isn't offered again.
  expect(within(matches).getAllByRole("button")).toHaveLength(1);
  await userEvent.click(
    within(matches).getByRole("button", { name: /Fix parser/ }),
  );
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      path: "/api/v1/work-items/w2",
      body: { revision: 2, repositoryIds: ["repo-1"] },
    }),
  );
});

it("keeps an archived repository's work links removable but takes no new ones", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.endsWith("/repositories/repo-1"))
      return json({ ...repo, archived: true });
    if (path.includes("/work-items?repositoryId="))
      return json({ items: [shipApi], total: 1 });
    return json({ items: [], total: 0 });
  });
  show(<Repositories selected={repo.id} />);
  expect(
    await screen.findByRole("button", { name: "Unlink Ship API" }),
  ).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Link work item" })).toBeNull();
});

it("shows the people tied to a repository, ties another, and keeps an archived one's ties without new ones", async () => {
  const maintainer = {
    id: "tie-1",
    personId: "person-1",
    name: "Dana Kim",
    personTitle: "Site Reliability Engineer",
    personTeam: "Platform",
    relationship: "Maintainer",
    archived: false,
    createdAt: "2026-11-01T10:00:00Z",
  };
  let archived = false;
  const writes: { path: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if ((init?.method ?? "GET") !== "GET") {
      writes.push({ path, body: JSON.parse(String(init?.body)) });
      return json({ ...maintainer, id: "tie-2" });
    }
    if (path.endsWith("/repositories/repo-1"))
      return json({ ...repo, archived, people: [maintainer] });
    if (path.includes("/people?"))
      return json({
        items: [
          {
            id: "person-1",
            name: "Dana Kim",
            title: "",
            team: "",
            revision: 1,
          },
          {
            id: "person-2",
            name: "Eli Park",
            title: "Engineer",
            team: "",
            revision: 1,
          },
        ],
        total: 2,
      });
    return json({ items: [], total: 0 });
  });
  const { unmount } = show(<Repositories selected={repo.id} />);
  expect(
    await screen.findByRole("heading", { name: /People/ }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Dana Kim" })).toHaveAttribute(
    "href",
    "#people/person-1",
  );
  expect(
    screen.getByText("Site Reliability Engineer · Platform"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("textbox", { name: "Role for Dana Kim" }),
  ).toHaveValue("Maintainer");
  await userEvent.selectOptions(
    await screen.findByRole("combobox", { name: "Choose person" }),
    "person-2",
  );
  await userEvent.click(screen.getByRole("button", { name: "Tie person" }));
  await waitFor(() =>
    expect(writes).toEqual([
      {
        path: "/api/v1/person-links",
        body: {
          personId: "person-2",
          targetType: "repository",
          targetId: repo.id,
          relationship: "",
        },
      },
    ]),
  );
  unmount();

  archived = true;
  show(<Repositories selected={repo.id} />);
  expect(
    await screen.findByRole("button", { name: "Unlink Dana Kim" }),
  ).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Tie person" })).toBeNull();
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
