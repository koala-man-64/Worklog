import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { slugger } from "../guideAnchors";
const ALLOWED_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);
function resolveLink(url: string): URL | null {
  try {
    const resolved = new URL(url, window.location.origin);
    return ALLOWED_PROTOCOLS.has(resolved.protocol) ? resolved : null;
  } catch {
    return null;
  }
}
function isSameOrigin(url: string): boolean {
  return resolveLink(url)?.origin === window.location.origin;
}
/** The parts of a Markdown syntax tree node that `anchorHeadings` reads and sets. */
type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
  data?: { hProperties?: Record<string, unknown> };
};
function visit(node: MarkdownNode, each: (node: MarkdownNode) => void) {
  each(node);
  for (const child of node.children ?? []) visit(child, each);
}
/** A heading's text as the page shows it; raw HTML is left out, as the page leaves it out. */
function plain(node: MarkdownNode): string {
  return node.type === "html"
    ? ""
    : (node.value ?? (node.children ?? []).map(plain).join(""));
}
/**
 * A remark plugin that takes headings from the parsed text, so underlined headings count
 * and lines in any code block never do. Each heading gets the id `<anchors>-<slug>`, a
 * repeated one numbered as GitHub numbers it, and `#slug` links and link definitions
 * become routes to them (`#<anchors>/<slug>`), since the whole hash is the page route.
 */
function anchorHeadings(anchors: string) {
  return () => (tree: MarkdownNode) => {
    const next = slugger();
    const slugs = new Set<string>();
    visit(tree, (node) => {
      if (node.type !== "heading") return;
      const slug = next(plain(node));
      if (!slug) return;
      slugs.add(slug);
      node.data = {
        ...node.data,
        hProperties: { ...node.data?.hProperties, id: `${anchors}-${slug}` },
      };
    });
    // A target may be written percent-encoded, so a heading "Café" is also #caf%C3%A9.
    const own = (target: string) => {
      try {
        return slugs.has(decodeURIComponent(target));
      } catch {
        return false;
      }
    };
    visit(tree, (node) => {
      if (
        (node.type === "link" || node.type === "definition") &&
        node.url?.startsWith("#") &&
        own(node.url.slice(1))
      )
        node.url = `#${anchors}/${node.url.slice(1)}`;
    });
  };
}
/**
 * `untrusted` is for text written elsewhere, such as an agent transcript: its links
 * may lead off this site but never to a page or download of Worklog itself.
 *
 * `anchors` names the page the text fills, such as `guide`: its headings get ids
 * (`guide-<slug>`) and its `#slug` links become routes to them (`#guide/<slug>`).
 * Other `#` links, such as `#work/<id>`, already name a route and stay as written.
 */
export function Markdown({
  text,
  untrusted = false,
  anchors,
}: {
  text: string;
  untrusted?: boolean;
  anchors?: string;
}) {
  const plugins = useMemo(
    () => (anchors ? [remarkGfm, anchorHeadings(anchors)] : [remarkGfm]),
    [anchors],
  );
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={plugins}
        skipHtml
        components={{
          img: ({ alt }) => (
            <span className="image-reference">
              [Image: {alt || "image reference"}]
            </span>
          ),
          a: ({ href, title, children }) => {
            const external = href ? !isSameOrigin(href) : false;
            return (
              <a
                href={href || undefined}
                title={title}
                target={external ? "_blank" : undefined}
                rel={external ? "noopener noreferrer" : undefined}
              >
                {children}
              </a>
            );
          },
        }}
        urlTransform={(url) =>
          resolveLink(url) && !(untrusted && isSameOrigin(url)) ? url : ""
        }
      >
        {text || "Nothing written yet."}
      </ReactMarkdown>
    </div>
  );
}
