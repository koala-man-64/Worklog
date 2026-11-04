import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { contextStorageKey, setDataContext } from "./api";
import { Markdown } from "./components";
import { Resources } from "./Resources";
import type { Resource, ResourceDetail } from "./resourceTypes";

const ID = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";
function resource(fields: Partial<Resource> = {}): Resource {
  return {
    id: ID,
    title: "Coding standards",
    summary: "Rules for every repository",
    archived: false,
    revision: 3,
    createdAt: "2026-11-02T08:00:00Z",
    updatedAt: "2026-11-03T09:30:00Z",
    ...fields,
  };
}
function detail(fields: Partial<ResourceDetail> = {}): ResourceDetail {
  return {
    ...resource(),
    body: "## Testing\n\n- Run the suite twice\n\nSee [Reviews](#reviews) and [the backlog](#work).\n\n## Reviews\n\nAsk early.",
    history: [
      {
        id: "event-1",
        kind: "resource.create",
        actorId: "local-user",
        actorLabel: "You",
        actorKind: "human",
        createdAt: "2026-11-02T08:00:00Z",
      },
    ],
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
  write: (w: Write) => Response = () => json(detail()),
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
  setDataContext("resource-test-" + crypto.randomUUID());
});
afterEach(() => {
  location.hash = "";
});

it("lists pages by title and opens the first one to read", async () => {
  const { paths } = serve({
    "/resources?": () => ({
      items: [
        resource(),
        resource({ id: OTHER, title: "Runbook", summary: "" }),
      ],
      total: 2,
    }),
    ["/resources/" + ID]: () => detail(),
  });
  show(<Resources />);
  const list = await screen.findByRole("region", { name: "Pages" });
  const first = await within(list).findByRole("link", {
    name: /Coding standards/,
  });
  expect(first).toHaveAttribute("href", "#resources/" + ID);
  expect(first).toHaveAttribute("aria-current", "page");
  expect(first).toHaveTextContent("Rules for every repository");
  expect(within(list).getByRole("link", { name: "Runbook" })).toHaveAttribute(
    "href",
    "#resources/" + OTHER,
  );
  expect(paths).toContain("/resources?archived=false&q=&limit=50&offset=0");
  const pane = screen.getByRole("region", { name: "Page" });
  expect(
    await within(pane).findByRole("heading", {
      level: 2,
      name: "Coding standards",
    }),
  ).toBeInTheDocument();
  expect(within(pane).getByText("Rules for every repository")).toHaveClass(
    "lede",
  );
  expect(within(pane).getByText("Run the suite twice")).toBeInTheDocument();
  expect(within(pane).getByText("Activity history (1)")).toBeInTheDocument();
  // Reading shows the page; nothing is editable until "Edit page".
  expect(within(pane).queryByRole("textbox")).not.toBeInTheDocument();
});

it("finds pages by their words and shows the archive", async () => {
  const { paths } = serve({ "/resources?": () => ({ items: [], total: 0 }) });
  show(<Resources />);
  expect(
    await screen.findByText(/Write down what you and your agents keep/),
  ).toBeInTheDocument();
  await userEvent.type(
    screen.getByRole("searchbox", { name: "Find pages" }),
    "deploy",
  );
  await waitFor(() =>
    expect(paths.at(-1)).toBe(
      "/resources?archived=false&q=deploy&limit=50&offset=0",
    ),
  );
  expect(await screen.findByText("No page matches those words.")).toBeVisible();
  await userEvent.click(screen.getByRole("checkbox", { name: "Show archive" }));
  await waitFor(() =>
    expect(paths.at(-1)).toBe(
      "/resources?archived=true&q=deploy&limit=50&offset=0",
    ),
  );
  // Archived pages are recovered, not added to.
  expect(
    screen.queryByRole("textbox", { name: "New page title" }),
  ).not.toBeInTheDocument();
});

