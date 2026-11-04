import { useEffect } from "react";
import guide from "../../docs/GUIDE.md?raw";
import { Markdown } from "./components";
import { headings, type Heading } from "./guideAnchors";

// docs/GUIDE.md is bundled at build time, so the guide needs no request and works offline.
// The page's own heading names it, so its Markdown title line is left out.
const text = guide.replace(/^# .*(\r?\n)+/, "");
const sections: { heading: Heading; children: Heading[] }[] = [];
for (const heading of headings(text)) {
  if (heading.level === 2) sections.push({ heading, children: [] });
  else if (heading.level === 3) sections.at(-1)?.children.push(heading);
}
function show(slug: string) {
  document.getElementById("guide-" + slug)?.scrollIntoView({ block: "start" });
}
/** The person's guide to Worklog, beside a list of its sections; `#guide/<slug>` opens one. */
export function Guide({ selected }: { selected?: string }) {
  useEffect(() => {
    if (selected) show(selected);
  }, [selected]);
  const entry = (heading: Heading) => (
    <a
      href={"#guide/" + heading.slug}
      aria-current={heading.slug === selected ? "location" : undefined}
      onClick={() => {
        // Choosing the open section again changes no hash, so scroll here.
        if (heading.slug === selected) show(heading.slug);
      }}
    >
      {heading.text}
    </a>
  );
  return (
    <div className="bounded-screen">
      <header>
        <h1>Guide</h1>
        <p className="lede">
          How Worklog works and how to use each page. Agents read their own
          guide with <code>worklog agent guide</code>.
        </p>
      </header>
      <div className="split guide-split">
        <nav className="panel" aria-label="Contents">
          <div className="panel-head">
            <h2>Contents</h2>
          </div>
          <div className="panel-body">
            <ul className="guide-contents">
              {sections.map(({ heading, children }) => (
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
        <article className="pane detail guide" aria-label="Worklog guide">
          <Markdown text={text} anchors="guide" />
        </article>
      </div>
    </div>
  );
}
