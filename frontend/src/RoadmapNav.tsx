import { useData } from "./hooks";
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
  const items = roads.data?.items ?? [];
  if (items.length === 0) return null;
  const children = roadmapChildren(items);
  const render = (parent: string | null): React.ReactNode =>
    children.get(parent)?.map((r) => (
      <li key={r.id}>
        <a
          href={"#roadmaps/" + r.id}
          title={r.title || "Untitled"}
          aria-current={selected === r.id ? "page" : undefined}
        >
          {r.title || "Untitled"}
        </a>
        {children.has(r.id) && <ul>{render(r.id)}</ul>}
      </li>
    ));
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
