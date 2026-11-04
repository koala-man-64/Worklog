import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import guide from "../../docs/GUIDE.md?raw";
import { Guide } from "./Guide";
import { headings, slug, slugger } from "./guideAnchors";
import { destinations } from "./navigation";

const all = headings(guide);

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the guide's Markdown", () => {
  it("links only to its own headings, so every link works on GitHub and in Worklog", () => {
    const slugs = new Set(all.map((heading) => heading.slug));
    const links = [...guide.matchAll(/\]\(([^)\s]+)\)/g)].map(
      (match) => match[1],
    );
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link, "a file link would not open in Worklog").toMatch(
        /^#|^https:/,
      );
      if (link.startsWith("#")) expect(slugs, link).toContain(link.slice(1));
    }
  });

  it("describes every page in the sidebar under Pages", () => {
    const pages = all.findIndex((heading) => heading.text === "Pages");
    const end = all.findIndex((h, index) => index > pages && h.level <= 2);
    const described = all.slice(pages + 1, end).map((heading) => heading.text);
    for (const { label } of destinations) expect(described).toContain(label);
  });

  it("gives every heading its own anchor", () => {
    const slugs = all.map((heading) => heading.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
  });
});

describe("Guide page", () => {
  it("lists the guide's sections and gives each heading the anchor its entry opens", () => {
    render(<Guide />);
    const contents = screen.getByRole("navigation", { name: "Contents" });
    const entries = within(contents).getAllByRole("link");
    const sections = all.filter((h) => h.level === 2 || h.level === 3);
    expect(entries.map((entry) => entry.textContent)).toEqual(
      sections.map((h) => h.text),
    );
    for (const heading of sections) {
      const entry = within(contents).getByRole("link", { name: heading.text });
      expect(entry).toHaveAttribute("href", "#guide/" + heading.slug);
      const target = document.getElementById("guide-" + heading.slug);
      expect(target, heading.text).not.toBeNull();
      expect(target).toHaveTextContent(heading.text);
    }
  });

  it("leaves the guide's title to the page heading", () => {
    render(<Guide />);
    expect(
      screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent),
    ).toEqual(["Guide"]);
  });

  it("routes the guide's own links to its sections", () => {
    render(<Guide />);
    const text = screen.getByRole("article", { name: "Worklog guide" });
    expect(
      within(text).getAllByRole("link", { name: "Conflicts" })[0],
    ).toHaveAttribute("href", "#guide/conflicts");
  });

  it("scrolls to the section in the address and marks it in the contents", () => {
    // jsdom lays nothing out, so it has no scrollIntoView to spy on.
    const scroll = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scroll(this.id);
    };
    try {
      render(<Guide selected="back-up-and-export" />);
      expect(scroll).toHaveBeenCalledWith("guide-back-up-and-export");
      expect(
        screen.getByRole("link", { name: "Back up and export" }),
      ).toHaveAttribute("aria-current", "location");
    } finally {
      Element.prototype.scrollIntoView = original;
    }
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
