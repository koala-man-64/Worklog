import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import guide from "../../docs/GUIDE.md?raw";
import agentGuide from "../../docs/AGENT-GUIDE.md?raw";
import { Guide } from "./Guide";
import { headings, slug, slugger } from "./guideAnchors";
import { destinations } from "./navigation";

const all = headings(guide);
// Each view of the Guide page, with one of its sections.
const views = [
  {
    route: "guide",
    label: "For you",
    name: "Worklog guide",
    markdown: guide,
    section: { slug: "back-up-and-export", text: "Back up and export" },
  },
  {
    route: "agent-guide",
    label: "For agents",
    name: "Agent guide",
    markdown: agentGuide,
    section: { slug: "8-errors", text: "8. Errors" },
  },
];

afterEach(() => {
  vi.restoreAllMocks();
});

/** jsdom lays nothing out, so it has no scrollIntoView to spy on; this records the ids scrolled to. */
function recordScrolls(each: () => void) {
  const scroll = vi.fn();
  const original = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = function (this: Element) {
    scroll(this.id);
  };
  try {
    each();
  } finally {
    Element.prototype.scrollIntoView = original;
  }
  return scroll;
}

describe.each(views)("$name in Markdown", ({ markdown }) => {
  const found = headings(markdown);

  it("links only to its own headings, so every link works on GitHub and in Worklog", () => {
    const slugs = new Set(found.map((heading) => heading.slug));
    const links = [...markdown.matchAll(/\]\(([^)\s]+)\)/g)].map(
      (match) => match[1],
    );
    for (const link of links) {
      expect(link, "a file link would not open in Worklog").toMatch(
        /^#|^https:/,
      );
      if (link.startsWith("#")) expect(slugs, link).toContain(link.slice(1));
    }
  });

  it("gives every heading its own anchor", () => {
    const slugs = found.map((heading) => heading.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
  });
});

describe("the person's guide in Markdown", () => {
  it("has links, so the check that they stay inside it reads some", () => {
    expect([...guide.matchAll(/\]\(([^)\s]+)\)/g)].length).toBeGreaterThan(0);
  });

  it("describes every page in the sidebar under Pages", () => {
    const pages = all.findIndex((heading) => heading.text === "Pages");
    const end = all.findIndex((h, index) => index > pages && h.level <= 2);
    const described = all.slice(pages + 1, end).map((heading) => heading.text);
    for (const { label } of destinations) expect(described).toContain(label);
  });
});

describe.each(views)(
  "Guide page, $label",
  ({ route, name, markdown, section }) => {
    it("lists the guide's sections and gives each heading the anchor its entry opens", () => {
      render(<Guide route={route} />);
      const contents = screen.getByRole("navigation", { name: "Contents" });
      const entries = within(contents).getAllByRole("link");
      const sections = headings(markdown).filter(
        (h) => h.level === 2 || h.level === 3,
      );
      expect(entries.map((entry) => entry.textContent)).toEqual(
        sections.map((h) => h.text),
      );
      const text = screen.getByRole("article", { name });
      for (const heading of sections) {
        const entry = within(contents).getByRole("link", {
          name: heading.text,
        });
        expect(entry).toHaveAttribute("href", `#${route}/${heading.slug}`);
        const target = document.getElementById(`${route}-${heading.slug}`);
        expect(target, heading.text).not.toBeNull();
        expect(text).toContainElement(target);
        expect(target).toHaveTextContent(heading.text);
      }
    });

    it("leaves the guide's title to the page heading", () => {
      render(<Guide route={route} />);
      expect(
        screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent),
      ).toEqual(["Guide"]);
    });

    it("marks its own view and links to both", () => {
      render(<Guide route={route} />);
      const switcher = screen.getByRole("group", { name: "Show the guide" });
      const links = within(switcher).getAllByRole("link");
      expect(
        links.map((link) => [
          link.textContent,
          link.getAttribute("href"),
          link.getAttribute("aria-current"),
        ]),
      ).toEqual(
        views.map((view) => [
          view.label,
          "#" + view.route,
          view.route === route ? "page" : null,
        ]),
      );
    });

    it("scrolls to the section in the address and marks it in the contents", () => {
      const scroll = recordScrolls(() => {
        render(<Guide route={route} selected={section.slug} />);
      });
      expect(scroll).toHaveBeenCalledWith(`${route}-${section.slug}`);
      expect(
        within(screen.getByRole("navigation", { name: "Contents" })).getByRole(
          "link",
          { name: section.text },
        ),
      ).toHaveAttribute("aria-current", "location");
    });
  },
);

describe("Guide page", () => {
  it("routes the person's guide's own links to its sections", () => {
    render(<Guide />);
    const text = screen.getByRole("article", { name: "Worklog guide" });
    expect(
      within(text).getAllByRole("link", { name: "Conflicts" })[0],
    ).toHaveAttribute("href", "#guide/conflicts");
  });

  it("opens the other guide afresh at its top, while its view links stay put", () => {
    const { rerender } = render(<Guide route="guide" />);
    const before = screen.getByRole("article", { name: "Worklog guide" });
    const contentsBefore = screen.getByRole("navigation", { name: "Contents" });
    const forAgents = screen.getByRole("link", { name: "For agents" });
    rerender(<Guide route="agent-guide" />);
    // A new pane, so neither keeps the scroll the person's guide had.
    expect(before.isConnected).toBe(false);
    expect(contentsBefore.isConnected).toBe(false);
    expect(screen.getByRole("article", { name: "Agent guide" })).toBeVisible();
    expect(screen.queryByRole("article", { name: "Worklog guide" })).toBeNull();
    // The link just chosen is the same element, so it keeps focus.
    expect(forAgents.isConnected).toBe(true);
    expect(forAgents).toHaveAttribute("aria-current", "page");
  });

  it("ignores a section the address names wrongly, even one no selector could hold", () => {
    const scroll = recordScrolls(() => {
      render(<Guide route="agent-guide" selected={'x"] \\'} />);
    });
    expect(scroll).not.toHaveBeenCalled();
    expect(screen.getByRole("article", { name: "Agent guide" })).toBeVisible();
  });
});

describe("heading anchors", () => {
  it("follow GitHub: lower case, punctuation dropped, spaces as hyphens", () => {
    expect(slug("Back up and export")).toBe("back-up-and-export");
    expect(slug("End a run, rotate or revoke a credential")).toBe(
      "end-a-run-rotate-or-revoke-a-credential",
    );
    expect(slug("Plan & progress")).toBe("plan--progress");
    expect(slug("The day’s work")).toBe("the-days-work");
    expect(slug("snake_case and 2 numbers")).toBe("snake_case-and-2-numbers");
    expect(slug("5.1 Guide and identity")).toBe("51-guide-and-identity");
  });

  it("number a repeated heading", () => {
    const next = slugger();
    expect(["Notes", "Notes", "Notes"].map(next)).toEqual([
      "notes",
      "notes-1",
      "notes-2",
    ]);
  });
});
