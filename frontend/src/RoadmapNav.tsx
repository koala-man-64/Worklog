import { useState } from "react";
import { contextStorageKey } from "./api";
import { useCollapsed, useData } from "./hooks";
import { Icon } from "./icons";
import type { List, Roadmap } from "./types";

/**
 * Groups roadmaps under their parents, keeping list order. A roadmap whose parent
 * is not in the list (archived, or filtered out) is shown at the top level.
 */
export function roadmapChildren<
  T extends { id: string; parentId?: string | null },
>(items: T[]) {
  const ids = new Set(items.map((r) => r.id));
  const children = new Map<string | null, T[]>();
  for (const r of items) {
    const parent = r.parentId && ids.has(r.parentId) ? r.parentId : null;
    children.set(parent, [...(children.get(parent) ?? []), r]);
  }
  return children;
}

// Shares the Roadmaps list query, so renames and archives refresh the nav too.
export function RoadmapNav({
  selected,
  open = true,
}: {
  selected?: string;
  open?: boolean;
}) {
  const roads = useData<List<Roadmap>>("/roadmaps?archived=false");
  const { collapsed, toggle, reveal } = useCollapsed(
    contextStorageKey("roadmaps-collapsed", "nav"),
  );
  const [revealed, setRevealed] = useState<string>();
  const items = roads.data?.items ?? [];
  // A roadmap opened from elsewhere shows in the tree: expand its collapsed ancestors once.
  if (items.length && selected !== revealed) {
    setRevealed(selected);
    const parents = new Map(items.map((r) => [r.id, r.parentId]));
    const ancestors: string[] = [];
    let parent = selected && parents.get(selected);
    while (parent && parents.has(parent) && !ancestors.includes(parent)) {
      ancestors.push(parent);
      parent = parents.get(parent);
    }
    if (ancestors.some((a) => collapsed.has(a))) reveal(ancestors);
  }
  if (items.length === 0) return null;
  const children = roadmapChildren(items);
  const render = (parent: string | null): React.ReactNode =>
    children.get(parent)?.map((r) => {
      const title = r.title || "Untitled";
      const closed = collapsed.has(r.id);
      const link = (
        <a
          href={"#roadmaps/" + r.id}
          title={title}
          aria-current={selected === r.id ? "page" : undefined}
        >
          {title}
        </a>
      );
      return (
        <li key={r.id}>
          {children.has(r.id) ? (
            <>
              <div className="nav-row">
                {link}
                <button
                  type="button"
                  className="nav-toggle"
                  aria-expanded={!closed}
                  aria-label={(closed ? "Expand " : "Collapse ") + title}
                  onClick={() => toggle(r.id)}
                >
                  <Icon name={closed ? "expand" : "collapse"} size={14} />
                </button>
              </div>
              {!closed && <ul>{render(r.id)}</ul>}
            </>
          ) : (
            link
          )}
        </li>
      );
    });
  return (
    <ul
      className="subnav"
      id="roadmap-subnav"
      aria-label="Roadmaps"
      hidden={!open}
    >
      {render(null)}
    </ul>
  );
}
