import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Markdown } from "./Markdown";
it("renders GFM without executing imported HTML or unsafe links", () => {
  render(
    <Markdown
      text={
        "# Heading\n\n- [x] Finished\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))"
      }
    />,
  );
  expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText("bad")).not.toHaveAttribute(
    "href",
    "javascript:alert(1)",
  );
});
it("renders image references as alt text and isolates external links", () => {
  render(
    <Markdown
      text={
        "![Evidence diagram](https://tracker.example/image.png)\n\n[Reference](https://example.com)\n\n[Local](#work)"
      }
    />,
  );
  expect(document.querySelector("img")).toBeNull();
  expect(screen.getByText("[Image: Evidence diagram]")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Reference" })).toHaveAttribute(
    "target",
    "_blank",
  );
  expect(screen.getByRole("link", { name: "Reference" })).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );
  expect(screen.getByRole("link", { name: "Local" })).not.toHaveAttribute(
    "target",
  );
});
// [markdown destination, href rendered (null = dropped), target]. The Markdown
// parser percent-encodes a backslash, so `/\evil.example` reaches the renderer as the
// same-origin path `/%5Cevil.example`.
it.each([
  ["//evil.example", "//evil.example", "_blank"],
  ["/\\evil.example", "/%5Cevil.example", undefined],
  ["JaVaScRiPt:alert(1)", null, undefined],
  ["#today", "#today", undefined],
  ["/", "/", undefined],
  ["mailto:a@b.c", "mailto:a@b.c", "_blank"],
  ["https://example.com", "https://example.com", "_blank"],
] as const)(
  "renders the link %j as href %j with target %j",
  (destination, href, target) => {
    render(<Markdown text={`[probe](${destination})`} />);
    const link = screen.getByText("probe").closest("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href") || null).toBe(href);
    if (target) {
      expect(link).toHaveAttribute("target", target);
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    } else {
      expect(link).not.toHaveAttribute("target");
    }
  },
);
