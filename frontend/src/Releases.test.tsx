import { beforeEach, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setDataContext } from "./api";
import { Releases, ReleasesSection, releaseCounts } from "./Releases";
import type { ReleaseCheck, ReleaseDetail, ReleaseRow } from "./releaseTypes";

const ID = "11111111-1111-4111-8111-111111111111";
const ITEM = "22222222-2222-4222-8222-222222222222";
const ROADMAP = "33333333-3333-4333-8333-333333333333";
const STEP = "44444444-4444-4444-8444-444444444444";
const PR = "55555555-5555-4555-8555-555555555555";
const CHAT = "66666666-6666-4666-8666-666666666666";
const CHECK = "77777777-7777-4777-8777-777777777777";
const ANA = "88888888-8888-4888-8888-888888888888";
const counts = {
  items: 0,
  itemsDone: 0,
  notify: 0,
  test: 0,
  prepare: 0,
  pastDue: 0,
};
function row(fields: Partial<ReleaseRow> = {}): ReleaseRow {
  return {
    id: ID,
    title: "November release",
    description: "",
    releaseDate: "2026-11-20",
    status: "planned",
    archived: false,
    revision: 3,
    createdAt: "2026-11-01T08:00:00Z",
    updatedAt: "2026-11-01T08:00:00Z",
    pastDue: false,
    counts,
    ...fields,
  };
}
function check(fields: Partial<ReleaseCheck> = {}): ReleaseCheck {
  return {
    id: CHECK,
    releaseId: ID,
    kind: "test",
    text: "Export a 10,000-row CSV",
    details: "",
    personId: null,
    personName: null,
    personArchived: false,
    dueDate: null,
    status: "open",
    archived: false,
    revision: 1,
    createdAt: "2026-11-02T08:00:00Z",
    updatedAt: "2026-11-02T08:00:00Z",
    pastDue: false,
    ...fields,
  };
}
function detail(fields: Partial<ReleaseDetail> = {}): ReleaseDetail {
  const { counts: _counts, ...release } = row();
  void _counts;
  return {
    ...release,
    items: [
      {
        id: ITEM,
        title: "Export button",
        status: "done",
        archived: false,
        addedAt: "2026-11-02T08:00:00Z",
      },
    ],
    roadmaps: [
      {
        id: ROADMAP,
        title: "Exports",
        archived: false,
        addedAt: "2026-11-02T08:00:00Z",
        steps: 4,
        stepsDone: 1,
      },
    ],
    steps: [
      {
        id: STEP,
        title: "CSV writer",
        status: "doing",
        retired: false,
        roadmapId: ROADMAP,
        roadmapTitle: "Exports",
        roadmapArchived: false,
        addedAt: "2026-11-02T08:00:00Z",
      },
    ],
    changes: [
      {
        id: PR,
        kind: "pr",
        url: "https://github.com/acme/app/pull/7",
        reference: "acme/app#7",
        title: "Add the export button",
        ownerType: "item",
        ownerId: ITEM,
        ownerTitle: "Export button",
        chatId: CHAT,
        chatTitle: "Export button (Claude)",
        provider: "claude",
        model: "opus",
        direct: false,
        archived: false,
        createdAt: "2026-11-02T08:00:00Z",
      },
    ],
    checks: [check()],
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
  write: (w: Write) => Response = () => json(row()),
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
  location.hash = "";
  setDataContext("release-test-" + crypto.randomUUID());
});

it("sums up a release's work and what is still open", () => {
  expect(releaseCounts(counts)).toBe("No work items");
  expect(
    releaseCounts({
      items: 3,
      itemsDone: 1,
      notify: 2,
      test: 1,
      prepare: 0,
      pastDue: 1,
    }),
  ).toBe("1 of 3 work items done · 2 to tell · 1 to test · 1 past due");
  expect(releaseCounts({ ...counts, items: 1, itemsDone: 1, prepare: 4 })).toBe(
    "1 of 1 work item done · 4 to prepare",
  );
});