it("adds a page by its title and opens it in its editor", async () => {
  const created = detail({
    id: OTHER,
    title: "Release checks",
    summary: "",
    body: "",
    revision: 1,
  });
  let pages: Resource[] = [];
  const { writes } = serve(
    {
      "/resources?": () => ({ items: pages, total: pages.length }),
      ["/resources/" + OTHER]: () => created,
    },
    () => {
      pages = [created];
      return json(created);
    },
  );
  const { rerender } = show(<Resources />);
  await userEvent.type(
    await screen.findByRole("textbox", { name: "New page title" }),
    "  Release checks ",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add page" }));
  await waitFor(() =>
    expect(writes).toEqual([
      {
        method: "POST",
        path: "/resources",
        body: { title: "Release checks", summary: "", body: "" },
      },
    ]),
  );
  await waitFor(() => expect(location.hash).toBe("#resources/" + OTHER));
  // The app passes the new route down.
  rerender(
    <QueryClientProvider client={new QueryClient()}>
      <Resources selected={OTHER} />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByRole("textbox", { name: "Page (Markdown)" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Summary" })).toHaveAttribute(
    "placeholder",
    "What this page is for, in a sentence",
  );
});

it("edits a page in place, saving each field with the page's revision", async () => {
  let saved = detail();
  const { writes } = serve(
    {
      "/resources?": () => ({ items: [resource()], total: 1 }),
      ["/resources/" + ID]: () => saved,
    },
    (w) => {
      // As the server does: the edit lands and the revision moves on.
      saved = { ...saved, ...(w.body as object), revision: saved.revision + 1 };
      return json(saved);
    },
  );
  show(<Resources selected={ID} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Edit page" }),
  );
  const body = screen.getByRole("textbox", { name: "Page (Markdown)" });
  await userEvent.clear(body);
  await userEvent.type(body, "Short now");
  await userEvent.click(screen.getByRole("button", { name: "Done" }));
  await waitFor(() =>
    expect(writes.at(-1)).toEqual({
      method: "PATCH",
      path: "/resources/" + ID,
      body: { revision: 3, body: "Short now" },
    }),
  );
  // Once saved, Done leaves the editor for the page.
  expect(
    await screen.findByRole("button", { name: "Edit page" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("textbox", { name: "Page (Markdown)" }),
  ).not.toBeInTheDocument();
});

it("opens a page with an unsaved draft in its editor, so the draft is never hidden", async () => {
  serve(
    {
      "/resources?": () => ({ items: [resource()], total: 1 }),
      ["/resources/" + ID]: () => detail(),
    },
    () => json({ error: { code: "conflict", message: "Changed" } }, 409),
  );
  localStorage.setItem(
    contextStorageKey("draft", "resource-summary-" + ID),
    "Rules for most repositories",
  );
  localStorage.setItem(
    contextStorageKey("draft-base", "resource-summary-" + ID),
    "2",
  );
  show(<Resources selected={ID} />);
  expect(await screen.findByRole("textbox", { name: "Summary" })).toHaveValue(
    "Rules for most repositories",
  );
  // A draft the server refused keeps the editor open, Done or not.
  expect(await screen.findByText(/Conflict: draft kept/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(screen.getByRole("textbox", { name: "Summary" })).toBeInTheDocument();
});

it("archives a page, which then offers only recovery", async () => {
  let archived = false;
  const { writes } = serve(
    {
      "/resources?": () => ({ items: [], total: 0 }),
      ["/resources/" + ID]: () =>
        detail({ archived, revision: archived ? 4 : 3 }),
    },
    () => {
      archived = true;
      return json(detail({ archived: true, revision: 4 }));
    },
  );
  show(<Resources selected={ID} />);
  await userEvent.click(await screen.findByRole("button", { name: "Archive" }));
  await waitFor(() =>
    expect(writes).toEqual([
      {
        method: "POST",
        path: `/resources/${ID}/archive`,
        body: { revision: 3 },
      },
    ]),
  );
  expect(
    await screen.findByRole("button", { name: "Recover" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Edit page" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/out of your agents’ context/)).toBeInTheDocument();
});

it("routes a page's own heading links to its sections and scrolls to the one in the address", async () => {
  const scroll = vi.fn();
  const original = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = function (this: Element) {
    scroll(this.id);
  };
  try {
    serve({
      "/resources?": () => ({ items: [resource()], total: 1 }),
      ["/resources/" + ID]: () => detail(),
    });
    show(<Resources selected={ID} section="reviews" />);
    const pane = screen.getByRole("region", { name: "Page" });
    const link = await within(pane).findByRole("link", { name: "Reviews" });
    expect(link).toHaveAttribute("href", `#resources/${ID}/reviews`);
    // A link to another page of Worklog stays the route it names.
    expect(
      within(pane).getByRole("link", { name: "the backlog" }),
    ).toHaveAttribute("href", "#work");
    expect(
      within(pane).getByRole("heading", { name: "Reviews" }),
    ).toHaveAttribute("id", `resources/${ID}-reviews`);
    await waitFor(() =>
      expect(scroll).toHaveBeenCalledWith(`resources/${ID}-reviews`),
    );
  } finally {
    Element.prototype.scrollIntoView = original;
  }
});

it("rewrites only links to the text's own headings, however they are encoded", () => {
  render(
    <Markdown
      anchors="guide"
      text={
        "## Café\n\n[Here](#café) [Encoded](#caf%C3%A9) [Elsewhere](#roadmaps/1) [Gone](#missing)"
      }
    />,
  );
  expect(screen.getByRole("link", { name: "Here" })).toHaveAttribute(
    "href",
    expect.stringMatching(/^#guide\/caf/),
  );
  expect(screen.getByRole("link", { name: "Encoded" })).toHaveAttribute(
    "href",
    "#guide/caf%C3%A9",
  );
  expect(screen.getByRole("link", { name: "Elsewhere" })).toHaveAttribute(
    "href",
    "#roadmaps/1",
  );
  expect(screen.getByRole("link", { name: "Gone" })).toHaveAttribute(
    "href",
    "#missing",
  );
});

it("takes headings from the parsed text: underlined ones count and code blocks never do", () => {
  const { container } = render(
    <Markdown
      anchors="resources/page"
      text={[
        "Setup",
        "=====",
        "",
        "~~~",
        "# Notes",
        "~~~",
        "",
        "# Notes",
        "",
        "# 🎉",
        "",
        "[Set up](#setup) [Read notes](#notes) [By reference][notes] [Empty](#)",
        "",
        "[notes]: #notes",
      ].join("\n")}
    />,
  );
  expect(
    screen.getByRole("heading", { level: 1, name: "Setup" }),
  ).toHaveAttribute("id", "resources/page-setup");
  // The fenced line is code, so the real heading keeps the plain slug.
  expect(
    screen.getByRole("heading", { level: 1, name: "Notes" }),
  ).toHaveAttribute("id", "resources/page-notes");
  expect(screen.getByRole("heading", { name: "🎉" })).not.toHaveAttribute("id");
  expect(screen.getByRole("link", { name: "Set up" })).toHaveAttribute(
    "href",
    "#resources/page/setup",
  );
  expect(screen.getByRole("link", { name: "Read notes" })).toHaveAttribute(
    "href",
    "#resources/page/notes",
  );
  expect(screen.getByRole("link", { name: "By reference" })).toHaveAttribute(
    "href",
    "#resources/page/notes",
  );
  // A heading with no letters has no slug, so an empty target stays as written.
  expect(screen.getByRole("link", { name: "Empty" })).toHaveAttribute(
    "href",
    "#",
  );
  expect(container.querySelectorAll("[id]")).toHaveLength(2);
});

it("keeps the editor open when typing resumes after Done", async () => {
  let saved = detail();
  const bodies: unknown[] = [];
  let open = () => {};
  const held = new Promise<void>((resolve) => (open = resolve));
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input).replace("/api/v1", "");
    if ((init?.method ?? "GET") === "GET")
      return json(
        path.startsWith("/resources?")
          ? { items: [resource()], total: 1 }
          : saved,
      );
    const body = JSON.parse(String(init?.body));
    bodies.push(body);
    // The first save waits until the person has typed again.
    if (bodies.length === 1) await held;
    saved = { ...saved, ...body, revision: saved.revision + 1 };
    return json(saved);
  });
  show(<Resources selected={ID} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Edit page" }),
  );
  await userEvent.type(screen.getByRole("textbox", { name: "Summary" }), ".");
  await userEvent.click(screen.getByRole("button", { name: "Done" }));
  await waitFor(() => expect(bodies).toHaveLength(1));
  await userEvent.type(screen.getByRole("textbox", { name: "Summary" }), "!");
  open();
  await waitFor(() =>
    expect(saved.summary).toBe("Rules for every repository.!"),
  );
  const field = screen
    .getByRole("textbox", { name: "Summary" })
    .closest("section")!;
  await waitFor(() =>
    expect(within(field).getByRole("status")).toHaveTextContent("Saved"),
  );
  // Every draft is saved, yet the editor stays, because typing resumed after Done.
  expect(screen.getByRole("textbox", { name: "Summary" })).toHaveValue(
    "Rules for every repository.!",
  );
  await userEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(
    await screen.findByRole("button", { name: "Edit page" }),
  ).toBeInTheDocument();
});
