import { afterEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DaySummary, summaryMarkdown } from "./DaySummary";
import type { Day, Entry, ItemStatus } from "./types";

const entry = (
  title: string,
  status: ItemStatus,
  fields: Partial<Entry> = {},
): Entry =>
  ({
    id: "entry-" + title,
    itemId: "item-" + title,
    date: "2026-11-05",
    planned: true,
    worked: true,
    outcome: "",
    position: 0,
    revision: 2,
    statusOnDay: status,
    item: {
      id: "item-" + title,
      title,
      description: "",
      status,
      adoUrl: null,
      stepId: null,
      archived: false,
      revision: 4,
    },
    ...fields,
  }) as Entry;
const day = (entries: Entry[]): Day => ({
  date: "2026-11-05",
  revision: 7,
  reflection: "",
  entries,
  summary: { worked: [], completed: [], blocked: [] },
  nextItemId: null,
});
const summary = () => screen.getByRole("region", { name: "The day’s summary" });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("groups the work done that day by the status each item ended it with, blockers last", () => {
  expect(
    summaryMarkdown(
      day([
        entry("Vendor sync", "blocked", {
          outcome: "Waiting on credentials from Sam.",
        }),
        entry("Fix login redirect", "done", {
          outcome: "Merged and deployed.",
        }),
        entry("Today summary", "doing", {
          outcome: "Plan approved.\nNext: the fold.",
        }),
        entry("Planned, not reached", "done", {
          worked: false,
          outcome: "Left out",
        }),
        entry("Update the README", "open"),
        entry("Second fix", "done", { outcome: " \n\t" }),
        entry("Security review", "waiting", {
          outcome: "With the security team.",
        }),
      ]),
    ),
  ).toBe(
    [
      "**Thursday, November 5**",
      "",
      "### Done",
      "",
      "- **Fix login redirect**",
      "",
      "  Merged and deployed.",
      "",
      "- **Second fix**",
      "",
      "### Doing",
      "",
      "- **Today summary**",
      "",
      "  Plan approved.",
      "  Next: the fold.",
      "",
      "### Open",
      "",
      "- **Update the README**",
      "",
      "### Waiting",
      "",
      "- **Security review**",
      "",
      "  With the security team.",
      "",
      "### Blocked",
      "",
      "- **Vendor sync**",
      "",
      "  Waiting on credentials from Sam.",
      "",
    ].join("\n"),
  );
});

it("groups by the status the day recorded, not the item's status now", () => {
  const markdown = summaryMarkdown(
    day([
      entry("Finished later", "done", { statusOnDay: "doing" }),
      // Work logged after its day has no recorded status; it shows its status now.
      entry("Logged afterwards", "blocked", { statusOnDay: undefined }),
    ]),
  );
  expect(markdown).toMatch(/### Doing\n\n- \*\*Finished later\*\*/);
  expect(markdown).toMatch(/### Blocked\n\n- \*\*Logged afterwards\*\*\n$/);
  expect(markdown).not.toContain("### Done");
});

it("never drops work whose status this page doesn't know; it comes last", () => {
  expect(
    summaryMarkdown(
      day([
        entry("Odd one", "open", { statusOnDay: "paused" as ItemStatus }),
        entry("Usual one", "blocked"),
      ]),
    ),
  ).toMatch(
    /### Blocked\n\n- \*\*Usual one\*\*\n\n### Paused\n\n- \*\*Odd one\*\*\n$/,
  );
});

it("keeps titles as typed and an outcome's own lists inside its item", () => {
  const typed = "Fix *nix  [paths]\n<now> & `then`_";
  const done = day([
    entry(typed, "done", {
      outcome: "\r\n\nDid one thing\r\n- step a\n- step b\n\n  \n",
    }),
  ]);
  expect(summaryMarkdown(done)).toContain(
    "- **Fix \\*nix \\[paths\\] \\<now\\> \\& \\`then\\`\\_**\n\n  Did one thing\n  - step a\n  - step b\n",
  );
  render(<DaySummary day={done} />);
  const title = within(summary()).getByText("Fix *nix [paths] <now> & `then`_");
  expect(title.tagName).toBe("STRONG");
  const item = title.closest("li")!;
  expect(item).toHaveTextContent("Did one thing");
  expect(
    within(item)
      .getAllByRole("listitem")
      .map((step) => step.textContent),
  ).toEqual(["step a", "step b"]);
});

it("waits for work ticked Worked on, and is left out of a day with no entries", () => {
  const { container, rerender } = render(
    <DaySummary
      day={day([
        entry("Planned only", "open", { worked: false, outcome: "Not yet" }),
      ])}
    />,
  );
  expect(summary()).toHaveTextContent("Nothing marked “Worked on” yet.");
  expect(summary()).not.toHaveTextContent("Not yet");
  expect(
    within(summary()).getByRole("button", { name: "Copy summary" }),
  ).toBeDisabled();
  rerender(<DaySummary day={day([])} />);
  expect(container).toBeEmptyDOMElement();
});

it("copies the summary's Markdown", async () => {
  const user = userEvent.setup();
  render(
    <DaySummary
      day={day([entry("Fix login redirect", "done", { outcome: "Merged." })])}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Copy summary" }));
  expect(await navigator.clipboard.readText()).toBe(
    "**Thursday, November 5**\n\n### Done\n\n- **Fix login redirect**\n\n  Merged.\n",
  );
  expect(within(summary()).getByRole("status")).toHaveTextContent("Copied");
});

it("copies formatted text beside the Markdown where the browser takes both", async () => {
  const user = userEvent.setup();
  const items: Record<string, Blob>[] = [];
  vi.stubGlobal(
    "ClipboardItem",
    class {
      constructor(data: Record<string, Blob>) {
        items.push(data);
      }
    },
  );
  const write = vi.spyOn(navigator.clipboard, "write").mockResolvedValue();
  const done = day([
    entry("Fix login redirect", "done", { outcome: "Merged **#106**." }),
  ]);
  render(<DaySummary day={done} />);
  await user.click(screen.getByRole("button", { name: "Copy summary" }));
  expect(write).toHaveBeenCalledTimes(1);
  expect(items).toHaveLength(1);
  expect(await items[0]["text/plain"].text()).toBe(summaryMarkdown(done));
  const html = await items[0]["text/html"].text();
  expect(html).toContain("<h3>Done</h3>");
  expect(html).toContain("<strong>Fix login redirect</strong>");
  expect(html).toContain("Merged <strong>#106</strong>.");
  expect(html).not.toContain("Copy summary");
});

it("says so when the summary could not be copied", async () => {
  const user = userEvent.setup();
  vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
    new Error("Clipboard blocked"),
  );
  render(<DaySummary day={day([entry("Fix login redirect", "done")])} />);
  await user.click(screen.getByRole("button", { name: "Copy summary" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The summary was not copied: Clipboard blocked",
  );
  expect(within(summary()).getByRole("status")).toBeEmptyDOMElement();
});