it("lists upcoming releases soonest first, then the ones not dated, and narrows the list", async () => {
  const { paths } = serve({
    "/releases": () => ({
      items: [
        row({
          pastDue: true,
          releaseDate: "2026-10-30",
          title: "Late release",
          counts: { ...counts, items: 2, itemsDone: 1, notify: 2, pastDue: 1 },
        }),
        row({
          id: "undated",
          title: "Next platform release",
          releaseDate: null,
        }),
      ],
      total: 2,
    }),
  });
  show(<Releases />);
  const late = await screen.findByRole("link", { name: "Late release" });
  expect(late).toHaveAttribute("href", "#releases/" + ID);
  const lateRow = late.closest("article") as HTMLElement;
  expect(within(lateRow).getByText("Past due")).toBeInTheDocument();
  expect(
    within(lateRow).getByText(
      "1 of 2 work items done · 2 to tell · 1 past due",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Not dated" }),
  ).toBeInTheDocument();
  const undated = screen.getByRole("link", { name: "Next platform release" });
  expect(
    within(undated.closest("article") as HTMLElement).getAllByText("Not dated"),
  ).toHaveLength(1);
  expect(paths).toContain(
    "/releases?status=planned&archived=false&limit=50&offset=0",
  );
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Release status" }),
    "all",
  );
  await userEvent.type(
    screen.getByRole("searchbox", { name: "Find releases" }),
    "vault",
  );
  await waitFor(() =>
    expect(paths.at(-1)).toBe(
      "/releases?status=all&archived=false&limit=50&offset=0&q=vault",
    ),
  );
  // Outside the upcoming view, the list keeps one order and no heading.
  expect(screen.queryByRole("heading", { name: "Not dated" })).toBeNull();
  // The archive holds releases of every status, so it opens on all of them.
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Release status" }),
    "planned",
  );
  await userEvent.click(screen.getByRole("checkbox", { name: "Show archive" }));
  expect(screen.getByRole("combobox", { name: "Release status" })).toHaveValue(
    "all",
  );
  await waitFor(() =>
    expect(paths.at(-1)).toBe(
      "/releases?status=all&archived=true&limit=50&offset=0&q=vault",
    ),
  );
});

