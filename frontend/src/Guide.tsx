import { useEffect, useMemo, type ReactNode } from "react";
import guide from "../../docs/GUIDE.md?raw";
import agentGuide from "../../docs/AGENT-GUIDE.md?raw";
import { Markdown } from "./ui/Markdown";
import { headings, type Heading } from "./guideAnchors";

type Section = { heading: Heading; children: Heading[] };
type View = {
  /** `#<route>` opens the view and `#<route>/<slug>` one of its sections. */
  route: string;
  label: string;
  /** What its article is called. */
  name: string;
  lede: ReactNode;
  text: string;
  sections: Section[];
};

/** A guide and its sections. The page's own heading names it, so its Markdown title line is left out. */
function view(fields: Omit<View, "text" | "sections">, markdown: string): View {
  const text = markdown.replace(/^# .*(\r?\n)+/, "");
  const sections: Section[] = [];
  for (const heading of headings(text)) {
    if (heading.level === 2) sections.push({ heading, children: [] });
    else if (heading.level === 3) sections.at(-1)?.children.push(heading);
  }
  return { ...fields, text, sections };
}

// Both guides are bundled at build time, so the page needs no request and works offline.
const views: View[] = [
  view(
    {
      route: "guide",
      label: "For you",
      name: "Worklog guide",
      lede: "How Worklog works and how to use each page.",
    },
    guide,
  ),
  view(
    {
      route: "agent-guide",
      label: "For agents",
      name: "Agent guide",
      lede: (
        <>
          What AI agents read before they use Worklog, as{" "}
          <code>worklog agent guide</code> prints it: the rules they follow,
          every command, the proposal operations, every error and the limits.
        </>
      ),
    },
    agentGuide,
  ),
];

/** The routes the Guide page answers: the person's guide and the one agents read. */
export const guideRoutes = views.map(({ route }) => route);

function show(route: string, slug: string) {
  // The contents follow along, so the marked entry is in view; then the section is. The slug
  // comes from the address, so it is compared rather than put into a selector.
  [...document.querySelectorAll(".guide-contents a")]
    .find((entry) => entry.getAttribute("href") === `#${route}/${slug}`)
    ?.scrollIntoView({ block: "nearest" });
  document
    .getElementById(`${route}-${slug}`)
    ?.scrollIntoView({ block: "start" });
}
/**
 * Worklog's guides, each beside a list of its sections: the person's at `#guide`, and at
 * `#agent-guide` the one agents print with `worklog agent guide`.
 */
export function Guide({
  route = "guide",
  selected,
}: {
  route?: string;
  selected?: string;
}) {
  const open = views.find((candidate) => candidate.route === route) ?? views[0];
  useEffect(() => {
    if (selected) show(open.route, selected);
  }, [open, selected]);
  // The same element while only the section changes, so the long text isn't parsed again.
  const text = useMemo(
    () => <Markdown text={open.text} anchors={open.route} />,
    [open],
  );
  const entry = (heading: Heading) => (
    <a
      href={`#${open.route}/${heading.slug}`}
      aria-current={heading.slug === selected ? "location" : undefined}
      onClick={() => {
        // Choosing the open section again changes no hash, so scroll here.
        if (heading.slug === selected) show(open.route, heading.slug);
      }}
    >
      {heading.text}
    </a>
  );
  return (
    <div className="bounded-screen">
      <header>
        <h1>Guide</h1>
        <p className="lede">{open.lede}</p>
        <div
          className="status-toggles guide-views"
          role="group"
          aria-label="Show the guide"
        >
          {views.map((candidate) => (
            <a
              key={candidate.route}
              className="button"
              href={"#" + candidate.route}
              aria-current={candidate === open ? "page" : undefined}
            >
              {candidate.label}
            </a>
          ))}
        </div>
      </header>
      {/* Keyed by view, so the other guide opens at its top, contents too; the links above keep focus. */}
      <div className="split guide-split" key={open.route}>
        <nav className="panel" aria-label="Contents">
          <div className="panel-head">
            <h2>Contents</h2>
          </div>
          <div className="panel-body">
            <ul className="guide-contents">
              {open.sections.map(({ heading, children }) => (
                <li key={heading.slug}>
                  {entry(heading)}
                  {children.length > 0 && (
                    <ul>
                      {children.map((child) => (
                        <li key={child.slug}>{entry(child)}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </nav>
        <article className="pane detail guide" aria-label={open.name}>
          {text}
        </article>
      </div>
    </div>
  );
}