it("adds a release with its date and opens it", async () => {
  const { writes } = serve({ "/releases": () => ({ items: [], total: 0 }) });
  show(<Releases />);
  await userEvent.type(
    await screen.findByRole("textbox", { name: "New release title" }),
    "  December release ",
  );
  fireEvent.change(screen.getByLabelText("Release date"), {
    target: { value: "2026-12-11" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Add release" }));
  await waitFor(() =>
    expect(writes).toEqual([
      {
        method: "POST",
        path: "/releases",
        body: { title: "December release", releaseDate: "2026-12-11" },
      },
    ]),
  );
  await waitFor(() => expect(location.hash).toBe("#releases/" + ID));
});

it("shows what a release holds and the changes agents made, and removes a record", async () => {
  const { writes } = serve({
    "/releases/": () => detail(),
    "/people": () => ({ items: [], total: 0 }),
  });
  show(<Releases selected={ID} />);
  expect(
    await screen.findByRole("heading", { name: "November release" }),
  ).toBeInTheDocument();
  const work = screen
    .getByRole("heading", { name: /^Work items/ })
    .closest("section") as HTMLElement;
  expect(
    within(work).getByRole("link", { name: "Export button" }),
  ).toHaveAttribute("href", "#work/" + ITEM);
  expect(within(work).getByText("Done")).toBeInTheDocument();
  expect(screen.getByText("1 of 4 steps done")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Exports / CSV writer" }),
  ).toHaveAttribute("href", `#roadmaps/${ROADMAP}/${STEP}`);
  const pr = screen.getByRole("link", {
    name: "acme/app#7 Add the export button ↗",
  });
  expect(pr).toHaveAttribute("href", "https://github.com/acme/app/pull/7");
  const change = pr.closest(".line") as HTMLElement;
  expect(
    within(change).getByRole("link", { name: "Export button (Claude)" }),
  ).toHaveAttribute("href", "#chats/" + CHAT);
  // A change on a work item the release holds goes with the work item, not on its own.
  expect(within(change).queryByRole("button", { name: /Remove/ })).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Remove CSV writer from the release" }),
  );
  await waitFor(() =>
    expect(writes).toEqual([
      {
        method: "POST",
        path: "/release-contents/remove",
        body: { releaseId: ID, targetType: "step", targetId: STEP },
      },
    ]),
  );
});

it("adds a check to a list and changes its person, due date and status", async () => {
  const { writes } = serve(
    {
      "/releases/": () => detail(),
      "/people": () => ({
        items: [{ id: ANA, name: "Ana", revision: 1, archived: false }],
        total: 1,
      }),
    },
    () => json(check()),
  );
  show(<Releases selected={ID} />);
  const notify = (
    await screen.findByRole("heading", { name: /Who needs to know/ })
  ).closest("section") as HTMLElement;
  expect(within(notify).getByText("No one to tell yet.")).toBeInTheDocument();
  await userEvent.type(
    within(notify).getByRole("textbox", {
      name: "New person or group to tell",
    }),
    "Support: the export button moves",
  );
  await userEvent.click(
    within(notify).getByRole("button", { name: "Add who to tell" }),
  );
  await waitFor(() => expect(writes).toHaveLength(1));
  const test = screen.getByRole("article", { name: "Export a 10,000-row CSV" });
  // A row holds its controls while one of its saves is on its way, so each waits for the last.
  const status = within(test).getByRole("combobox", { name: "Status" });
  await userEvent.selectOptions(status, "waiting");
  await waitFor(() => expect(writes).toHaveLength(2));
  const person = within(test).getByRole("combobox", { name: "Person" });
  await waitFor(() => expect(person).toBeEnabled());
  await userEvent.selectOptions(person, ANA);
  await waitFor(() => expect(writes).toHaveLength(3));
  const due = within(test).getByLabelText("Due");
  await waitFor(() => expect(due).toBeEnabled());
  fireEvent.change(due, { target: { value: "2026-11-13" } });
  fireEvent.blur(due);
  await waitFor(() => expect(writes).toHaveLength(4));
  expect(writes).toEqual([
    {
      method: "POST",
      path: "/release-checks",
      body: {
        releaseId: ID,
        kind: "notify",
        text: "Support: the export button moves",
      },
    },
    {
      method: "PATCH",
      path: "/release-checks/" + CHECK,
      body: { revision: 1, status: "waiting" },
    },
    {
      method: "PATCH",
      path: "/release-checks/" + CHECK,
      body: { revision: 1, personId: ANA },
    },
    {
      method: "PATCH",
      path: "/release-checks/" + CHECK,
      body: { revision: 1, dueDate: "2026-11-13" },
    },
  ]);
});

it("names an archived person a check already names, and marks a past-due check", async () => {
  serve({
    "/releases/": () =>
      detail({
        checks: [
          check({
            kind: "prepare",
            text: "Request the firewall rule",
            personId: ANA,
            personName: "Ana",
            personArchived: true,
            dueDate: "2026-11-01",
            pastDue: true,
            details: "Ticket **NET-12**",
          }),
        ],
      }),
    "/people": () => ({ items: [], total: 0 }),
  });
  show(<Releases selected={ID} />);
  const prep = await screen.findByRole("article", {
    name: "Request the firewall rule",
  });
  expect(within(prep).getByRole("combobox", { name: "Person" })).toHaveValue(
    ANA,
  );
  expect(
    within(prep).getByRole("option", { name: "Ana (archived)" }),
  ).toBeInTheDocument();
  expect(within(prep).getByText("Past due")).toBeInTheDocument();
  expect(within(prep).getByText("NET-12").tagName).toBe("STRONG");
});

it("sets, clears and moves the release date, and changes the release's status", async () => {
  const { writes } = serve({
    "/releases/": () => detail(),
    "/people": () => ({ items: [], total: 0 }),
  });
  show(<Releases selected={ID} />);
  const date = await screen.findByLabelText("Release date");
  expect(date).toHaveValue("2026-11-20");
  // Typing part of a date saves nothing; leaving the field saves it.
  fireEvent.change(date, { target: { value: "2026-11-27" } });
  expect(writes).toEqual([]);
  fireEvent.blur(date);
  await waitFor(() => expect(writes).toHaveLength(1));
  // The page holds its controls while a save is on its way, so each waits for the last.
  const clear = screen.getByRole("button", { name: "Clear date" });
  await waitFor(() => expect(clear).toBeEnabled());
  await userEvent.click(clear);
  await waitFor(() => expect(writes).toHaveLength(2));
  const status = screen.getByRole("combobox", { name: "Release status" });
  await waitFor(() => expect(status).toBeEnabled());
  await userEvent.selectOptions(status, "released");
  await waitFor(() =>
    expect(writes).toEqual([
      {
        method: "PATCH",
        path: "/releases/" + ID,
        body: { revision: 3, releaseDate: "2026-11-27" },
      },
      {
        method: "PATCH",
        path: "/releases/" + ID,
        body: { revision: 3, releaseDate: null },
      },
      {
        method: "PATCH",
        path: "/releases/" + ID,
        body: { revision: 3, status: "released" },
      },
    ]),
  );
});

it("never saves a date typed part way", async () => {
  const { writes } = serve({
    "/releases/": () => detail(),
    "/people": () => ({ items: [], total: 0 }),
  });
  show(<Releases selected={ID} />);
  const date = await screen.findByLabelText("Release date");
  // A cleared month leaves the browser's value empty, with the field marked bad input.
  Object.defineProperty(date, "validity", {
    configurable: true,
    value: { badInput: true },
  });
  fireEvent.change(date, { target: { value: "" } });
  fireEvent.blur(date);
  expect(date).toHaveValue("2026-11-20");
  // A year typed part way reads as a year before 1000.
  Object.defineProperty(date, "validity", {
    configurable: true,
    value: { badInput: false },
  });
  fireEvent.change(date, { target: { value: "0020-11-20" } });
  fireEvent.blur(date);
  expect(date).toHaveValue("2026-11-20");
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(writes).toEqual([]);
});

it("keeps an archived release read-only until it is recovered", async () => {
  const { writes } = serve({
    "/releases/": () => detail({ archived: true }),
    "/people": () => ({ items: [], total: 0 }),
  });
  show(<Releases selected={ID} />);
  expect(await screen.findByText(/Archived: this release/)).toBeInTheDocument();
  for (const name of [
    "Add work item",
    "Add roadmap or step",
    "Add PR or commit",
    "Add test",
  ])
    expect(screen.queryByRole("button", { name })).toBeNull();
  expect(
    screen.getByRole("combobox", { name: "Release status" }),
  ).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Recover" }));
  await waitFor(() =>
    expect(writes).toEqual([
      {
        method: "POST",
        path: `/releases/${ID}/recover`,
        body: { revision: 3 },
      },
    ]),
  );
});

it("lists the releases that hold a work item and adds it to an upcoming one", async () => {
  const other = "99999999-9999-4999-8999-999999999999";
  const { writes, paths } = serve({
    "/releases?status=all": () => ({
      items: [row({ status: "released", releaseDate: "2026-10-01" })],
      total: 1,
    }),
    "/releases?status=planned": () => ({
      items: [
        row(),
        row({ id: other, title: "Next platform release", releaseDate: null }),
      ],
      total: 2,
    }),
  });
  show(<ReleasesSection targetType="item" targetId={ITEM} />);
  const held = await screen.findByRole("link", { name: "November release" });
  expect(held).toHaveAttribute("href", "#releases/" + ID);
  expect(paths).toContain(
    `/releases?status=all&targetType=item&targetId=${ITEM}&limit=200`,
  );
  // A release that already holds the item isn't offered again.
  const choose = screen.getByRole("combobox", { name: "Release to add to" });
  expect(
    within(choose)
      .getAllByRole("option")
      .map((o) => o.textContent),
  ).toEqual([
    "Choose an upcoming release…",
    "Next platform release · Not dated",
  ]);
  await userEvent.selectOptions(choose, other);
  await userEvent.click(screen.getByRole("button", { name: "Add to release" }));
  await waitFor(() =>
    expect(writes).toEqual([
      {
        method: "POST",
        path: "/release-contents",
        body: { releaseId: other, targetType: "item", targetId: ITEM },
      },
    ]),
  );
});
